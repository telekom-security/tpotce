"""tpotctl without a user interface: bootstrap, ops parsers, command line.

Run from the repository root: python3 -m unittest discover tpotctl/tests
"""

import hashlib
import io
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


class Child:
    """A child of subprocess.Popen: wait() takes the next step, an exit code or an exception to raise."""

    def __init__(self, command, steps):
        self.args, self.steps, self.returncode, self.waits = command, list(steps), None, 0

    def wait(self, timeout=None):
        self.waits += 1
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
        children.append(Child(command, steps))
        children[-1].kwargs = kwargs
        return children[-1]
    return popen, children


def main(argv):
    """cli.main, a KeyboardInterrupt it lets through as a result (unittest would stop on it)."""
    try:
        return cli.main(argv)
    except KeyboardInterrupt:
        return "KeyboardInterrupt"


class TTY(io.StringIO):
    """stdin of a person at a terminal."""

    def isatty(self):
        return True


class WaitChildTest(unittest.TestCase):

    def test_ctrl_c_waits_for_the_child_and_gives_128_plus_the_signal(self):
        child = Child(["x"], [KeyboardInterrupt, -2])
        self.assertEqual(bootstrap.wait_child(child), 130)
        self.assertEqual(child.waits, 2)

    def test_exit_codes_and_other_signals(self):
        for code, expected in ((0, 0), (3, 3), (130, 130), (-15, 143), (-9, 137)):
            self.assertEqual(bootstrap.wait_child(Child(["x"], [code])), expected)

    def test_a_double_ctrl_c_still_waits(self):
        child = Child(["x"], [KeyboardInterrupt, KeyboardInterrupt, 0])
        self.assertEqual(bootstrap.wait_child(child), 0)

    def test_it_remembers_a_ctrl_c_while_it_waited(self):
        """ansible-playbook or sudo catch the ^C and exit 99 / 1: the caller still learns that it came."""
        interrupts = []
        self.assertEqual(bootstrap.wait_child(Child(["x"], [KeyboardInterrupt, 99]), interrupts), 99)
        self.assertEqual(len(interrupts), 1)
        interrupts = []
        self.assertEqual(bootstrap.wait_child(Child(["x"], [3]), interrupts), 3)
        self.assertEqual(interrupts, [])

    def test_reexec_gives_130_for_ctrl_c_not_254(self):
        popen, children = fake_popen(KeyboardInterrupt, -2)
        with mock.patch("subprocess.Popen", side_effect=popen):
            self.assertEqual(bootstrap.reexec("/venv/bin/python3", "/x/tpot", ["ps"]), 130)
        self.assertEqual(children[0].args, ["/venv/bin/python3", "/x/tpot", "ps"])
        self.assertEqual(children[0].kwargs["env"][bootstrap.GUARD], "1")


