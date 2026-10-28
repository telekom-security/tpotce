"""Ubuntu's command-not-found in the interactive shell of the Ubuntu personas: the table of
tools/build_command_not_found.py (command -> package of the cnf indexes of the release), the runtime
file of the persona's release and the answer of the patched shell (shell/honeypot.py), as
command-not-found 23.04.0 / 22.04.0 print it (ubuntu:24.04 / ubuntu:22.04, round 4 references)."""
import configparser
import json
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

from tests.test_patch import FIXTURE, PATCH, FakeConfig, method, module_parts
from tpot_personas import catalog, system
from tpot_personas.identity import draw
from tpot_personas.render import render, render_cfg

HONEYPOT = "src/cowrie/shell/honeypot.py"


class TableTest(unittest.TestCase):
    def test_the_table_of_each_ubuntu_release(self):
        for pid in ("ubuntu-noble", "ubuntu-jammy", "gpu-ubuntu"):
            persona = catalog.by_id(pid)
            for seed in range(6):
                ident = draw(persona, seed)
                with self.subTest(persona=pid, release=ident.release.label):
                    lines = system.command_not_found_lines(persona, ident)
                    table = {line.split(" ", 1)[0]: line.split(" ", 1)[1] for line in lines}
                    if ident.release.extra["codename"] == "noble":
                        self.assertEqual(table["nc"], "install netcat-openbsd=1.226-1ubuntu1,"
                                         "ncat=7.94+git20230807.3be01efb1+dfsg-2,netcat-traditional=1.10-48")
                        self.assertEqual(table["kubectl"], "similar kubectx/kubectx=0.9.5-1ubuntu0.4")
                    else:
                        self.assertEqual(table["nc"], "install netcat-openbsd=1.218-4ubuntu1,"
                                         "ncat=7.91+dfsg1+really7.80+dfsg1-2build1,netcat-traditional=1.10-47")
                        self.assertEqual(table["rar"], "similar-count 21")
                    self.assertEqual(table["htop"].split("=")[0], "install htop")
                    self.assertEqual(table["ifconfig"].split("=")[0], "install net-tools")
                    self.assertEqual(table["masscan"].split("=")[0], "install masscan")

    def test_only_ubuntu_has_it(self):
        for persona in catalog.PERSONAS:
            with self.subTest(persona=persona.id):
                lines = system.command_not_found_lines(persona, draw(persona, 0))
                self.assertEqual(lines is not None, persona.family == "ubuntu")

    def test_render_writes_the_file_and_the_keys(self):
        for pid in ("ubuntu-noble", "debian-bookworm"):
            with self.subTest(persona=pid):
                persona = catalog.by_id(pid)
                ident = draw(persona, 0)
                out = Path(tempfile.mkdtemp())
                self.addCleanup(shutil.rmtree, out)
                cfg = configparser.ConfigParser(interpolation=None)
                cfg.read_string(render_cfg(persona, ident, out, out / "keys"))
                if pid == "ubuntu-noble":
                    self.assertEqual(cfg.get("shell", "command_not_found"), f"{out}/{system.COMMAND_NOT_FOUND}")
                    self.assertEqual(cfg.get("shell", "command_not_found_sudo"), "ubuntu")
                else:
                    self.assertFalse(cfg.has_option("shell", "command_not_found"))


class AnswerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        handle = tempfile.TemporaryDirectory()
        cls.addClassCleanup(handle.cleanup)
        root = Path(handle.name) / "cowrie"
        shutil.copytree(FIXTURE, root)
        subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(root)], check=True, capture_output=True,
                       text=True)
        cls.source = (root / HONEYPOT).read_text()
        cls.table = Path(handle.name) / "command-not-found.txt"
        cls.table.write_text(
            "htop install htop=3.3.0-4build1\n"
            "nc install netcat-openbsd=1.226-1ubuntu1,ncat=7.94+git20230807.3be01efb1+dfsg-2,"
            "netcat-traditional=1.10-48\n"
            "kubectl similar kubectx/kubectx=0.9.5-1ubuntu0.4\n"
            "rar similar-count 21\n")

    def message(self, cmd, uid=0, username="root", interactive=True, table=True, style="bash"):
        values = {("shell", "not_found_style"): style}
        if table:
            values[("shell", "command_not_found")] = str(self.table)
            values[("shell", "command_not_found_sudo")] = "ubuntu"
        namespace = {"CowrieConfig": FakeConfig(values)}
        module_parts(self.source, ["tpot_command_not_found_table", "tpot_command_not_found"], namespace)
        namespace["tpot_command_not_found_table"].cache_clear()
        function = method(self.source, "HoneyPotShell", "command_not_found_message", namespace)
        shell = types.SimpleNamespace(
            protocol=types.SimpleNamespace(user=types.SimpleNamespace(uid=uid, username=username),
                                           fs=types.SimpleNamespace(isdir=lambda p: False, exists=lambda p: False,
                                                                    resolve_path=lambda p, c: p)),
            cwd="/root", line_numbers=not interactive, current_line=1, syntax_label=None,
            name="-bash" if interactive else "bash")
        shell.error_prefix = lambda: method(self.source, "HoneyPotShell", "error_prefix", namespace)(shell)
        self.shell = shell
        return function(shell, cmd)

    def test_one_package(self):
        # ubuntu:24.04, root: apt install htop; a user of the sudo group: sudo apt install htop
        self.assertEqual(self.message("htop"), "Command 'htop' not found, but can be installed with:\napt install htop\n")
        self.assertEqual(self.message("htop", 1000, "ubuntu"),
                         "Command 'htop' not found, but can be installed with:\nsudo apt install htop\n")
        # a user outside sudo: "Please ask your administrator." is a print() to stdout
        #   (advice_single_deb_package; ref cnf-direct-htop-tester-u2404: stdout)
        self.assertEqual(self.message("htop", 1001, "tester"),
                         "Command 'htop' not found, but can be installed with:\napt install htop\n")
        self.assertEqual(self.shell.tpot_cnf_stdout, b"Please ask your administrator.\n")
        self.message("htop", 1000, "ubuntu")
        self.assertEqual(getattr(self.shell, "tpot_cnf_stdout", b""), b"")

    def test_several_packages(self):
        # the first line ends with ", or", the others do not (advice_multi_deb_package)
        self.assertEqual(self.message("nc", 1000, "ubuntu"),
                         "Command 'nc' not found, but can be installed with:\n"
                         "sudo apt install netcat-openbsd      # version 1.226-1ubuntu1, or\n"
                         "sudo apt install ncat                # version 7.94+git20230807.3be01efb1+dfsg-2\n"
                         "sudo apt install netcat-traditional  # version 1.10-48\n")
        self.assertTrue(self.message("nc", 1001, "tester").endswith(
            "apt install netcat-traditional  # version 1.10-48\nAsk your administrator to install one of them.\n"))

    def test_spelling_suggestions(self):
        self.assertEqual(self.message("kubectl"), "Command 'kubectl' not found, did you mean:\n"
                                                  "  command 'kubectx' from deb kubectx (0.9.5-1ubuntu0.4)\n"
                                                  "Try: apt install <deb name>\n")
        self.assertTrue(self.message("kubectl", 1000, "ubuntu").endswith("Try: sudo apt install <deb name>\n"))
        self.assertEqual(self.message("rar"), "Command 'rar' not found, but there are 21 similar ones.\n")

    def test_python(self):
        self.assertEqual(self.message("python"), "Command 'python' not found, did you mean:\n"
                                                 "  command 'python3' from deb python3\n"
                                                 "  command 'python' from deb python-is-python3\n")

    def test_other_commands_and_shells_stay(self):
        # command_not_found_handle prints "nosuchcmd123: command not found", without bash's name
        #   (refs cnf-bash-i-root-u2404 / -u2204, cnf-bash-i-tester-*)
        self.assertEqual(self.message("nosuchcmd123"), "nosuchcmd123: command not found\n")
        # bash -c and scripts (no command_not_found_handle of /etc/bash.bashrc)
        self.assertEqual(self.message("htop", interactive=False), "bash: line 1: htop: command not found\n")
        # a persona without the table
        self.assertEqual(self.message("htop", table=False), "-bash: htop: command not found\n")
        self.assertEqual(self.message("/usr/bin/htop"), "-bash: /usr/bin/htop: No such file or directory\n")

    def test_the_stdout_part_goes_out_after_the_error(self):
        source = self.source
        start = source.index("    def _write_shell_error(")
        self.assertIn("pp.outReceived(", source[start:start + 1200])

    def write_shell_error(self, redirection_error):
        """_write_shell_error of the patched shell with the command-not-found stdout pending: what the
        pipe got, in order, and what stays pending."""
        got = []

        class Pipe:
            has_redirection_error = redirection_error
            redirect_real_files = []

            def errReceived(self, data):  # noqa: N802 - Twisted's name
                got.append(("err", data))

            def outReceived(self, data):  # noqa: N802 - Twisted's name
                got.append(("out", data))

        shell = types.SimpleNamespace(tpot_cnf_stdout=b"Please ask your administrator.\n",
                                      _make_pipe=lambda *args: Pipe(),
                                      protocol=types.SimpleNamespace(terminal=types.SimpleNamespace(redirFiles=set())))
        function = method(self.source, "HoneyPotShell", "_write_shell_error", {"Any": object})
        written = function(shell, b"htop: not found\n")
        return written, got, shell.tpot_cnf_stdout

    def test_the_stdout_part_follows_the_error_once(self):
        self.assertEqual(self.write_shell_error(False),
                         (True, [("err", b"htop: not found\n"), ("out", b"Please ask your administrator.\n")], b""))

    def test_a_failed_redirection_leaves_nothing_for_a_later_error(self):
        # bash reports only the redirection and runs no handler; its text must not follow the next error
        self.assertEqual(self.write_shell_error(True), (False, [], b""))

    def test_a_missing_or_broken_table_is_no_table(self):
        values = {("shell", "command_not_found"): "/nonexistent/table", ("shell", "command_not_found_sudo"): ""}
        namespace = {"CowrieConfig": FakeConfig(values)}
        module_parts(self.source, ["tpot_command_not_found_table", "tpot_command_not_found"], namespace)
        self.assertIsNone(namespace["tpot_command_not_found"]("htop", 0, "root"))
        broken = self.table.with_name("broken.txt")
        broken.write_bytes(b"htop\nnc install\n\xff\xfe x\nok install a=1\n")
        namespace["CowrieConfig"] = FakeConfig({("shell", "command_not_found"): str(broken)})
        namespace["tpot_command_not_found_table"].cache_clear()
        self.assertIsNone(namespace["tpot_command_not_found"]("htop", 0, "root"))
        self.assertEqual(namespace["tpot_command_not_found"]("ok", 0, "root"),
                         ("Command 'ok' not found, but can be installed with:\napt install a\n", ""))


    def test_fails_loudly_on_unknown_upstream(self):
        cases = ("\n\nclass HoneyPotShell:\n", "\nimport copy\nimport enum\n",
                 "\nfrom cowrie.core.config import CowrieConfig\n", "        pp.errReceived(message)\n")
        for old in cases:
            with self.subTest(needle=old), tempfile.TemporaryDirectory() as handle:
                root = Path(handle) / "cowrie"
                shutil.copytree(FIXTURE, root)
                path = root / HONEYPOT
                text = path.read_text()
                self.assertEqual(text.count(old), 1)
                path.write_text(text.replace(old, old[:-1] + "  # x\n"))
                done = subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(root)],
                                      capture_output=True, text=True)
                self.assertNotEqual(done.returncode, 0)
                self.assertIn("Could not find expected text", done.stderr)



