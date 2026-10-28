"""pgrep and pidof of the patched Cowrie (Command_tpot_pgrep, Command_tpot_pidof of patch_ps_top): the BusyBox applets of
a firmware (the output of the real programs in containers, fixtures/pgrep_refs.json) and the pidof of a server, over the
same rows as ps and top."""
import ast
import json
import re
import shlex
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from tests.test_grep import grep_classes
from tests.test_top import PS, Config, PatchedTest, Session

REFS = json.loads((Path(__file__).parent / "fixtures" / "pgrep_refs.json").read_text(encoding="utf-8"))
BANNER_136 = "BusyBox v1.36.1 (2023-05-18 22:34:17 UTC)"
BANNER_133 = "BusyBox v1.33.1 (2021-09-13 17:21:17 UTC)"
BANNER_126 = "BusyBox v1.26.2 (2017-06-15 00:40:02 UTC)"


def proc(pid, command, user="root", tty="?", **extra):
    return dict({"USER": user, "PID": pid, "CPU": 0.0, "MEM": 0.0, "VSZ": 1232, "RSS": 700, "TTY": tty, "STAT": "S",
                 "START": "Sep30", "TIME": 0.0, "COMMAND": command}, **extra)


# the containers of the recordings: the container's sh (PID 1) with three sleeps (7, 8, 9), or two
THREE = [proc(1, "sh")] + [proc(7 + n, f"/bin/sleep {301 + n}") for n in range(3)]
TWO = [proc(1, "sh")] + [proc(7 + n, f"/bin/sleep {301 + n}") for n in range(2)]
SLEEPS = [proc(7 + n, f"/bin/sleep {301 + n}") for n in range(3)]


def firmware(version="1.36.1", banner=BANNER_136, libc="glibc", help_text=None):
    values = {("shell", "top_style"): "busybox", ("shell", "text_version"): version, ("shell", "text_libc"): libc,
              ("shell", "text_banner"): banner, ("shell", "kernel_threads"): "0",
              ("shell", "ssh_version"): "Dropbear v2022.82"}
    if help_text is not None:
        handle = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
        handle.write(help_text)
        handle.close()
        values[("shell", "busybox_help")] = handle.name
    return values


class RunTest(PatchedTest):
    def run_line(self, line, processes, config=None, **session):
        """(output, status) of a command line of the recordings: stdout and stderr together."""
        args = shlex.split(line)
        made = Session(self, config=config or {}, processes=processes, username="root", target="capture", **session)
        classes = grep_classes("3.11")
        grep = types.ModuleType("cowrie.commands.fs")
        grep.TpotGrepMatcher, grep.TpotGrepSyntax = classes["TpotGrepMatcher"], classes["TpotGrepSyntax"]
        with mock.patch.dict(sys.modules, {"cowrie.commands.fs": grep}):
            cmd = made.run({"pgrep": "Command_tpot_pgrep", "pidof": "Command_tpot_pidof"}[args[0]], *args[1:])
        return cmd.out + cmd.err, cmd.exit_code


def recorded(refs, key):
    ref = refs[key]
    return ref["out"], ref["rc"]