class LauncherTest(unittest.TestCase):
    """The launcher itself, run as a script would be."""

    LAUNCHER = os.path.join(bootstrap.REPO_DIR, "tpot")

    def launch(self, *argv, platform="darwin"):
        import runpy
        err = io.StringIO()
        with mock.patch.object(sys, "argv", ["tpot"] + list(argv)), mock.patch.object(sys, "path", list(sys.path)), \
                mock.patch.dict(os.environ), mock.patch("sys.platform", platform), \
                mock.patch.object(os, "geteuid", return_value=1000, create=True), \
                mock.patch("sys.stderr", err), mock.patch("sys.stdout", io.StringIO()):
            try:
                runpy.run_path(self.LAUNCHER, run_name="__main__")
                code = 0
            except SystemExit as stop:
                code = stop.code
            except KeyboardInterrupt:
                code = "KeyboardInterrupt"
        return code, err.getvalue()

    def test_ctrl_c_in_setup_is_cancelled_with_130(self):
        with mock.patch.object(bootstrap, "setup_venv", side_effect=KeyboardInterrupt):
            code, err = self.launch("setup")
        self.assertEqual(code, 130, err)
        self.assertIn("Cancelled.", err)
        self.assertNotIn("Traceback", err)
        self.assertTrue(err.startswith("\n"), repr(err))          # ends the line of the ^C

    def test_ctrl_c_after_a_line_that_is_ended_adds_no_empty_line(self):
        """ensure_link ends the line of the ^C itself and says how to link later."""
        with mock.patch.dict(os.environ, {bootstrap.GUARD: ""}), \
                mock.patch.object(bootstrap, "ensure_link", side_effect=bootstrap.Interrupted), \
                mock.patch.object(bootstrap, "setup_venv", side_effect=AssertionError("must not build")):
            code, err = self.launch("setup", platform="linux")
        self.assertEqual(code, 130, err)
        self.assertIn("Cancelled.", err)
        self.assertNotIn("\n\n", err)

    def test_the_exit_code_of_the_venv_child_comes_through(self):
        with mock.patch.object(bootstrap, "ensure", return_value="/venv/bin/python3"), \
                mock.patch.object(bootstrap, "reexec", return_value=130):
            self.assertEqual(self.launch("ps")[0], 130)


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

    def test_the_version_comes_from_one_place(self):
        """The file version of the checkout, else TPOT_VERSION of its .env, else nothing."""
        with tempfile.TemporaryDirectory() as repo:
            self.assertEqual(ops.tpot_version(repo), "")
            with open(os.path.join(repo, ".env"), "w") as handle:
                handle.write("TPOT_VERSION=98.0.0\n")
            self.assertEqual(ops.tpot_version(repo), "98.0.0")
            with open(os.path.join(repo, "version"), "w") as handle:
                handle.write("99.1.0\n")
            self.assertEqual(ops.tpot_version(repo), "99.1.0")
            with open(os.path.join(repo, "version"), "w") as handle:
                handle.write("\n")                          # an empty file says nothing
            self.assertEqual(ops.tpot_version(repo), "98.0.0")
        with open(os.path.join(ops.REPO_DIR, "version"), encoding="utf-8") as handle:
            self.assertEqual(ops.tpot_version(), handle.read().strip())

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
        popen, children = fake_popen(0)
        with mock.patch("subprocess.Popen", side_effect=popen):
            self.assertEqual(cli.main(["restart"]), 0)
        self.assertEqual([c.args for c in children], [["sudo", "systemctl", "restart", "tpot"]])

    def test_ctrl_c_during_a_child_is_cancelled_with_130(self):
        """The child gets the ^C as well and ends; the T-Pot Manager waits for it, then says so."""
        popen, children = fake_popen(KeyboardInterrupt, -2)
        err = io.StringIO()
        with mock.patch("subprocess.Popen", side_effect=popen), mock.patch("sys.stderr", err):
            self.assertEqual(main(["restart"]), 130)
        self.assertEqual(children[0].waits, 2)
        self.assertIn("Cancelled.", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())

    def test_ctrl_c_that_the_child_survives_but_fails_on_is_cancelled(self):
        """sudo at its password prompt exits 1 on ^C (sudo's behaviour), ansible-playbook 99: the command
        was stopped all the same, 130 and Cancelled.; a child that ends well despite the ^C gives 0."""
        for steps, expected in (((KeyboardInterrupt, 1), 130), ((KeyboardInterrupt, 99), 130),
                                ((KeyboardInterrupt, 0), 0)):
            popen, _children = fake_popen(*steps)
            err = io.StringIO()
            with mock.patch("subprocess.Popen", side_effect=popen), mock.patch("sys.stderr", err):
                self.assertEqual(main(["restart"]), expected, steps)
            self.assertEqual("Cancelled." in err.getvalue(), expected == 130, err.getvalue())

    def test_a_child_ended_by_another_signal_gives_128_plus_it(self):
        popen, _children = fake_popen(-15)
        with mock.patch("subprocess.Popen", side_effect=popen):
            self.assertEqual(main(["stop"]), 143)

    def test_ctrl_c_anywhere_in_main_is_cancelled_with_130(self):
        err = io.StringIO()
        with mock.patch.object(cli, "print_status", side_effect=KeyboardInterrupt), mock.patch("sys.stderr", err):
            self.assertEqual(main(["status"]), 130)
        self.assertTrue(err.getvalue().startswith("\n"), repr(err.getvalue()))
        self.assertIn("Cancelled.", err.getvalue())

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


