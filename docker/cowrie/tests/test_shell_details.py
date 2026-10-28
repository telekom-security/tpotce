"""Shell details of the personas (tests/fixtures/shell_details.json, recorded with `sh -c` / `bash -c` /
`bash -ic` in Docker): the status and text of a file without an x bit and of a folder run as a command,
$HISTFILE, and the HISTTIMEFORMAT of `history`."""
import copy
import json
import shutil
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

from tests.test_patch import FIXTURE, PATCH, FakeConfig, method, module_parts

HONEYPOT = "src/cowrie/shell/honeypot.py"
RECORDED = json.loads((Path(__file__).parent / "fixtures" / "shell_details.json").read_text(encoding="utf-8"))["images"]
BASH_IMAGES = ("debian_12", "debian_13", "ubuntu_22.04", "ubuntu_24.04")
ASH_IMAGES = ("busybox_1.25.1", "busybox_1.33.1", "busybox_1.36.1-musl")


class Patched(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        handle = tempfile.TemporaryDirectory()
        cls.addClassCleanup(handle.cleanup)
        root = Path(handle.name) / "cowrie"
        shutil.copytree(FIXTURE, root)
        subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(root)], check=True, capture_output=True,
                       text=True)
        cls.root = root
        cls.source = (root / HONEYPOT).read_text()
        cls.protocol = (root / "src/cowrie/shell/protocol.py").read_text()


