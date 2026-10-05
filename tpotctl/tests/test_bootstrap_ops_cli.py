"""tpotctl without a user interface: bootstrap, ops parsers, command line.

Run from the repository root: python3 -m unittest discover tpotctl/tests
"""

import hashlib
import json
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from tpotctl.tests import isolate  # noqa: E402

isolate()   # keeps the user's config out of the tests

from tpotctl import bootstrap, cli, ops  # noqa: E402

PS = [
    {"Names": "elasticsearch", "State": "running", "Status": "Up 2 hours (health: starting)",
     "Ports": "127.0.0.1:64298->9200/tcp", "Image": "es"},
    {"Names": "cowrie", "State": "running", "Status": "Up 2 hours (healthy)",
     "Ports": "0.0.0.0:22->22/tcp, [::]:22->22/tcp, 0.0.0.0:23->23/tcp, [::]:23->23/tcp", "Image": "cowrie"},
    {"Names": "conpot_ipmi", "State": "exited", "Status": "Exited (1) 3 minutes ago", "Ports": "", "Image": "c"},
    {"Names": "sentrypeer", "State": "running", "Status": "Up 1 hour (unhealthy)",
     "Ports": "0.0.0.0:5060->5060/tcp, 0.0.0.0:5060->5060/udp", "Image": "s"},
]