MSH = json.loads((Path(__file__).parent / "fixtures" / "msh.json").read_text(encoding="utf-8"))["images"]
MSH_C = MSH["fw_dlink_dir859_105b03_msh_c"]["cases"]
MSH_PTY = MSH["fw_dlink_dir859_105b03_msh_pty"]["cases"]


class MshTest(unittest.TestCase):
    """The DIR-859 speaks BusyBox msh (1.14.1 of its 1.05B03 image, tests/fixtures/msh.json): a command it
    cannot find is "x: not found" without the shell's name (127), a folder or a file without an x bit
    "x: can't execute" (126), the builtins msh has not are not found."""

    @classmethod
    def setUpClass(cls):
        handle = tempfile.TemporaryDirectory()
        cls.addClassCleanup(handle.cleanup)
        root = Path(handle.name) / "cowrie"
        shutil.copytree(FIXTURE, root)
        subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(root)], check=True, capture_output=True,
                       text=True)
        cls.source = (root / HONEYPOT).read_text()
        cls.protocol = (root / "src/cowrie/shell/protocol.py").read_text()
        cls.base = (root / "src/cowrie/commands/base.py").read_text()
        cls.persona = catalog.by_id("dlink-dir859")
        cfg = configparser.ConfigParser(interpolation=None)
        cfg.read_string(render_cfg(cls.persona, draw(cls.persona, 0), Path("/tmp/r"), Path("/tmp/k")))
        cls.cfg = cfg

    def message(self, cmd, interactive=True, folders=(), files=()):
        namespace = {"CowrieConfig": FakeConfig({("shell", key): value for key, value in self.cfg.items("shell")})}
        module_parts(self.source, ["tpot_command_not_found_table", "tpot_command_not_found"], namespace)
        function = method(self.source, "HoneyPotShell", "command_not_found_message", namespace)
        fs = types.SimpleNamespace(isdir=lambda p: p in folders, exists=lambda p: p in folders or p in files,
                                   resolve_path=lambda p, c: p)
        login = self.cfg.get("shell", "shell_name")
        shell = types.SimpleNamespace(protocol=types.SimpleNamespace(fs=fs), cwd="/", line_numbers=not interactive,
                                      current_line=1, syntax_label=None if interactive else "-c",
                                      name=f"-{login}" if interactive else login)
        shell.error_prefix = lambda: method(self.source, "HoneyPotShell", "error_prefix", namespace)(shell)
        return function(shell, cmd)

    def status(self, message):
        namespace = {"CowrieConfig": FakeConfig({("shell", "not_found_style"): "msh"})}
        module_parts(self.source, ["tpot_not_found_status"], namespace)
        return namespace["tpot_not_found_status"](message.encode())

    def test_the_persona_speaks_msh(self):
        self.assertIn("dlink", system.MSH_FAMILIES)
        self.assertNotIn("dlink", system.ASH_FAMILIES)
        self.assertEqual(system.shell_style(self.persona), ("sh", "msh"))
        self.assertEqual(self.cfg.get("shell", "not_found_style"), "msh")

    def test_a_missing_command(self):
        for interactive in (True, False):
            with self.subTest(interactive=interactive):
                out, err, code = MSH_C["nosuch"]
                self.assertEqual(self.message("nosuch", interactive), err)
                self.assertEqual(self.status(err), code)
                self.assertIn("# nosuch\r\nnosuch: not found\r\n# echo $?\r\n127\r\n", MSH_PTY["nosuch\necho $?\nexit\n"][0])
                out, err, code = MSH_C["/nosuch/x"]
                self.assertEqual(self.message("/nosuch/x", interactive), err)
                self.assertEqual(self.status(err), code)

    def test_a_folder_cannot_be_executed(self):
        out, err, code = MSH_C["/tmp"]
        self.assertEqual(self.message("/tmp", folders=("/tmp",)), err)
        self.assertEqual(self.status(err), code)
        self.assertEqual(MSH_PTY["/tmp\nexit\n"][2], 126)
        # the shell takes its status from the message
        self.assertIn("self.last_exit_code = tpot_not_found_status(message)", self.source)

    def test_a_file_without_an_x_bit(self):
        out, err, code = MSH_C["./nx"]
        namespace = {"CowrieConfig": FakeConfig({("shell", "not_found_style"): "msh"})}
        module_parts(self.protocol, ["tpot_exec_denied"], namespace)
        self.assertEqual(namespace["tpot_exec_denied"]("", "./nx"), (err, code))
        namespace = {"CowrieConfig": FakeConfig({})}
        module_parts(self.protocol, ["tpot_exec_denied"], namespace)
        self.assertEqual(namespace["tpot_exec_denied"]("-bash: ", "./nx"), ("-bash: ./nx: Permission denied\n", 126))

    def test_the_builtins_msh_has_not(self):
        skipped = {name.strip() for name in self.cfg.get("shell", "skip_python_commands").split(",")}
        for case in ("alias", "type ls", "history", "jobs", "unset HOME; echo $HOME"):
            name = case.split()[0]
            with self.subTest(command=name):
                self.assertEqual(MSH_C[case][1], f"{name}: not found\n")
                self.assertIn(name, skipped)
                self.assertEqual(self.message(name), MSH_C[case][1])
        self.assertEqual(MSH_C["ulimit -a | head -2"][1], "ulimit: not found\n")
        self.assertIn("ulimit", skipped)
        # msh's own builtins and the applets of the firmware (kill) stay
        for name in ("cd", "export", "set", "exit", "help", "umask", "echo", "kill", "test"):
            self.assertNotIn(name, skipped, name)

    def test_fails_loudly_on_unknown_upstream(self):
        # the needles of patch_msh and patch_bash_version
        cases = (("src/cowrie/shell/honeypot.py", "\n\nclass HoneyPotShell:\n"),
                 ("src/cowrie/shell/honeypot.py", "            self.last_exit_code = 127  # command not found\n"),
                 ("src/cowrie/shell/protocol.py", "class HoneyPotBaseProtocol(insults.TerminalProtocol, TimeoutMixin):\n"),
                 ("src/cowrie/shell/protocol.py", '                    self.errorWrite(f"{prefix}{name}: Permission denied\\n")\n'
                                                  "                    self.exit_code = 126\n"),
                 ("src/cowrie/shell/protocol.py", '            self.terminal.write(self.fs.file_contents("/etc/motd"))\n'),
                 ("src/cowrie/commands/base.py", "class Command_help(HoneyPotCommand):\n    def call(self) -> None:\n"),
                 ("src/cowrie/commands/bash.py", "from cowrie.shell.command import HoneyPotCommand\n"),
                 ("src/cowrie/commands/bash.py", "    def start(self) -> None:\n"
                                                 '        if self.args and self.args[0].strip() == "-c":\n'))
        for rel, old in cases:
            with self.subTest(file=rel, needle=old.strip()[:40]), tempfile.TemporaryDirectory() as handle:
                root = Path(handle) / "cowrie"
                shutil.copytree(FIXTURE, root)
                path = root / rel
                text = path.read_text()
                self.assertEqual(text.count(old), 1)
                path.write_text(text.replace(old, old[:-1] + "  # x\n"))
                done = subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(root)],
                                      capture_output=True, text=True)
                self.assertNotEqual(done.returncode, 0)
                self.assertIn("Could not find expected text", done.stderr)

    def test_help_lists_the_builtins_of_msh(self):
        # on a terminal (a Telnet login); under -c msh prints nothing
        transcript = MSH_PTY["help\nexit\n"][0].replace("\r\n", "\n")
        text = transcript.split("# help\n", 1)[1].rsplit("# exit\n", 1)[0]
        self.assertTrue(text.startswith("\nBuilt-in commands:\n"))
        self.assertEqual(MSH_C["help"], ["", "", 0])
        for interactive, expected in ((True, text), (False, "")):
            written = []
            command = types.SimpleNamespace(write=written.append,
                                            shell=types.SimpleNamespace(interactive=interactive))
            call = method(self.base, "Command_help", "call",
                          {"CowrieConfig": FakeConfig({("shell", "not_found_style"): "msh"})})
            call(command)
            self.assertEqual("".join(written), expected, interactive)
        # bash's help stays elsewhere
        written = []
        call = method(self.base, "Command_help", "call", {"CowrieConfig": FakeConfig({})})
        call(types.SimpleNamespace(write=written.append, shell=types.SimpleNamespace(interactive=True)))
        self.assertTrue("".join(written).startswith("GNU bash, version"))


class PythonFloorTest(unittest.TestCase):
    def test_no_backslash_in_an_f_string_expression(self):
        # Python 3.11, the floor of the project, takes no backslash in the expression of an f-string
        #   (PEP 701 allows it from 3.12)
        import ast
        root = Path(__file__).resolve().parent.parent
        for path in sorted((root / "tools").glob("*.py")) + sorted((root / "dist" / "tpot_personas").glob("*.py")):
            source = path.read_text(encoding="utf-8")
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, ast.FormattedValue):
                    segment = ast.get_source_segment(source, node.value) or ""
                    with self.subTest(path=path.name, line=node.lineno):
                        self.assertNotIn("\\", segment)


if __name__ == "__main__":
    unittest.main()