class NotExecutableTest(Patched):
    """A folder or a file without an x bit as a command ends with status 126 in bash and BusyBox ash (a
    missing path 127)."""

    def shell(self, style, folders=(), files=()):
        values = {("shell", "not_found_style"): style, ("shell", "shell_name"): "sh" if style == "ash" else "bash"}
        namespace = {"CowrieConfig": FakeConfig(values)}
        module_parts(self.source, ["tpot_command_not_found_table", "tpot_command_not_found",
                                   "tpot_not_found_status"], namespace)
        fs = types.SimpleNamespace(isdir=lambda p: p in folders, exists=lambda p: p in folders or p in files,
                                   resolve_path=lambda p, c: p)
        login = values[("shell", "shell_name")]
        shell = types.SimpleNamespace(protocol=types.SimpleNamespace(fs=fs), cwd="/", line_numbers=True,
                                      current_line=1, syntax_label="-c", name=login)
        shell.error_prefix = lambda: method(self.source, "HoneyPotShell", "error_prefix", namespace)(shell)
        message = method(self.source, "HoneyPotShell", "command_not_found_message", namespace)
        return lambda cmd: message(shell, cmd).encode(), namespace["tpot_not_found_status"]

    def recorded(self, images, key):
        for image in images:
            out, err, _ = RECORDED[image]["cases"][key]
            yield image, err, int(out.strip())

    def test_a_folder_as_a_command(self):
        for style, images, shell in (("bash", BASH_IMAGES, "bash"), ("ash", ASH_IMAGES, "sh")):
            message, status = self.shell(style, folders=("/etc",))
            for image, err, code in self.recorded(images, f"{shell} -c '/etc; echo $?'"):
                with self.subTest(image=image):
                    self.assertEqual(message("/etc").decode(), err)
                    self.assertEqual(status(message("/etc")), code)
                    self.assertEqual(code, 126)

    def test_a_missing_path_and_command_stay_127(self):
        for style, images, shell in (("bash", BASH_IMAGES, "bash"), ("ash", ASH_IMAGES, "sh")):
            message, status = self.shell(style)
            for cmd in ("./nosuch", "nosuch"):
                for image, err, code in self.recorded(images, f"{shell} -c '{cmd}; echo $?'"):
                    with self.subTest(image=image, cmd=cmd):
                        self.assertEqual(message(cmd).decode(), err)
                        self.assertEqual(status(message(cmd)), code)
                        self.assertEqual(code, 127)

    def test_a_file_without_an_x_bit(self):
        # protocol.py's scriptcmd: the text with the prefix of the shell, status 126
        for style, images, shell in (("bash", BASH_IMAGES, "bash"), ("ash", ASH_IMAGES, "sh")):
            message, _ = self.shell(style, files=("./nx",))
            prefix = method(self.source, "HoneyPotShell", "error_prefix",
                            {"CowrieConfig": FakeConfig({("shell", "not_found_style"): style})})
            for image, err, code in self.recorded(images, f"{shell} -c ': > nx; chmod 644 nx; ./nx; echo $?'"):
                with self.subTest(image=image):
                    self.assertEqual(code, 126)
                    shell_object = types.SimpleNamespace(line_numbers=True, current_line=1, syntax_label="-c",
                                                         name=shell)
                    self.assertEqual(f"{prefix(shell_object)}./nx: Permission denied\n", err)
        namespace = {"CowrieConfig": FakeConfig({("shell", "not_found_style"): "ash"})}
        module_parts(self.protocol, ["tpot_exec_denied"], namespace)
        self.assertEqual(namespace["tpot_exec_denied"]("sh: ", "./nx"), ("sh: ./nx: Permission denied\n", 126))

    def test_the_no_x_bit_path_of_the_protocol(self):
        # protocol.py scriptcmd decides on the x bit of the file, then writes the text and the status
        namespace = {"CowrieConfig": FakeConfig({}), "command": types.SimpleNamespace(HoneyPotCommand=object),
                     "run_script_file": lambda *a, **k: self.fail("a file without an x bit was run")}
        module_parts(self.protocol, ["tpot_exec_denied"], namespace)
        scriptcmd = method(self.protocol, "HoneyPotBaseProtocol", "scriptcmd", namespace)
        for style, images, shell in (("bash", BASH_IMAGES, "bash"), ("ash", ASH_IMAGES, "sh")):
            namespace["CowrieConfig"].values[("shell", "not_found_style")] = style
            for image, err, code in self.recorded(images, f"{shell} -c ': > nx; chmod 644 nx; ./nx; echo $?'"):
                with self.subTest(image=image):
                    cls = scriptcmd(types.SimpleNamespace(), "/root/nx", "./nx")
                    out, asked = [], []
                    cmd = cls.__new__(cls)
                    cmd.fs = types.SimpleNamespace(is_executable=lambda path, uid, gid: asked.append(
                        (path, uid, gid)) or False)
                    cmd.user = {"uid": 0, "gid": 0}
                    cmd.shell = types.SimpleNamespace(error_prefix=lambda: err.split("./nx")[0])
                    cmd.errorWrite = out.append
                    cmd.call()
                    self.assertEqual(("".join(out), cmd.exit_code, asked), (err, code, [("/root/nx", 0, 0)]))

    def test_centos_7_has_the_status_but_not_the_text_of_bash_5(self):
        # bash 4.2 (centos:7): the same statuses; its messages have no "line 1: " (not modelled since the
        #   arithmetic task: the persona's text is that of bash 5)
        for key, code in (("/etc", 126), ("./nosuch", 127), ("nosuch", 127), (": > nx; chmod 644 nx; ./nx", 126)):
            out, err, _ = RECORDED["centos_7"]["cases"][f"bash -c '{key}; echo $?'"]
            five = RECORDED["debian_12"]["cases"][f"bash -c '{key}; echo $?'"][1]
            with self.subTest(key=key):
                self.assertEqual(int(out), code)
                self.assertEqual(err, five.replace("bash: line 1: ", "bash: "))
        message, status = self.shell("bash", folders=("/etc",))
        self.assertEqual(status(message("/etc")), 126)

    def test_the_other_status_stays(self):
        _, status = self.shell("bash")
        self.assertEqual(status(b"-bash: x: command not found\n"), 127)
        self.assertEqual(status(b"-bash: exec: x: not found\n"), 127)


