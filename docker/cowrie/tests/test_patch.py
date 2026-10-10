import ast
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from tests.helpers import DIST, tmpdir

FIXTURE = Path(__file__).parent / "fixtures" / "v3.1.1"
PATCH = DIST / "patch_cowrie_persona_support.py"


def method(source, cls, name):
    """The function `name` of class `cls` in source, compiled on its own."""
    for node in ast.parse(source).body:
        if isinstance(node, ast.ClassDef) and node.name == cls:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == name:
                    namespace = {}
                    exec(compile(ast.Module([item], []), "<patched>", "exec"), namespace)  # noqa: S102
                    return namespace[name]
    raise AssertionError(f"{cls}.{name} not found")


def module_parts(source, names, namespace):
    """The imports (not those of cowrie or twisted) and the top-level classes / functions
    `names` of source, run in namespace (stubs for what they need)."""
    keep = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] in ("cowrie", "twisted"):
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            keep.append(node)
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in names:
            keep.append(node)
    found = {n.name for n in keep if isinstance(n, (ast.ClassDef, ast.FunctionDef))}
    if found != set(names):
        raise AssertionError(f"missing in patched source: {sorted(set(names) - found)}")
    exec(compile(ast.Module(keep, []), "<patched>", "exec"), namespace)  # noqa: S102
    return namespace


CPUINFO = b"".join(b"processor\t: %d\nmodel name\t: Intel(R) Xeon(R)\n\n" % n for n in range(4))
MEMINFO = b"MemTotal:        8023548 kB\nMemFree:         1200000 kB\n"


class FakeFs:
    def __init__(self, files, dirs=("/", "/proc", "/root")):
        self.files = files
        self.dirs = set(dirs)

    def resolve_path(self, path, cwd):
        import posixpath
        return posixpath.normpath(posixpath.join(cwd, path))

    def isdir(self, path):
        return path in self.dirs

    def file_contents(self, path):
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]


class FakeCommand:
    """The parts of cowrie.shell.command.HoneyPotCommand that Command_grep uses."""

    def __init__(self, args, input_data=None, files=None, cwd="/root"):
        self.args = list(args)
        self.input_data = input_data
        self.fs = FakeFs({"/proc/cpuinfo": CPUINFO, "/proc/meminfo": MEMINFO} if files is None else files)
        self.cwd = cwd
        self.out = bytearray()
        self.err = ""
        self.code = "running"

        class Events:
            def dispatch(self, *args, **kwargs):
                pass

        class Protocol:
            events = Events()
            terminal = object()

        self.protocol = Protocol()

    def writeBytes(self, data):
        self.out += data

    def write(self, text):
        self.out += text.encode("utf-8")

    def errorWrite(self, text):
        self.err += text

    def exit(self, code=None):
        self.code = code

    def check_arguments(self, application, args):
        # verbatim logic of HoneyPotCommand.check_arguments in v3.1.1
        files = []
        for arg in args:
            path = self.fs.resolve_path(arg, self.cwd)
            if self.fs.isdir(path):
                self.errorWrite(f"{application}: error reading `{arg}': Is a directory\n")
                continue
            files.append(path)
        return files


class FakeConfig:
    def __init__(self, values):
        self.values = values

    def get(self, section, option, fallback=None):
        return self.values.get((section, option), fallback)