class ConsoleColoursTest(unittest.TestCase):
    """The tables of tpot status / ps / images follow the colour rule of the scripts (prefs.detect_colors)
    at a terminal, i.e. true colour for iTerm2 over SSH (LC_TERMINAL), and have no escapes in a pipe."""

    TERMINAL = ("COLORTERM", "TERM", "TERM_PROGRAM", "LC_TERMINAL", "TMUX", "VTE_VERSION", "KONSOLE_VERSION",
                "WT_SESSION", "TPOT_COLORS", "NO_COLOR", "FORCE_COLOR", "TTY_COMPATIBLE", "TTY_INTERACTIVE")

    class Tty(io.StringIO):
        def isatty(self):
            return True

    def setUp(self):
        try:
            import rich  # noqa: F401
        except ImportError:
            self.skipTest("Rich is not installed, run with the venv of tpot")

    def console(self, env, stream):
        environ = {k: v for k, v in os.environ.items() if k not in self.TERMINAL}
        environ.update(env)
        with mock.patch.dict(os.environ, environ, clear=True), mock.patch("sys.stdout", stream):
            console = cli._console()
            console.print("[bold red]x[/]")
        return console, stream.getvalue()

    def test_a_terminal_gets_the_colours_of_the_rule(self):
        cases = [({"TERM": "xterm-256color", "LC_TERMINAL": "iTerm2"}, "truecolor"),     # iTerm2 over SSH
                 ({"TERM": "xterm-256color"}, "256"),
                 ({"TERM": "xterm"}, "standard"),                                         # PuTTY
                 ({"TERM": "xterm-256color", "TPOT_COLORS": "16"}, "standard")]
        for env, expected in cases:
            with self.subTest(env=env):
                console, _out = self.console(env, self.Tty())
                self.assertEqual(console.color_system, expected)

    def test_a_pipe_gets_no_escapes(self):
        console, out = self.console({"TERM": "xterm-256color", "LC_TERMINAL": "iTerm2"}, io.StringIO())
        self.assertNotIn("\x1b[", out)
        self.assertIsNone(console.color_system)