class BusyboxPgrepTest(RunTest):
    def test_the_recorded_selections(self):
        """pgrep of BusyBox 1.36.1 over the sleeps of the recording: names, whole names, command lines, newest, oldest."""
        refs = REFS["busybox"]
        for line in ("pgrep sleep", "pgrep -l sleep", "pgrep -a sleep", "pgrep -x sleep", "pgrep -x sle",
                     "pgrep -f 'sleep 302'", "pgrep -lf 'sleep 302'", "pgrep -n sleep", "pgrep -o sleep",
                     "pgrep nomatch", "pgrep sle.p", "pgrep 'sleep|nomatch'"):
            with self.subTest(line=line):
                self.assertEqual(self.run_line(line, THREE, firmware()), recorded(refs, line))

    def test_the_recorded_edge_cases(self):
        refs = REFS["busybox"]
        for line in ("pgrep -x '/bin/sleep'", "pgrep -lx /bin/sleep", "pgrep -lo sleep", "pgrep -ln sleep",
                     "pgrep sleep -l", "pgrep -P 99999 sleep", "pgrep -- sleep", "pgrep pgrep",
                     "pgrep -f pgrep", "pgrep -l 'sl.*p'"):
            with self.subTest(line=line):
                self.assertEqual(self.run_line(line, TWO, firmware()), recorded(refs, "3 " + line))

    def test_the_newest_process_that_does_not_match(self):
        scene = TWO + [proc(30, "/usr/sbin/crond")]
        out, status = self.run_line("pgrep -nv sleep", scene, firmware())
        self.assertEqual(status, 0)
        self.assertRegex(out, r"\A\d+\n\Z")
        self.assertNotIn(out, ("7\n", "8\n"))
        self.assertGreaterEqual(int(out), 30)
        pids = [int(line) for line in self.run_line("pgrep -v sleep", scene, firmware())[0].splitlines()]
        self.assertNotIn(7, pids)
        self.assertIn(1, pids)

    def test_an_empty_pattern_selects_every_process_but_pgrep(self):
        out, status = self.run_line("pgrep ''", TWO, firmware())
        self.assertEqual(status, 0)
        pids = [int(line) for line in out.splitlines()]
        self.assertEqual(pids, sorted(pids))
        self.assertTrue({1, 7, 8} <= set(pids))

    def test_usage_and_option_errors_as_recorded(self):
        refs = REFS["busybox"]
        for line in ("pgrep", "pgrep -c sleep", "pgrep -z", "pgrep -h", "pgrep sleep nomatch", "pgrep -i sleep"):
            key = line if line in refs else "3 " + line
            with self.subTest(line=line):
                self.assertEqual(self.run_line(line, THREE, firmware()), recorded(refs, key))

    def test_a_bad_regex_and_a_bad_number(self):
        refs = REFS["busybox"]
        self.assertEqual(self.run_line("pgrep '('", THREE, firmware()), recorded(refs, "pgrep '('"))
        self.assertEqual(self.run_line("pgrep -s x sleep", TWO, firmware()), recorded(refs, "3 pgrep -s x sleep"))

    def test_an_old_firmware_prints_the_option_without_quotes(self):
        refs = REFS["busybox_1_33"]
        config = firmware("1.33.1", BANNER_133, "uclibc")
        for line in ("pgrep -c sleep", "pgrep -z"):
            with self.subTest(line=line):
                self.assertEqual(self.run_line(line, THREE, config), recorded(refs, line))
        out, _ = self.run_line("pidof -o", THREE, config)
        self.assertTrue(out.startswith("pidof: option requires an argument -- o\n"))

    def test_before_1_27_pgrep_has_no_a_option(self):
        """BusyBox 1.26: -a is an invalid option and the usage lists -flnovx (recorded)."""
        refs = REFS["busybox_1_26"]
        config = firmware("1.26.2", BANNER_126, "uclibc")
        for line in ("pgrep", "pgrep -a x"):
            with self.subTest(line=line):
                self.assertEqual(self.run_line(line, THREE, config), recorded(refs, line))
        self.assertEqual(self.run_line("pgrep -l sleep", THREE, config), ("7 /bin/sleep\n8 /bin/sleep\n9 /bin/sleep\n", 0))

    def test_an_applet_the_busybox_table_lacks_is_not_found(self):
        """A link to busybox for an applet that the persona's own help does not name answers as BusyBox does."""
        table = "Currently defined functions:\n\tash, ps, sleep\n"
        self.assertEqual(self.run_line("pgrep sleep", THREE, firmware(help_text=table)), ("pgrep: applet not found\n", 127))
        named = "Currently defined functions:\n\tash, pgrep,\n\tps\n"
        self.assertEqual(self.run_line("pgrep sleep", THREE, firmware(help_text=named)), ("7\n8\n9\n", 0))

    def test_the_session_and_the_parent(self):
        """-P a parent: the sleeps of the cron daemon and no others; -s 0 the session of pgrep: the login shell."""
        shell = proc(1520, "-ash", tty="pts/0")
        jobs = [proc(7 + n, f"/bin/sleep {301 + n}", PPID=30) for n in range(2)]
        scene = [proc(1, "/sbin/procd"), proc(30, "/usr/sbin/crond"), shell, *jobs, proc(31, "/bin/sleep 400")]
        for line, expected in (("pgrep -P 30 sleep", "7\n8\n"), ("pgrep -P 30 -l", "7 /bin/sleep\n8 /bin/sleep\n"),
                               ("pgrep -P 99999 sleep", ""), ("pgrep -s 0 sleep", ""),
                               ("pgrep sleep", "7\n8\n31\n")):
            with self.subTest(line=line):
                self.assertEqual(self.run_line(line, scene, firmware()), (expected, 0 if expected else 1))
        out, status = self.run_line("pgrep -s 0 -l", scene, firmware())
        self.assertRegex(out, r"\A\d+ ash\n\Z")
        self.assertEqual(status, 0)

    def test_the_names_are_those_of_a_program(self):
        """The name is argv[0] where its base name is the comm, else the comm (a daemon that renames itself, a login shell)."""
        scene = [proc(1, "/sbin/procd"), proc(40, "sshd: root@pts/0"), proc(41, "-ash", tty="pts/0"),
                 proc(42, "/usr/sbin/dropbear -F -P /var/run/dropbear.1.pid")]
        out, _ = self.run_line("pgrep -l .", scene, firmware())
        self.assertIn("1 /sbin/procd\n", out)
        self.assertIn("40 sshd\n", out)
        self.assertRegex(out, r"(?m)^\d+ ash$")
        self.assertIn("42 /usr/sbin/dropbear\n", out)

    def test_pgrep_does_not_hang_on_a_pattern_that_repeats(self):
        out, status = self.run_line("pgrep '(a*)*Q'", THREE, firmware())
        self.assertEqual((out, status), ("", 1))