class HistFileTest(Patched):
    """$HISTFILE: an interactive bash has ~/.bash_history as a shell variable (recorded: debian_12, centos_7),
    `bash -c` none (recorded) and `sh -c` of BusyBox none (recorded, not interactive); an interactive BusyBox
    ash and msh have none (derived, not recorded: they are not run on a terminal here)."""

    def environ(self, values, home="/root", uid=0):
        entry = {"pw_uid": uid, "pw_gid": uid, "pw_dir": home}
        pwd = types.SimpleNamespace(Passwd=lambda: types.SimpleNamespace(getpwnam=lambda name: entry))
        init = method(self.source_of("telnet.py"), "HoneyPotTelnetSession", "__init__",
                      {"CowrieConfig": FakeConfig(values), "pwd": pwd})
        session = types.SimpleNamespace()
        init(session, b"root", types.SimpleNamespace(initFileSystem=lambda home: None))
        return session.environ

    def source_of(self, name):
        return (self.root / "src/cowrie/shell" / name).read_text()

    def test_an_interactive_bash_has_it(self):
        for image in ("debian_12", "centos_7"):
            out, _, _ = RECORDED[image]["cases"]["bash -ic 'echo $HISTFILE'"]
            self.assertEqual(self.environ({})["HISTFILE"] + "\n", out)
        self.assertEqual(self.environ({}, home="/home/pi", uid=1000)["HISTFILE"], "/home/pi/.bash_history")

    def test_ash_and_msh_have_none(self):
        # recorded for `sh -c` only; the interactive ash and msh are derived
        for image in ASH_IMAGES:
            self.assertEqual(RECORDED[image]["cases"]["sh -c 'echo \"$HISTFILE\"'"][0], "\n")
        for style in ("ash", "msh"):
            self.assertNotIn("HISTFILE", self.environ({("shell", "not_found_style"): style}))

    def test_a_command_line_run_has_none(self):
        for image in ("debian_12",):
            self.assertEqual(RECORDED[image]["cases"]["bash -c 'echo \"$HISTFILE\"'"][0], "\n")
        ssh = self.source_of("session.py")
        init = method(ssh, "SSHSessionForCowrieUser", "__init__", {"CowrieConfig": FakeConfig({}), "stat": stat})
        session = types.SimpleNamespace()
        init(session, types.SimpleNamespace(home="/root", server=types.SimpleNamespace(initFileSystem=lambda h: None),
                                            uid=0, gid=0, username="root", temporary=False))
        self.assertNotIn("HISTFILE", session.environ)

    def test_the_ssh_interactive_shell_has_it(self):
        # shell/session.py openShell is the interactive shell over SSH (execCommand is a command line run)
        stub = types.SimpleNamespace(LoggingServerProtocol=lambda *a: types.SimpleNamespace(
            makeConnection=lambda p: None))
        namespace = {"CowrieConfig": FakeConfig({}), "stat": stat, "insults": stub,
                     "protocol": types.SimpleNamespace(HoneyPotInteractiveProtocol=object),
                     "ProtocolTransport": lambda p: None}
        open_shell = method(self.source_of("session.py"), "SSHSessionForCowrieUser", "openShell", namespace)
        processprotocol = types.SimpleNamespace(makeConnection=lambda t: None)
        for home, uid, want in (("/root", 0, "/root/.bash_history"), ("/home/pi", 1000, "/home/pi/.bash_history")):
            session = types.SimpleNamespace(environ={"HOME": home}, uid=uid)
            open_shell(session, processprotocol)
            self.assertEqual(session.environ["HISTFILE"], want)
        session = types.SimpleNamespace(environ={"HOME": "/"}, uid=0)
        namespace["CowrieConfig"] = FakeConfig({("shell", "not_found_style"): "ash"})
        method(self.source_of("session.py"), "SSHSessionForCowrieUser", "openShell", namespace)(session, processprotocol)
        self.assertNotIn("HISTFILE", session.environ)

    def test_it_is_not_exported(self):
        source = self.source
        values = {}
        for exports, expected in (("", {"PATH"}), ("HISTFILE", {"PATH", "HISTFILE"})):
            values[("shell", "history_export")] = exports

            class Shell:
                pass

            init = method(source, "HoneyPotShell", "__init__", {"CowrieConfig": FakeConfig(values), "copy": copy,
                                                                "HoneyPotShell": Shell})
            shell = Shell()
            init(shell, types.SimpleNamespace(cmdstack=[], environ={"PATH": "/bin", "HISTFILE": "/root/.bash_history"}))
            self.assertEqual(shell.exported, expected)


if __name__ == "__main__":
    unittest.main()