class BootstrapTest(unittest.TestCase):

    def test_venv_dir_follows_xdg(self):
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": "/x/data"}):
            self.assertEqual(bootstrap.venv_dir(), "/x/data/tpotce/venv")
        with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": "/x/cache"}):
            self.assertEqual(bootstrap.old_venv_dir(), "/x/cache/tpotce/customizer-venv")

    def test_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(bootstrap.marker_ok(tmp))
            with open(os.path.join(tmp, bootstrap.MARKER), "w") as handle:
                handle.write("nope\n")
            self.assertFalse(bootstrap.marker_ok(tmp))
            with open(os.path.join(tmp, bootstrap.MARKER), "w") as handle:
                handle.write(bootstrap.requirements_hash() + "\n")
            self.assertTrue(bootstrap.marker_ok(tmp))
        with open(bootstrap.REQUIREMENTS, "rb") as handle:
            self.assertEqual(bootstrap.requirements_hash(), hashlib.sha256(handle.read()).hexdigest())

    def test_requirements_are_pinned_with_hashes(self):
        with open(bootstrap.REQUIREMENTS, encoding="utf-8") as handle:
            text = handle.read()
        pins = re.findall(r"^([A-Za-z0-9_.-]+)==\S+", text, re.M)
        self.assertIn("textual", pins)
        self.assertIn("pyyaml", [p.lower() for p in pins])
        for block in re.split(r"\n(?=[A-Za-z])", text.split("\n", 5)[-1]):
            if re.match(r"^[A-Za-z0-9_.-]+==", block):
                self.assertIn("--hash=sha256:", block, block.split()[0])

    def test_ensure_yaml_uses_this_python(self):
        if not bootstrap.importable(["yaml"]):
            self.skipTest("this Python has no PyYAML, ensure() takes the venv then")
        self.assertIsNone(bootstrap.ensure("yaml"))

    def test_guard_stops_loops(self):
        with mock.patch.dict(os.environ, {bootstrap.GUARD: "1"}), \
                mock.patch.object(bootstrap, "importable", return_value=False):
            with self.assertRaises(bootstrap.BootstrapError):
                bootstrap.ensure("ui")

    def test_hints(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as handle:
            handle.write('ID=raspbian\nID_LIKE="debian"\n')
        try:
            self.assertEqual(bootstrap.os_release_ids(handle.name), ["raspbian", "debian"])
        finally:
            os.unlink(handle.name)
        self.assertIn("python3-venv", bootstrap.linux_hint(["ubuntu", "debian"]))
        self.assertIn("out of the box", bootstrap.linux_hint(["opensuse-tumbleweed", "suse"]))
        self.assertIn("out of the box", bootstrap.linux_hint(["rocky", "rhel", "fedora"]))


class OpsTest(unittest.TestCase):

    def test_parse_ps(self):
        containers = ops.parse_ps("\n".join(json.dumps(i) for i in PS))
        self.assertEqual([c.name for c in containers], ["conpot_ipmi", "cowrie", "elasticsearch", "sentrypeer"])
        by = {c.name: c for c in containers}
        self.assertEqual(by["cowrie"].health, "healthy")
        self.assertEqual(by["elasticsearch"].health, "starting")
        self.assertEqual(by["sentrypeer"].health, "unhealthy")
        self.assertEqual(by["conpot_ipmi"].health, "")
        self.assertEqual(by["cowrie"].ports, "22->22/tcp, 23->23/tcp")
        self.assertEqual(by["elasticsearch"].ports, "127.0.0.1:64298->9200/tcp")
        self.assertEqual(by["sentrypeer"].ports, "5060->5060/tcp, 5060->5060/udp")

    def test_parse_images(self):
        lines = [{"Repository": "ghcr.io/telekom-security/cowrie", "Tag": "24.04.2", "ID": "abc", "Size": "90MB",
                  "CreatedSince": "2 days ago"},
                 {"Repository": "alpine", "Tag": "3.24", "ID": "def", "Size": "8MB", "CreatedSince": "1 week ago"}]
        images = ops.parse_images("\n".join(json.dumps(i) for i in lines))
        self.assertEqual([i.ref for i in images], ["alpine:3.24", "ghcr.io/telekom-security/cowrie:24.04.2"])

    def test_restart_loops_are_shown(self):
        loop = {"Names": "p0f", "State": "restarting", "Status": "Restarting (1) 5 seconds ago", "Ports": "",
                "Image": "p0f"}
        containers = ops.parse_ps("\n".join(json.dumps(i) for i in PS + [loop]))
        self.assertIn("p0f", [c.name for c in containers])
        self.assertIn("1 restarting", cli.summary(containers))

    def test_containers_lists_all_states(self):
        seen = {}

        def fake(command, **_kwargs):
            seen["command"] = command
            return mock.Mock(returncode=0, stdout=json.dumps(PS[0]) + "\n", stderr="")
        with mock.patch.object(ops.shutil, "which", return_value="/usr/bin/docker"):
            self.assertEqual(len(ops.containers(run=fake)), 1)
        self.assertEqual(seen["command"][:3], ["docker", "ps", "--all"])

    def test_docker_error(self):
        fake = mock.Mock(return_value=mock.Mock(returncode=1, stdout="", stderr="permission denied"))
        with mock.patch.object(ops.shutil, "which", return_value="/usr/bin/docker"):
            with self.assertRaises(ops.OpsError):
                ops.containers(run=fake)

    def test_env_and_edition(self):
        with tempfile.TemporaryDirectory() as repo:
            with open(os.path.join(repo, ".env"), "w") as handle:
                handle.write("# comment\nTPOT_VERSION=24.04.2\nTPOT_TYPE=HIVE\n"
                             'GALAH_LLM_MODEL: "llama3.1"\nTPOT_DOCKER_COMPOSE=./docker-compose.yml\n')
            env = ops.env_values(repo)
            self.assertEqual((env["TPOT_VERSION"], env["GALAH_LLM_MODEL"]), ("24.04.2", "llama3.1"))
            self.assertEqual(ops.edition(repo), "none")
            with open(os.path.join(repo, "docker-compose.yml"), "w") as handle:
                handle.write("# T-Pot: SENSOR\nnetworks:\n")
            self.assertEqual(ops.edition(repo), "SENSOR")
            with open(os.path.join(repo, "docker-compose.yml"), "w") as handle:
                handle.write("# T-Pot: CUSTOM\n# customizer: version=2 base=MINI\n# customizer: add=\n")
            self.assertEqual(ops.edition(repo), "CUSTOM (from MINI)")

    def test_commands(self):
        self.assertEqual(ops.service_command("restart"), ["sudo", "systemctl", "restart", "tpot"])
        with self.assertRaises(ops.OpsError):
            ops.service_command("reload")
        self.assertEqual(ops.script_command("update.sh", ["-y"], "/r"), ["/r/update.sh", "-y"])
        with self.assertRaises(ops.OpsError):
            ops.script_command("install.sh", [])

    def test_backups_newest_first(self):
        with tempfile.TemporaryDirectory() as home:
            folder = os.path.join(home, "tpot_backups")
            os.makedirs(folder)
            for i, name in enumerate(["20260901_tpot_backup.tar", "20261001_tpot_backup_full.tar", "other.tar"]):
                path = os.path.join(folder, name)
                open(path, "w").close()
                os.utime(path, (1000 + i, 1000 + i))
            self.assertEqual([os.path.basename(p) for p in ops.backups(home)],
                             ["20261001_tpot_backup_full.tar", "20260901_tpot_backup.tar"])


class HostOstypeTest(unittest.TestCase):
    """ops.host_ostype: what tpotinit compares TPOT_OSTYPE with (entrypoint.sh, uname of the Docker kernel)."""

    def setUp(self):
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("TPOT_HOST_OSTYPE", None)

    @staticmethod
    def kernel(text, code=0):
        import subprocess
        return lambda command, **kwargs: subprocess.CompletedProcess(command, code, stdout=text, stderr="")

    def test_from_the_docker_kernel(self):
        with mock.patch("shutil.which", return_value="/usr/bin/docker"):
            self.assertEqual(ops.host_ostype(run=self.kernel("6.10.14-linuxkit\n")), "mac")
            self.assertEqual(ops.host_ostype(run=self.kernel("5.15.167.4-microsoft-standard-WSL2\n")), "win")
            self.assertEqual(ops.host_ostype(run=self.kernel("6.12.48+deb13-amd64\n")), "linux")

    def test_no_docker_falls_back_to_the_platform(self):
        with mock.patch("shutil.which", return_value=None):
            with mock.patch("sys.platform", "darwin"):
                self.assertEqual(ops.host_ostype(run=self.kernel("", 1)), "mac")
            with mock.patch("sys.platform", "linux"), mock.patch("platform.release", return_value="6.8.0-45-generic"):
                self.assertEqual(ops.host_ostype(run=self.kernel("", 1)), "linux")
            with mock.patch("sys.platform", "linux"), \
                    mock.patch("platform.release", return_value="5.15.167.4-microsoft-standard-WSL2"):
                self.assertEqual(ops.host_ostype(run=self.kernel("", 1)), "win")

    def test_docker_that_does_not_answer(self):
        import subprocess

        def hangs(command, **kwargs):
            raise subprocess.TimeoutExpired(command, kwargs.get("timeout"))
        with mock.patch("shutil.which", return_value="/usr/bin/docker"), mock.patch("sys.platform", "darwin"):
            self.assertEqual(ops.host_ostype(run=hangs), "mac")

    def test_the_environment_wins(self):
        os.environ["TPOT_HOST_OSTYPE"] = "win"
        self.assertEqual(ops.host_ostype(run=self.kernel("6.10.14-linuxkit\n")), "win")

    def test_tests_never_ask_docker(self):
        isolate()
        self.assertEqual(os.environ.get("TPOT_HOST_OSTYPE"), "linux")


class CliTest(unittest.TestCase):

    def setUp(self):
        self.patches = [mock.patch.object(cli.os, "geteuid", return_value=1000, create=True),
                        mock.patch.object(ops, "linux_host", return_value=True)]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in self.patches:
            patch.stop()

    def test_update_and_restore_pass_everything_on(self):
        for command, script in (("update", "update.sh"), ("restore", "restore.sh")):
            with mock.patch.object(cli.os, "execv") as execv, mock.patch.object(cli.os, "chdir") as chdir:
                cli.main([command, "-y", "-b", "dev", "--full", "-h"])
            path = os.path.join(cli.REPO_DIR, script)
            execv.assert_called_once_with(path, [path, "-y", "-b", "dev", "--full", "-h"])
            chdir.assert_called_once_with(cli.REPO_DIR)

    def test_customize_passes_everything_on(self):
        with mock.patch.object(cli.os, "execv") as execv:
            cli.main(["customize", "--base", "mini", "--add", "wordpot"])
        execv.assert_called_once_with(sys.executable, [sys.executable, cli.CUSTOMIZER, "--base", "mini",
                                                       "--add", "wordpot"])

    def test_refuses_root(self):
        with mock.patch.object(cli.os, "geteuid", return_value=0, create=True):
            self.assertEqual(cli.main(["status"]), 2)

    def test_needs_a_host(self):
        with mock.patch.object(ops, "linux_host", return_value=False):
            self.assertEqual(cli.main(["status"]), 1)
            self.assertEqual(cli.main(["update", "-y"]), 1)

    def test_service_actions(self):
        with mock.patch("subprocess.call", return_value=0) as call:
            self.assertEqual(cli.main(["restart"]), 0)
        call.assert_called_once_with(["sudo", "systemctl", "restart", "tpot"])

    def test_help_lists_every_command(self):
        text = cli.build_parser().format_help()
        for command in ("status", "ps", "images", "start", "stop", "restart", "update", "restore",
                        "customize", "setup"):
            self.assertIn(command, text)
        self.assertIn("was: dps", text)
        self.assertIn("was: dim", text)

    def test_watch_interval(self):
        parser = cli.build_parser()
        self.assertIsNone(parser.parse_args(["ps"]).watch)
        self.assertEqual(parser.parse_args(["ps", "--watch"]).watch, 2.0)
        self.assertEqual(parser.parse_args(["ps", "-w", "5"]).watch, 5.0)

    def test_no_menu_without_a_terminal(self):
        with mock.patch.object(cli.sys.stdin, "isatty", return_value=False), \
                mock.patch("sys.stdout", new_callable=lambda: open(os.devnull, "w")):
            self.assertEqual(cli.main([]), 2)


class SetupForceTest(unittest.TestCase):
    """setup_venv(force=True) in a temporary XDG_DATA_HOME; venv, pip and the import check are faked."""

    def setUp(self):
        import shutil
        import tempfile
        self.data = tempfile.mkdtemp(prefix="tpot-venv-")
        self.addCleanup(shutil.rmtree, self.data)
        patcher = mock.patch.dict(os.environ, {"XDG_DATA_HOME": self.data})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.venv = bootstrap.venv_dir()
        os.makedirs(os.path.join(self.venv, "bin"))
        with open(os.path.join(self.venv, "OLD"), "w") as out:
            out.write("old venv\n")

    def fake_call(self, pip_ok=True):
        def call(command, **kwargs):
            if command[1:3] == ["-m", "venv"]:
                python = bootstrap.venv_python(command[3])
                os.makedirs(os.path.dirname(python), exist_ok=True)
                open(python, "w").close()
                return 0
            return 0 if pip_ok else 1
        return call

    def test_rebuild_swaps_the_venv(self):
        with mock.patch("subprocess.call", side_effect=self.fake_call()), \
                mock.patch.object(bootstrap, "imports_ok", return_value=True):
            python = bootstrap.setup_venv(force=True, quiet=True)
        self.assertEqual(python, bootstrap.venv_python(self.venv))
        self.assertFalse(os.path.exists(os.path.join(self.venv, "OLD")))
        self.assertTrue(os.path.exists(os.path.join(self.venv, bootstrap.MARKER)))
        self.assertFalse(os.path.exists(self.venv + ".new"))
        self.assertFalse(os.path.exists(self.venv + ".old"))

    def test_failed_rebuild_keeps_the_old_venv(self):
        with mock.patch("subprocess.call", side_effect=self.fake_call(pip_ok=False)), \
                mock.patch.object(bootstrap, "imports_ok", return_value=True):
            with self.assertRaises(bootstrap.BootstrapError):
                bootstrap.setup_venv(force=True, quiet=True)
        self.assertTrue(os.path.exists(os.path.join(self.venv, "OLD")))
        self.assertFalse(os.path.exists(self.venv + ".new"))

    def test_leftovers_of_a_broken_run_go(self):
        os.makedirs(self.venv + ".new")
        os.makedirs(self.venv + ".old")
        with mock.patch("subprocess.call", side_effect=self.fake_call()), \
                mock.patch.object(bootstrap, "imports_ok", return_value=True):
            bootstrap.setup_venv(force=True, quiet=True)
        self.assertFalse(os.path.exists(self.venv + ".new"))
        self.assertFalse(os.path.exists(self.venv + ".old"))

    def test_a_plain_launch_leaves_a_rebuild_in_progress_alone(self):
        """tpot ps in another shell while the menu rebuilds: venv.new must stay."""
        os.makedirs(self.venv + ".new")
        with mock.patch.object(bootstrap, "venv_current", return_value=True):
            bootstrap.setup_venv(quiet=True)
        self.assertTrue(os.path.exists(self.venv + ".new"))

    def test_a_second_rebuild_waits_for_the_first(self):
        import threading
        order = []
        gate = threading.Event()
        calls = self.fake_call()

        def slow(command, **kwargs):
            if command[1:3] == ["-m", "venv"] and not order:
                order.append("first starts")
                gate.wait(2)
                order.append("first done")
            elif command[1:3] == ["-m", "venv"]:
                order.append("second starts")
            return calls(command, **kwargs)
        with mock.patch("subprocess.call", side_effect=slow), \
                mock.patch.object(bootstrap, "imports_ok", return_value=True):
            first = threading.Thread(target=bootstrap.setup_venv, kwargs={"force": True, "quiet": True})
            first.start()
            while not order:
                pass
            second = threading.Thread(target=bootstrap.setup_venv, kwargs={"force": True, "quiet": True})
            second.start()
            second.join(0.3)
            gate.set()
            first.join(5)
            second.join(5)
        self.assertEqual(order, ["first starts", "first done", "second starts"])
        self.assertTrue(os.path.exists(bootstrap.venv_python(self.venv)))

    def test_launcher_and_cli_know_force(self):
        from tpotctl import cli
        self.assertTrue(cli.build_parser().parse_args(["setup", "--force"]).force)
        with open(os.path.join(bootstrap.REPO_DIR, "tpot"), encoding="utf-8") as handle:
            self.assertTrue('"--force" in argv' in handle.read())


if __name__ == "__main__":
    unittest.main()