class BusyboxPidofTest(RunTest):
    def test_the_recorded_lists(self):
        """PIDs highest first on one line, a name after another, the program itself included."""
        refs = REFS["busybox"]
        for line in ("pidof sleep", "pidof nomatch", "pidof", "pidof -s sleep", "pidof -o %PPID sleep",
                     "pidof /bin/sleep", "pidof -x sh", "pidof -z"):
            with self.subTest(line=line):
                self.assertEqual(self.run_line(line, THREE, firmware()), recorded(refs, line))
        self.assertEqual(self.run_line("pidof sleep nomatch sh", THREE, firmware()), ("9 8 7 1\n", 0))

    def test_one_pid_per_name_and_omitted_pids(self):
        refs = REFS["busybox"]
        self.assertEqual(self.run_line("pidof -o 7 sleep", TWO, firmware()), recorded(refs, "3 pidof -o 7 sleep"))
        self.assertEqual(self.run_line("pidof -o 7 -o 8 sleep", TWO, firmware()),
                         recorded(refs, "3 pidof -o 7 -o 8 sleep"))
        self.assertEqual(self.run_line("pidof -s sleep sh", TWO, firmware()), recorded(refs, "3 pidof -s sleep sh"))
        out, status = self.run_line("pidof -s pidof sleep", TWO, firmware())
        self.assertRegex(out, r"^\d+ 8\n$")
        self.assertEqual(status, 0)

    def test_pidof_names_itself(self):
        out, status = self.run_line("pidof pidof", TWO, firmware())
        self.assertRegex(out, r"^\d+\n$")
        self.assertGreater(int(out), 8)
        self.assertEqual(status, 0)

    def test_the_option_without_its_value(self):
        refs = REFS["busybox"]
        self.assertEqual(self.run_line("pidof -o", TWO, firmware()), recorded(refs, "3 pidof -o"))

    def test_an_omitted_pid_must_be_a_number(self):
        refs = REFS["busybox_1_26"]
        config = firmware("1.26.2", BANNER_126, "uclibc")
        for line in ("pidof -o x sleep", "pidof -o 7x sleep", "pidof -o 7,8 sleep"):
            key = "3 " + line
            with self.subTest(line=line):
                self.assertEqual(self.run_line(line, THREE, config), recorded(refs, key))

    def test_the_usage_follows_the_version(self):
        """1.26 and 1.33 differ in the synopsis only (recorded both)."""
        old = firmware("1.26.2", BANNER_126, "uclibc")
        for line in ("pidof -z", "pidof -o"):
            with self.subTest(line=line):
                self.assertEqual(self.run_line(line, THREE, old), recorded(REFS["busybox_1_26"], line))
        out, _ = self.run_line("pidof -z", THREE, firmware("1.33.1", BANNER_133, "uclibc"))
        self.assertEqual((out, 1), recorded(REFS["busybox_1_33"], "pidof -z"))
        self.assertIn("Usage: pidof [-s] [-o PID] [NAME]...", out)
        self.assertIn("Usage: pidof [OPTIONS] [NAME]...", self.run_line("pidof -z", THREE, old)[0])

    def test_an_applet_the_busybox_table_lacks_is_not_found(self):
        table = "Currently defined functions:\n\tash, ps, sleep\n"
        self.assertEqual(self.run_line("pidof sleep", THREE, firmware(help_text=table)), ("pidof: applet not found\n", 127))