class SetupVenvInstallTest(unittest.TestCase):
    """A Python without its venv module (Debian, Ubuntu, Raspberry Pi OS): tpot asks, then installs it."""

    ENSUREPIP = ("The virtual environment was not created successfully because ensurepip is not\n"
                 "available.  On Debian/Ubuntu systems, you need to install the python3-venv\n"
                 "package using the following command.\n\n    apt install python3.13-venv\n\n"
                 "You may need to use sudo with that command.  After installing the python3-venv\n"
                 "package, recreate your virtual environment.\n")

    def setUp(self):
        import shutil
        import tempfile
        self.data = tempfile.mkdtemp(prefix="tpot-venv-")
        self.addCleanup(shutil.rmtree, self.data)
        self.calls, self.installed = [], False
        for patcher in (mock.patch.dict(os.environ, {"XDG_DATA_HOME": self.data}),
                        mock.patch("sys.platform", "linux"),
                        mock.patch.object(bootstrap.os, "geteuid", return_value=1000, create=True),
                        mock.patch.object(bootstrap, "imports_ok", return_value=True),
                        mock.patch.object(bootstrap, "apt_get", return_value="/usr/bin/apt-get"),
                        mock.patch.object(bootstrap, "run_child", side_effect=self.call)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def call(self, command, **kwargs):
        self.calls.append(command)
        if command[1:3] == ["-m", "venv"]:
            if not self.installed:
                if kwargs.get("stdout") not in (None, bootstrap.subprocess.DEVNULL):
                    kwargs["stdout"].write(self.ENSUREPIP)
                os.makedirs(command[3], exist_ok=True)          # venv leaves a half one behind
                return 1
            python = bootstrap.venv_python(command[3])
            os.makedirs(os.path.dirname(python), exist_ok=True)
            open(python, "w").close()
            return 0
        if command[:3] == ["sudo", "apt-get", "install"]:
            self.installed = self.apt_ok
            return 0 if self.apt_ok else 100
        return 0

    def setup(self, ids=("debian",), answer="", tty=True, apt_ok=True):
        self.apt_ok = apt_ok
        asked = []

        def ask(prompt):
            asked.append(prompt)
            return answer
        with mock.patch.object(bootstrap, "os_release_ids", return_value=list(ids)), \
                mock.patch.object(bootstrap, "interactive", return_value=tty), \
                mock.patch.object(bootstrap, "ask", side_effect=ask), \
                mock.patch("sys.stderr", new_callable=io.StringIO):
            try:
                return bootstrap.setup_venv(quiet=True), asked
            except bootstrap.BootstrapError as err:
                return err, asked

    def apt_calls(self):
        return [c for c in self.calls if c[:1] == ["sudo"]]

    def test_yes_installs_the_package_and_builds_on(self):
        python, asked = self.setup(answer="")
        self.assertEqual(python, bootstrap.venv_python(bootstrap.venv_dir()))
        self.assertEqual(len(asked), 1)
        self.assertTrue("sudo apt-get" in asked[0], asked)
        self.assertEqual(self.apt_calls(), [["sudo", "apt-get", "install", "-y", "python3.13-venv"]])
        self.assertEqual(len([c for c in self.calls if c[1:3] == ["-m", "venv"]]), 2)

    def test_no_keeps_the_hint(self):
        err, asked = self.setup(answer="n")
        self.assertIsInstance(err, bootstrap.BootstrapError)
        self.assertEqual(len(asked), 1)
        self.assertEqual(self.apt_calls(), [])
        self.assertTrue("sudo apt install python3.13-venv" in str(err), str(err))
        self.assertFalse(os.path.exists(bootstrap.venv_dir() + ".new"))

    def test_without_a_terminal_no_question(self):
        err, asked = self.setup(tty=False)
        self.assertIsInstance(err, bootstrap.BootstrapError)
        self.assertEqual((asked, self.apt_calls()), ([], []))
        self.assertTrue("python3.13-venv" in str(err), str(err))

    def test_ctrl_c_at_the_question_says_later_and_goes_on(self):
        err = io.StringIO()
        with mock.patch.object(bootstrap, "os_release_ids", return_value=["debian"]), \
                mock.patch.object(bootstrap, "interactive", return_value=True), \
                mock.patch.object(bootstrap, "ask", side_effect=KeyboardInterrupt), \
                mock.patch("sys.stderr", err), self.assertRaises(bootstrap.Interrupted):
            bootstrap.setup_venv(quiet=True)
        self.assertIn("Later with: sudo apt-get install python3.13-venv", err.getvalue())
        self.assertEqual(self.apt_calls(), [])
        self.assertFalse(os.path.exists(bootstrap.venv_dir() + ".new"))

    def test_a_failed_apt_get_says_so(self):
        err, _asked = self.setup(apt_ok=False)
        self.assertIsInstance(err, bootstrap.BootstrapError)
        self.assertTrue("apt-get" in str(err), str(err))

    def test_other_distributions_get_the_output_of_venv(self):
        err, asked = self.setup(ids=("fedora",))
        self.assertIsInstance(err, bootstrap.BootstrapError)
        self.assertEqual((asked, self.apt_calls()), ([], []))
        self.assertTrue("ensurepip is not" in str(err), str(err))

    def test_the_package_fits_the_python(self):
        self.assertEqual(bootstrap.venv_package(self.ENSUREPIP, "/usr/bin/python3"), "python3.13-venv")
        self.assertEqual(bootstrap.venv_package("", "/usr/bin/python3"), "python3-venv")
        minor = sys.version_info.minor
        self.assertEqual(bootstrap.venv_package("", "/usr/local/bin/python3.12"), f"python3.{minor}-venv")


class LinkTest(unittest.TestCase):
    """tpot started as ./tpot from ~/tpotce offers the link /usr/local/bin/tpot, once."""

    def setUp(self):
        import shutil
        self.root = tempfile.mkdtemp(prefix="tpot-link-")
        self.addCleanup(shutil.rmtree, self.root)
        self.checkout = os.path.join(self.root, "home", "tpotce")
        os.makedirs(self.checkout)
        self.launcher = os.path.join(self.checkout, "tpot")
        open(self.launcher, "w").close()
        self.link = os.path.join(self.root, "bin", "tpot")
        os.makedirs(os.path.dirname(self.link))
        self.calls = []
        for patcher in (mock.patch.dict(os.environ, {"XDG_DATA_HOME": os.path.join(self.root, "data")}),
                        mock.patch("sys.platform", "linux"),
                        mock.patch.object(bootstrap.os, "geteuid", return_value=1000, create=True),
                        mock.patch.object(bootstrap, "run_child", side_effect=self.call)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def call(self, command, **kwargs):
        self.calls.append(command)
        if command[:3] == ["sudo", "ln", "-sfn"]:
            if os.path.lexists(command[4]):
                os.unlink(command[4])
            os.symlink(command[3], command[4])
        return 0

    def ensure(self, answer="", tty=True, checkout=None):
        asked = []

        def ask(prompt):
            asked.append(prompt)
            return answer
        with mock.patch.object(bootstrap, "interactive", return_value=tty), \
                mock.patch.object(bootstrap, "ask", side_effect=ask), \
                mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            bootstrap.ensure_link(self.launcher, self.link, checkout or self.checkout)
        return asked, err.getvalue()

    def test_missing_link_is_offered_and_made(self):
        asked, _err = self.ensure()
        self.assertEqual(len(asked), 1)
        self.assertEqual(self.calls, [["sudo", "ln", "-sfn", self.launcher, self.link]])
        self.assertEqual(os.readlink(self.link), self.launcher)

    def test_a_link_to_this_launcher_is_left_as_it_is(self):
        os.symlink(self.launcher, self.link)
        self.assertEqual(self.ensure(), ([], ""))
        self.assertEqual(self.calls, [])

    def test_a_dead_link_is_offered_again(self):
        os.symlink(os.path.join(self.root, "gone", "tpot"), self.link)
        asked, _err = self.ensure()
        self.assertEqual(len(asked), 1)
        self.assertEqual(os.readlink(self.link), self.launcher)

    def test_a_link_to_another_launcher_defaults_to_no(self):
        """A host with a second user: their T-Pot Manager stays linked unless this one says yes."""
        other = os.path.join(self.root, "other", "tpotce", "tpot")
        os.makedirs(os.path.dirname(other))
        open(other, "w").close()
        os.symlink(other, self.link)
        asked, _err = self.ensure(answer="")
        self.assertEqual(self.calls, [])
        self.assertTrue(other in asked[0] and self.launcher in asked[0] and "[y/N]" in asked[0], asked)
        os.unlink(os.path.join(os.path.dirname(bootstrap.venv_dir()), "link-asked"))
        self.ensure(answer="y")
        self.assertEqual(os.readlink(self.link), self.launcher)

    def test_interactive_is_not_faked_here(self):
        """The real condition: stdin and stderr at a terminal."""
        tty = mock.Mock(isatty=lambda: True)
        asked = []
        with mock.patch("sys.stdin", tty), mock.patch("sys.stderr", mock.Mock(isatty=lambda: False)), \
                mock.patch.object(bootstrap, "ask", side_effect=lambda p: asked.append(p) or ""):
            bootstrap.ensure_link(self.launcher, self.link, self.checkout)
        self.assertEqual((asked, self.calls), ([], []))
        err = io.StringIO()
        err.isatty = lambda: True
        with mock.patch("sys.stdin", tty), mock.patch("sys.stderr", err), \
                mock.patch.object(bootstrap, "ask", side_effect=lambda p: asked.append(p) or ""):
            bootstrap.ensure_link(self.launcher, self.link, self.checkout)
        self.assertEqual(len(asked), 1)

    def test_a_file_of_its_own_stays(self):
        with open(self.link, "w") as handle:
            handle.write("#!/bin/sh\n")
        asked, err = self.ensure()
        self.assertEqual((asked, self.calls), ([], []))
        self.assertTrue("leaves it alone" in err, err)

    def test_without_a_terminal_nothing_and_later_it_asks(self):
        """tpot setup from the playbook or update.sh: no question, no note, so ./tpot asks later."""
        self.assertEqual(self.ensure(tty=False), ([], ""))
        self.assertEqual(self.calls, [])
        asked, _err = self.ensure()
        self.assertEqual(len(asked), 1)

    def test_the_question_goes_to_the_terminal_not_to_stdout(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("sys.stdin", io.StringIO("n\n")), mock.patch("sys.stdout", out), \
                mock.patch("sys.stderr", err):
            self.assertEqual(bootstrap.ask("Link it? [Y/n] "), "n\n")
        self.assertEqual((out.getvalue(), err.getvalue()), ("", "Link it? [Y/n] "))
        with mock.patch("sys.stdin", io.StringIO("")), self.assertRaises(EOFError):
            bootstrap.ask("again? ")

    def test_interactive_needs_stdin_and_stderr_at_a_terminal(self):
        tty, pipe = mock.Mock(isatty=lambda: True), mock.Mock(isatty=lambda: False)
        for stdin, stderr, expected in ((tty, tty, True), (tty, pipe, False), (pipe, tty, False)):
            with mock.patch("sys.stdin", stdin), mock.patch("sys.stderr", stderr):
                self.assertEqual(bootstrap.interactive(), expected)

    def test_asked_once(self):
        self.ensure(answer="n")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.ensure(), ([], ""))

    def test_ctrl_c_at_the_question_says_later_and_goes_on(self):
        """Ctrl+C is an answer as well: the command for later, the interrupt on to the launcher, not asked again."""
        err = io.StringIO()
        with mock.patch.object(bootstrap, "interactive", return_value=True), \
                mock.patch.object(bootstrap, "ask", side_effect=KeyboardInterrupt), \
                mock.patch("sys.stderr", err), self.assertRaises(KeyboardInterrupt) as caught:
            bootstrap.ensure_link(self.launcher, self.link, self.checkout)
        self.assertIsInstance(caught.exception, bootstrap.Interrupted)       # the line of the ^C is ended
        self.assertTrue(err.getvalue().startswith("\n"), repr(err.getvalue()))
        self.assertIn(f"Later with: sudo ln -sfn {self.launcher} {self.link}", err.getvalue())
        self.assertEqual(self.calls, [])
        self.assertEqual(self.ensure(), ([], ""))

    def test_the_launcher_offers_it(self):
        with open(os.path.join(os.path.dirname(cli.__file__), os.pardir, "tpot"), encoding="utf-8") as handle:
            launcher = handle.read()
        self.assertTrue('argv[:1] in ([], ["setup"])' in launcher and "bootstrap.ensure_link(" in launcher)
        with open(os.path.join(os.path.dirname(cli.__file__), os.pardir, "install.sh"), encoding="utf-8") as handle:
            self.assertTrue("myTPOT_FOUND=$(command -v tpot)" in handle.read())

    def test_nothing_on_a_mac_or_from_another_checkout(self):
        with mock.patch("sys.platform", "darwin"):
            self.assertEqual(self.ensure(), ([], ""))
        self.assertEqual(self.ensure(checkout=os.path.join(self.root, "elsewhere")), ([], ""))
        self.assertEqual(self.calls, [])


class ForegroundChildTest(unittest.TestCase):
    """sudo apt-get (the venv package) and sudo ln (the link) are children in the foreground: a ^C reaches
    them from the terminal and they decide, tpot does not kill sudo halfway (subprocess.call would)."""

    class Child(Child):
        killed = False

        def kill(self):
            self.killed = True
            super().kill()

    def popen(self, *steps):
        children = []

        def popen(command, **kwargs):
            children.append(self.Child(command, steps))
            return children[-1]
        return popen, children

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tpot-child-")
        import shutil
        self.addCleanup(shutil.rmtree, self.root)
        for patcher in (mock.patch.dict(os.environ, {"XDG_DATA_HOME": os.path.join(self.root, "data")}),
                        mock.patch("sys.platform", "linux"),
                        mock.patch.object(bootstrap.os, "geteuid", return_value=1000, create=True),
                        mock.patch.object(bootstrap, "os_release_ids", return_value=["debian"]),
                        mock.patch.object(bootstrap, "apt_get", return_value="/usr/bin/apt-get"),
                        mock.patch.object(bootstrap, "interactive", return_value=True),
                        mock.patch.object(bootstrap, "ask", return_value="")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_ctrl_c_during_apt_get_waits_for_sudo(self):
        popen, children = self.popen(KeyboardInterrupt, 0)
        with mock.patch("subprocess.Popen", side_effect=popen), mock.patch("sys.stderr", new_callable=io.StringIO):
            self.assertTrue(bootstrap.offer_venv_package("python3-venv"))
        self.assertEqual(children[0].args, ["sudo", "apt-get", "install", "-y", "python3-venv"])
        self.assertFalse(children[0].killed)
        self.assertEqual(children[0].waits, 2)

    def test_ctrl_c_that_ends_apt_get_cancels(self):
        for steps in ((KeyboardInterrupt, -2), (KeyboardInterrupt, 1)):     # apt-get by the ^C, sudo at its prompt
            popen, children = self.popen(*steps)
            with mock.patch("subprocess.Popen", side_effect=popen), \
                    mock.patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(KeyboardInterrupt):
                bootstrap.offer_venv_package("python3-venv")
            self.assertFalse(children[0].killed)

    def test_ctrl_c_at_sudo_ln_says_later(self):
        checkout = os.path.join(self.root, "home", "tpotce")
        os.makedirs(checkout)
        launcher, link = os.path.join(checkout, "tpot"), os.path.join(self.root, "tpot-link")
        open(launcher, "w").close()
        popen, children = self.popen(KeyboardInterrupt, 1)
        err = io.StringIO()
        with mock.patch("subprocess.Popen", side_effect=popen), mock.patch("sys.stderr", err), \
                self.assertRaises(bootstrap.Interrupted):
            bootstrap.ensure_link(launcher, link, checkout)
        self.assertEqual(children[0].args, ["sudo", "ln", "-sfn", launcher, link])
        self.assertFalse(children[0].killed)
        self.assertIn(f"Later with: sudo ln -sfn {launcher} {link}", err.getvalue())


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
        with mock.patch.object(bootstrap, "run_child", side_effect=self.fake_call()), \
                mock.patch.object(bootstrap, "imports_ok", return_value=True):
            python = bootstrap.setup_venv(force=True, quiet=True)
        self.assertEqual(python, bootstrap.venv_python(self.venv))
        self.assertFalse(os.path.exists(os.path.join(self.venv, "OLD")))
        self.assertTrue(os.path.exists(os.path.join(self.venv, bootstrap.MARKER)))
        self.assertFalse(os.path.exists(self.venv + ".new"))
        self.assertFalse(os.path.exists(self.venv + ".old"))

    def test_failed_rebuild_keeps_the_old_venv(self):
        with mock.patch.object(bootstrap, "run_child", side_effect=self.fake_call(pip_ok=False)), \
                mock.patch.object(bootstrap, "imports_ok", return_value=True):
            with self.assertRaises(bootstrap.BootstrapError):
                bootstrap.setup_venv(force=True, quiet=True)
        self.assertTrue(os.path.exists(os.path.join(self.venv, "OLD")))
        self.assertFalse(os.path.exists(self.venv + ".new"))

    def test_leftovers_of_a_broken_run_go(self):
        os.makedirs(self.venv + ".new")
        os.makedirs(self.venv + ".old")
        with mock.patch.object(bootstrap, "run_child", side_effect=self.fake_call()), \
                mock.patch.object(bootstrap, "imports_ok", return_value=True):
            bootstrap.setup_venv(force=True, quiet=True)
        self.assertFalse(os.path.exists(self.venv + ".new"))
        self.assertFalse(os.path.exists(self.venv + ".old"))

    def test_a_plain_launch_leaves_a_rebuild_in_progress_alone(self):
        """tpot ps in another shell while the menu rebuilds (it holds the lock): venv.new must stay."""
        try:
            import fcntl
        except ImportError:
            self.skipTest("no fcntl")
        os.makedirs(self.venv + ".new")
        with open(self.venv + ".lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with mock.patch.object(bootstrap, "venv_current", return_value=True):
                bootstrap.setup_venv(quiet=True)
        self.assertTrue(os.path.exists(self.venv + ".new"))

    def test_a_plain_launch_clears_what_a_stopped_rebuild_left(self):
        """tpot setup --force stopped with ^C leaves venv.new (tens of MB): the next start clears it, also
        when the venv in use is current."""
        try:
            import fcntl  # noqa: F401
        except ImportError:
            self.skipTest("no fcntl")
        os.makedirs(os.path.join(self.venv + ".new", "lib"))
        os.makedirs(self.venv + ".old")
        with mock.patch.object(bootstrap, "venv_current", return_value=True):
            self.assertEqual(bootstrap.setup_venv(quiet=True), bootstrap.venv_python(self.venv))
        self.assertFalse(os.path.exists(self.venv + ".new"))
        self.assertFalse(os.path.exists(self.venv + ".old"))
        self.assertTrue(os.path.exists(os.path.join(self.venv, "OLD")))         # the venv in use stays

    def test_ctrl_c_during_pip_leaves_no_venv_new(self):
        def call(command, **kwargs):
            if command[1:3] == ["-m", "venv"]:
                return self.fake_call()(command, **kwargs)
            raise KeyboardInterrupt
        with mock.patch.object(bootstrap, "run_child", side_effect=call), \
                mock.patch.object(bootstrap, "imports_ok", return_value=True), self.assertRaises(KeyboardInterrupt):
            bootstrap.setup_venv(force=True, quiet=True)
        self.assertFalse(os.path.exists(self.venv + ".new"))
        self.assertTrue(os.path.exists(os.path.join(self.venv, "OLD")))

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
        with mock.patch.object(bootstrap, "run_child", side_effect=slow), \
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