class PatchTest(unittest.TestCase):
    def patched(self):
        root = tmpdir(self) / "cowrie"
        shutil.copytree(FIXTURE, root)
        subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(root)],
                       check=True, capture_output=True, text=True)
        return root

    def test_applies_to_v3_1_1(self):
        protocol = (self.patched() / "src/cowrie/shell/protocol.py").read_text()
        self.assertIn("skip_python_commands", protocol)
        self.assertIn("self.fs.resolve_path(cmd, cwd)", protocol)
        self.assertIn("for x in paths if x", protocol)
        self.assertNotIn("self.cwd", protocol)
        self.assertIn("if path in self.commands and not skip_python_command(path):", protocol)

    def test_ssh_uname_is_persona_driven(self):
        ssh = (self.patched() / "src/cowrie/commands/ssh.py").read_text()
        self.assertNotIn("2.6.26-2-686", ssh)
        self.assertIn('CowrieConfig.get("shell", "kernel_version"', ssh)

    def test_netstat_and_service_lose_upstream_markers(self):
        root = self.patched()
        netstat = (root / "src/cowrie/commands/netstat.py").read_text()
        self.assertNotIn("com/ubuntu/upstart", netstat)
        self.assertNotIn("Phil Blundell", netstat)
        service = (root / "src/cowrie/commands/service.py").read_text()
        self.assertNotIn("whoopsie", service)
        self.assertIn("for line in output:", service)

    def test_free_reads_the_emulated_meminfo(self):
        free = (self.patched() / "src/cowrie/commands/free.py").read_text()
        self.assertNotIn('open("/proc/meminfo")', free)
        get_free_stats = method(free, "Command_free", "get_free_stats")
        meminfo = (b"MemTotal:          4028248 kB\nMemFree:           1200000 kB\n"
                   b"MemAvailable:      2500000 kB\nBuffers:             80000 kB\n"
                   b"Cached:            1300000 kB\nSwapCached:              0 kB\n"
                   b"SwapTotal:               0 kB\nSwapFree:                0 kB\n"
                   b"Shmem:               12000 kB\n")
        asked = []

        class FakeFs:
            def file_contents(self, path):
                asked.append(path)
                return meminfo

        class Command:
            fs = FakeFs()

        stats = get_free_stats(Command())
        self.assertEqual(asked, ["/proc/meminfo"])
        self.assertEqual(stats, {"MemTotal": 4028248, "MemFree": 1200000, "MemAvailable": 2500000,
                                 "Buffers": 80000, "Cached": 1300000, "SwapTotal": 0, "SwapFree": 0,
                                 "Shmem": 12000})

    def test_free_without_meminfo_prints_nothing(self):
        free = (self.patched() / "src/cowrie/commands/free.py").read_text()
        get_free_stats = method(free, "Command_free", "get_free_stats")

        class FakeFs:
            def file_contents(self, path):
                raise FileNotFoundError(path)

        class Command:
            fs = FakeFs()

        self.assertEqual(get_free_stats(Command()), {})

    def test_telnet_without_issue_net_logs_no_traceback(self):
        # routers and cameras have no /etc/issue.net: an empty banner, as upstream, but no failure
        factory = (self.patched() / "src/cowrie/telnet/factory.py").read_text()
        self.assertNotIn("self._log.failure", factory)
        self.assertIn('self._log.info("No /etc/issue.net, empty telnet banner")', factory)
        self.assertIn('self.banner = b""', factory)

    def grep(self, *args, **kwargs):
        fs = (self.patched() / "src/cowrie/commands/fs.py").read_text()
        command_grep = module_parts(fs, ["Command_grep"], {"HoneyPotCommand": FakeCommand})["Command_grep"]
        command = command_grep(args, **kwargs)
        command.start()
        return command

    def test_grep_c_counts_the_matching_lines(self):
        # miner bots ask `grep -c processor /proc/cpuinfo`; v3.1.1 printed the lines
        done = self.grep("-c", "^processor", "/proc/cpuinfo")
        self.assertEqual((bytes(done.out), done.err, done.code), (b"4\n", "", 0))
        done = self.grep("-c", "processor", "/proc/cpuinfo")
        self.assertEqual(bytes(done.out), b"4\n")

    def test_grep_c_without_a_match_prints_0_and_fails(self):
        done = self.grep("-c", "bogomips", "/proc/meminfo")
        self.assertEqual((bytes(done.out), done.code), (b"0\n", 1))

    def test_grep_c_names_each_file_as_given(self):
        done = self.grep("-c", "processor", "/proc/cpuinfo", "/proc/meminfo")
        self.assertEqual((bytes(done.out), done.code), (b"/proc/cpuinfo:4\n/proc/meminfo:0\n", 0))
        done = self.grep("-c", "MemTotal", "cpuinfo", "meminfo", cwd="/proc")
        self.assertEqual(bytes(done.out), b"cpuinfo:0\nmeminfo:1\n")

    def test_grep_c_skips_missing_files_and_directories(self):
        done = self.grep("-c", "processor", "/proc/cpuinfo", "/nope", "/proc")
        self.assertEqual(bytes(done.out), b"/proc/cpuinfo:4\n")
        self.assertIn("grep: /nope: No such file or directory", done.err)
        self.assertIn("Is a directory", done.err)

    def test_grep_c_counts_a_pipe(self):
        done = self.grep("-c", "processor", input_data=CPUINFO)
        self.assertEqual((bytes(done.out), done.code), (b"4\n", 0))

    def test_grep_c_counts_interactive_stdin_at_eof(self):
        done = self.grep("-c", "a")
        self.assertEqual(done.code, "running")
        for line in ("abc", "xyz", "bar"):
            done.lineReceived(line)
        self.assertEqual(bytes(done.out), b"")
        done.eofReceived()
        self.assertEqual((bytes(done.out), done.code), (b"2\n", 0))

    def test_grep_c_stops_at_max_count_per_file(self):
        done = self.grep("-c", "-m", "2", "processor", "/proc/cpuinfo")
        self.assertEqual(bytes(done.out), b"2\n")
        done = self.grep("-m1", "-c", "processor", "/proc/cpuinfo", "/proc/cpuinfo")
        self.assertEqual(bytes(done.out), b"/proc/cpuinfo:1\n/proc/cpuinfo:1\n")

    def test_grep_counts_no_line_after_the_last_newline(self):
        done = self.grep("-c", "", "/proc/meminfo")
        self.assertEqual(bytes(done.out), b"2\n")
        done = self.grep("^", "/proc/meminfo")
        self.assertEqual(bytes(done.out), MEMINFO)

    def test_grep_without_c_still_prints_the_lines(self):
        done = self.grep("processor", "/proc/cpuinfo")
        self.assertEqual(bytes(done.out), b"".join(b"processor\t: %d\n" % n for n in range(4)))
        self.assertEqual(done.code, 0)
        done = self.grep("-m", "1", "processor", input_data=CPUINFO)
        self.assertEqual(bytes(done.out), b"processor\t: 0\n")

    def banner(self, issue, os_release=None, config=None):
        factory = (self.patched() / "src/cowrie/telnet/factory.py").read_text()
        values = {("honeypot", "hostname"): "build39.corp.local",
                  ("shell", "kernel_version"): "5.14.0-427.13.1.el9_4.x86_64",
                  ("shell", "kernel_build_string"): "#1 SMP PREEMPT_DYNAMIC Wed Apr 10 10:29:16 EDT 2024",
                  ("shell", "hardware_platform"): "x86_64"}
        files = {} if os_release is None else {"etc/os-release": os_release}
        asked = []

        def read_honeyfs_bytes(relpath):
            asked.append(relpath)
            if relpath not in files:
                raise FileNotFoundError(relpath)
            return files[relpath]

        namespace = module_parts(factory, ["tpot_expand_issue", "tpot_os_release"],
                                 {"CowrieConfig": FakeConfig(values if config is None else config),
                                  "read_honeyfs_bytes": read_honeyfs_bytes})
        return namespace["tpot_expand_issue"](issue), asked

    RHEL_OS_RELEASE = (b'NAME="Red Hat Enterprise Linux"\nVERSION="9.4 (Plow)"\nID="rhel"\n'
                       b'PRETTY_NAME="Red Hat Enterprise Linux 9.4 (Plow)"\nANSI_COLOR="0;31"\n')

    def test_telnet_banner_expands_the_issue_net_escapes(self):
        text, asked = self.banner("\\S\nKernel \\r on an \\m\n", self.RHEL_OS_RELEASE)
        self.assertEqual(text, "Red Hat Enterprise Linux 9.4 (Plow)\n"
                               "Kernel 5.14.0-427.13.1.el9_4.x86_64 on an x86_64\n")
        self.assertEqual(asked, ["etc/os-release"])

    def test_telnet_banner_knows_the_agetty_escapes(self):
        text, _ = self.banner("\\s|\\n|\\v|\\l|\\\\|\\S{ID}|\\S{NOPE}|\\d \\t \\O \\4|\\",
                              self.RHEL_OS_RELEASE)
        self.assertEqual(text, "Linux|build39.corp.local|#1 SMP PREEMPT_DYNAMIC Wed Apr 10 10:29:16 EDT 2024"
                               "|pts/0|\\|rhel||\\d \\t \\O \\4|\\")

    def test_telnet_banner_without_os_release_says_linux(self):
        text, asked = self.banner("\\S \\r", None)
        self.assertEqual(text, "Linux 5.14.0-427.13.1.el9_4.x86_64")
        self.assertEqual(asked, ["etc/os-release", "usr/lib/os-release"])
        text, _ = self.banner("\\S", b'NAME="CentOS Linux"\n')
        self.assertEqual(text, "Linux")

    def test_telnet_banner_without_escapes_reads_nothing(self):
        text, asked = self.banner("Ubuntu 24.04.4 LTS\n", self.RHEL_OS_RELEASE)
        self.assertEqual((text, asked), ("Ubuntu 24.04.4 LTS\n", []))

    def test_telnet_banner_is_expanded_once_at_start(self):
        factory = (self.patched() / "src/cowrie/telnet/factory.py").read_text()
        self.assertIn("from cowrie.core.config import CowrieConfig", factory)
        self.assertIn('tpot_expand_issue(\n                read_honeyfs_bytes("etc/issue.net")', factory)

    def test_fails_loudly_on_unknown_upstream(self):
        cases = (("src/cowrie/shell/protocol.py", "for x in paths if x", "for x in paths"),
                 ("src/cowrie/commands/free.py", 'open("/proc/meminfo")', 'open("/proc/meminfo", "rb")'),
                 ("src/cowrie/telnet/factory.py", "Failed to load /etc/issue.net", "No /etc/issue.net"),
                 ("src/cowrie/commands/service.py", "        for line in output:", "        for row in output:"),
                 ("src/cowrie/commands/fs.py", "                self.writeBytes(line + b\"\\n\")",
                  "                self.write(line)"),
                 ("src/cowrie/commands/fs.py", "            for pname in self.check_arguments(\"grep\", files):",
                  "            for pname in files:"),
                 ("src/cowrie/commands/fs.py", "            self.grep_application(self.input_data, self.match)\n",
                  "            self.grep_application(self.input_data, match=self.match)\n"),
                 ("src/cowrie/commands/fs.py", "            if opt == \"-h\":", "            if opt in (\"-h\",):"),
                 ("src/cowrie/commands/fs.py", "self.grep_application(line.encode(\"utf8\"), self.match)",
                  "self.grep_application(line.encode(), self.match)"),
                 ("src/cowrie/commands/fs.py", "self.grep_application(f.read(), self.match)\n        self.exit(",
                  "self.grep_application(f.read(), self.match)\n        return self.exit("),
                 ("src/cowrie/commands/fs.py", "            self.grep_application(contents, match)\n",
                  "            self.grep_application(contents, match=match)\n"),
                 ("src/cowrie/telnet/factory.py", "from cowrie.shell.honeyfs import read_honeyfs_bytes",
                  "from cowrie.shell import honeyfs"),
                 ("src/cowrie/telnet/factory.py", "\nimport time\n", "\nimport time as clock\n"))
        for rel, old, new in cases:
            with self.subTest(rel=rel, needle=old):
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

    def test_fails_loudly_on_a_needle_found_twice(self):
        root = tmpdir(self) / "cowrie"
        shutil.copytree(FIXTURE, root)
        path = root / "src/cowrie/commands/fs.py"
        path.write_text(path.read_text() + '#            self.grep_application(line.encode("utf8"), self.match)\n')
        done = subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(root)],
                              capture_output=True, text=True)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("more than once", done.stderr)


if __name__ == "__main__":
    unittest.main()