class ProcpsPidofTest(RunTest):
    """The pidof of a server (recorded on Debian 12 and 13 and Ubuntu 24.04, which print the same)."""

    def run_pidof(self, line):
        return self.run_line(line, SLEEPS, {})

    def test_the_recorded_answers(self):
        refs = REFS["pidof"]
        for key in refs:
            if key in ("pidof pidof", "pidof -s pidof sleep"):
                continue  # its own PID differs per run
            with self.subTest(line=key):
                self.assertEqual(self.run_pidof(key), recorded(refs, key))

    def test_pidof_names_itself_and_takes_one_pid_per_name(self):
        out, status = self.run_pidof("pidof pidof")
        self.assertRegex(out, r"^\d+\n$")
        out, status = self.run_pidof("pidof -s pidof sleep")
        self.assertRegex(out, r"^\d+ 9\n$")
        self.assertEqual(status, 0)

    def test_the_separator(self):
        self.assertEqual(self.run_pidof("pidof -d , sleep"), ("9,8,7\n", 0))


class PidofStyleTest(RunTest):
    """The pidof of RHEL 9 (procps-ng, recorded) and of the sysvinit ones (CentOS 7 recorded) differ from Debian's."""

    def run_style(self, line, style):
        config = {("shell", "pidof_style"): style, ("shell", "procps_version"): "3.3.17"}
        return self.run_line(line, SLEEPS, config)

    def test_procps_ng_prints_its_own_usage_and_messages(self):
        refs = REFS["pidof_el9"]
        for line in ("pidof -h", "pidof --help", "pidof -V", "pidof -z sleep", "pidof -Z sleep", "pidof -o",
                     "pidof -o x sleep"):
            with self.subTest(line=line):
                self.assertEqual(self.run_style(line, "procps-ng"), recorded(refs, line))

    def test_procps_ng_lists_the_pids_with_its_separator_and_single_shot(self):
        self.assertEqual(self.run_style("pidof sleep", "procps-ng"), ("9 8 7\n", 0))
        self.assertEqual(self.run_style("pidof -s sleep", "procps-ng"), ("9\n", 0))
        self.assertEqual(self.run_style("pidof -S , sleep", "procps-ng"), ("9,8,7\n", 0))

    def test_sysvinit_says_nothing_for_help_and_unknown_options(self):
        refs = REFS["pidof_sysvinit"]
        for line in ("pidof -h", "pidof --help", "pidof -V", "pidof -z sleep", "pidof -Z sleep", "pidof -w sleep",
                     "pidof -o", "pidof -q sleep"):
            with self.subTest(line=line):
                self.assertEqual(self.run_style(line, "sysvinit"), recorded(refs, line))

    def test_sysvinit_takes_a_pid_that_is_no_number(self):
        """Recorded on CentOS 7: -o x is ignored."""
        self.assertEqual(self.run_style("pidof -o x sleep", "sysvinit"), ("9 8 7\n", 0))
        self.assertEqual(self.run_style("pidof -s sleep", "sysvinit"), ("9\n", 0))

    def test_debian_is_the_default(self):
        self.assertEqual(self.run_line("pidof -h", SLEEPS, {})[0], REFS["pidof"]["pidof -h"]["out"])


