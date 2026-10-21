"""Ubuntu's command-not-found in the interactive shell of the Ubuntu personas: the table of
tools/build_command_not_found.py (command -> package of the cnf indexes of the release), the runtime
file of the persona's release and the answer of the patched shell (shell/honeypot.py), as
command-not-found 23.04.0 / 22.04.0 print it (ubuntu:24.04 / ubuntu:22.04, round 4 references)."""
import configparser
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