class RegistrationTest(PatchedTest):
    def test_the_programs_of_the_persona_are_answered_by_path(self):
        """A node of the file system for pgrep or pidof reaches the command (not Cowrie's script runner: Exec format
        error); a name alone is not a command, a persona without the node has none."""
        source = (self.root / "src/cowrie/commands/base.py").read_text()
        for name in ("pgrep", "pidof"):
            self.assertIn(f'commands[f"{{_dir}}/{name}"] = Command_tpot_{name}', source)
        self.assertNotIn('commands["pgrep"]', source)
        self.assertNotIn('commands["pidof"]', source)
        self.assertIn('for _name in ("ps", "/bin/ps", "/usr/bin/ps"):\n    commands[_name] = Command_tpot_ps', source)

    def command_for(self, name, nodes, cwd="/root"):
        """What the patched getCommand makes of a name over a file system with these nodes (upstream's own method)."""
        base = self.base_commands()
        source = (self.root / "src/cowrie/shell/protocol.py").read_text()
        keep = []
        for node in ast.parse(source).body:
            if isinstance(node, ast.ClassDef) and node.name == "HoneyPotBaseProtocol":
                keep = [item for item in node.body if isinstance(item, ast.FunctionDef) and item.name == "getCommand"]
        self.assertTrue(keep, "getCommand not found")
        module = ast.Module([ast.ClassDef(name="Proto", bases=[], keywords=[], body=keep, decorator_list=[])], [])
        ast.fix_missing_locations(module)

        def no_data(*parts):
            raise FileNotFoundError(parts)

        contents = 5

        class Fs:
            @staticmethod
            def resolve_path(path, cwd):
                return path if path.startswith("/") else f"{cwd}/{path}"

            @staticmethod
            def exists(path):
                return path in nodes

            isfile = exists

            @staticmethod
            def getfile(path, follow_symlinks=True):
                return None

            fs = {contents: []}

        namespace = {"CowrieConfig": Config({}), "Path": Path, "read_data_bytes": no_data,
                     "fs": types.SimpleNamespace(A_TYPE=1, A_TARGET=2, A_NAME=3, T_LINK=4, A_CONTENTS=contents),
                     "tpot_skip_python_commands": frozenset, "tpot_builtins": lambda: None}
        exec(compile(module, "<patched protocol>", "exec"), namespace)  # noqa: S102
        proto = namespace["Proto"]()
        proto.commands, proto.fs, proto._log = base, Fs(), types.SimpleNamespace(info=lambda *a, **k: None)
        proto.txtcmd = lambda data: "txtcmd"
        proto.scriptcmd = lambda path, name: "script"
        return proto.getCommand(name, ["/usr/local/bin", "/usr/sbin", "/usr/bin", "/sbin", "/bin"], cwd)

    def base_commands(self):
        if not hasattr(self, "_commands"):
            self._commands = Session(self).base["commands"]
        return self._commands

    def test_a_node_of_the_persona_reaches_the_command_and_no_node_none(self):
        """The regression of the first version: a node with no command went to the script runner (Exec format error)."""
        commands = self.base_commands()
        for name in ("pgrep", "pidof"):
            cls = commands[f"/bin/{name}"]
            self.assertEqual(cls.__name__, f"Command_tpot_{name}")
            for folder in ("/bin", "/sbin", "/usr/bin", "/usr/sbin"):
                with self.subTest(name=name, folder=folder):
                    self.assertIs(self.command_for(name, {f"{folder}/{name}"}), commands[f"{folder}/{name}"])
            self.assertIsNone(self.command_for(name, set()))
            self.assertNotIn(name, commands)
            self.assertEqual(self.command_for(name, {f"/usr/local/bin/{name}"}), "script")
            self.assertIsNone(self.command_for(f"/usr/local/bin/{name}", set()))

    def test_a_persona_with_a_pgrep_is_a_busybox_one(self):
        """The pgrep of the personas is BusyBox's (no procps pgrep is answered)."""
        import tempfile
        from tests.helpers import base_tree
        from tpot_personas import catalog, tree
        from tpot_personas.identity import draw
        from tpot_personas.render import render
        for persona in catalog.PERSONAS:
            ident = draw(persona, 1, 1_790_000_000)
            out = Path(tempfile.mkdtemp())
            render(persona, ident, base_tree(), out, runtime_dir=out, keys_dir=out / "keys")
            fs = tree.load_pickle(out / "fs.pickle")
            has = any(tree.resolve(fs, f"{folder}/pgrep") is not None for folder in ("bin", "sbin", "usr/bin", "usr/sbin"))
            if has:
                style = persona.builder(persona, ident).top_style
                self.assertEqual(style, "busybox", persona.id)


if __name__ == "__main__":
    unittest.main()
