import ast
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from tests.helpers import DIST, base_tree, tmpdir
from tpot_personas import tree

FIXTURE = Path(__file__).parent / "fixtures" / "v3.1.1"
PATCH = DIST / "patch_cowrie_persona_support.py"


def method(source, cls, name, namespace=None):
    """The function `name` of class `cls` in source, compiled on its own (with the
    module globals of namespace)."""
    for node in ast.parse(source).body:
        if isinstance(node, ast.ClassDef) and node.name == cls:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == name:
                    namespace = dict(namespace or {})
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
        if isinstance(node, ast.Import) and any(a.name.split(".")[0] in ("cowrie", "twisted") for a in node.names):
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

    def has_option(self, section, option):
        return (section, option) in self.values



def module_all(source, namespace):
    """The whole patched module without its cowrie / twisted imports, run in namespace."""
    keep = [node for node in ast.parse(source).body
            if not (isinstance(node, ast.ImportFrom) and node.module
                    and node.module.split(".")[0] in ("cowrie", "twisted"))]
    exec(compile(ast.Module(keep, []), "<patched>", "exec"), namespace)  # noqa: S102
    return namespace


class TreeFs:
    """The parts of Cowrie's HoneyPotFilesystem getCommand uses, on a tpot_personas tree."""

    def __init__(self, root):
        self.fs = root

    def resolve_path(self, path, cwd):
        import posixpath
        return posixpath.normpath(posixpath.join(cwd, path))

    def getfile(self, path, follow_symlinks=True):
        node = tree.find_node(self.fs, path)
        if follow_symlinks and node is not None and node[tree.A_TYPE] == tree.T_LINK:
            node = tree.find_node(self.fs, node[tree.A_TARGET])
        return node

    def exists(self, path):
        return self.getfile(path) is not None

    def isfile(self, path):
        node = self.getfile(path)
        return node is not None and node[tree.A_TYPE] == tree.T_FILE


class FsModule:
    A_NAME, A_TYPE, A_CONTENTS, A_TARGET = tree.A_NAME, tree.A_TYPE, tree.A_CONTENTS, tree.A_TARGET
    T_LINK, T_DIR, T_FILE = tree.T_LINK, tree.T_DIR, tree.T_FILE


PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin".split(":")


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
        self.assertIn("if spelling in self.commands and not skip_python_command(spelling):", protocol)
        # the smoke test looks for upstream's txtcmds_path lookup: it stays, the spellings follow it
        self.assertIn("operator_path = Path(txtcmds_path) / relpath", protocol)

    def lookup(self, txtcmds, commands, skip=""):
        """getCommand of the patched protocol.py on base_tree() (bin -> usr/bin) with these
        txtcmds files and Python commands; returns a function name -> what it found."""
        root = tmpdir(self)
        for rel, text in txtcmds.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text)
        protocol = (self.patched() / "src/cowrie/shell/protocol.py").read_text()

        def read_data_bytes(*parts):
            raise FileNotFoundError("/".join(parts))

        config = FakeConfig({("honeypot", "txtcmds_path"): str(root), ("shell", "skip_python_commands"): skip})
        self.config = config
        skip_set = module_parts(protocol, ["tpot_skip_python_commands"], {"CowrieConfig": config})
        get_command = method(protocol, "HoneyPotBaseProtocol", "getCommand",
                             {"CowrieConfig": config, "Path": Path, "fs": FsModule, "read_data_bytes": read_data_bytes,
                              "tpot_skip_python_commands": skip_set["tpot_skip_python_commands"]})

        class Protocol:
            fs = TreeFs(base_tree())

            def txtcmd(self, txt):
                return ("txtcmd", txt.decode())

            def scriptcmd(self, path, name):
                return ("script", path)

            class _log:
                @staticmethod
                def info(*args, **kwargs):
                    pass

        Protocol.commands = commands
        protocol_obj = Protocol()
        return lambda name, cwd="/root": get_command(protocol_obj, name, PATH, cwd)

    def test_txtcmd_answers_every_spelling_through_the_bin_link(self):
        # `df` finds /usr/bin/df on PATH (usr/bin before bin), the txtcmd is bin/df
        find = self.lookup({"bin/df": "df of the persona\n", "usr/bin/top": "top of the persona\n"}, {})
        for name in ("df", "/bin/df", "/usr/bin/df"):
            self.assertEqual(find(name), ("txtcmd", "df of the persona\n"), name)
        for name in ("top", "/bin/top", "/usr/bin/top"):
            self.assertEqual(find(name), ("txtcmd", "top of the persona\n"), name)
        # a program without txtcmd or command stays Cowrie's script / binary
        self.assertEqual(find("uname"), ("script", "/usr/bin/uname"))
        self.assertIsNone(find("nothere"))

    def test_python_command_answers_every_spelling(self):
        # a command found by path is looked up as /usr/bin/x and /bin/x (Cowrie registers /bin/busybox)
        find = self.lookup({}, {"/bin/top": "top"})
        self.assertEqual(find("/usr/bin/top"), "top")
        self.assertEqual(find("top"), "top")

    def test_skip_list_is_parsed_once(self):
        find = self.lookup({}, {"/bin/top": "top", "top": "top"}, skip="top,gcc")
        asked = []
        get = self.config.get
        self.config.get = lambda section, option, fallback=None: (asked.append(option), get(section, option, fallback))[1]
        for _ in range(3):
            self.assertEqual(find("top"), ("script", "/usr/bin/top"))
            self.assertIsNone(find("nothere"))
        self.assertLessEqual(asked.count("skip_python_commands"), 1)

    def test_skipped_python_command_stays_skipped_in_every_spelling(self):
        find = self.lookup({}, {"/bin/top": "top", "top": "top"}, skip="top")
        self.assertEqual(find("top"), ("script", "/usr/bin/top"))
        self.assertEqual(find("/bin/top"), ("script", "/bin/top"))

    def busybox(self, args, help_path=None):
        source = (self.patched() / "src/cowrie/commands/busybox.py").read_text()
        values = {("shell", "busybox_help"): str(help_path)} if help_path else {}
        dispatched, executed = [], []

        class Pipe:
            targets = ()

            def __init__(self, *args, **kwargs):
                self.stdin_from_pipe = False

        class BusyboxBase(FakeCommand):
            environ = {"PATH": ":".join(PATH)}
            user = None
            pp = Pipe()

            def exec_command(self, pp, cmdclass, *args):
                executed.append((cmdclass, args))

        namespace = module_all(source, {"HoneyPotCommand": BusyboxBase, "PipeProtocol": Pipe,
                                        "CowrieConfig": FakeConfig(values)})
        command = namespace["Command_busybox"](args)

        class Events:
            def dispatch(self, *args, **kwargs):
                dispatched.append(kwargs)

        class Protocol:
            events = Events()

            def getCommand(self, cmd, paths, cwd):
                return {"wget": "Command_wget", "cat": "Command_cat"}.get(cmd)

        command.protocol = Protocol()
        command.start()
        return command, dispatched, executed

    def test_busybox_runs_its_applets(self):
        command, dispatched, executed = self.busybox(["wget", "http://127.0.0.1:1/x"])
        self.assertEqual(executed, [("Command_wget", ("http://127.0.0.1:1/x",))])
        self.assertEqual(dispatched, [{"input": "wget http://127.0.0.1:1/x"}])

    def test_busybox_names_an_unknown_applet(self):
        command, _, executed = self.busybox(["ECCHI"])
        self.assertEqual((bytes(command.out), executed), (b"ECCHI: applet not found\n", []))

    def test_busybox_prints_the_help_of_the_persona(self):
        help_path = tmpdir(self) / "busybox-help.txt"
        help_path.write_text("BusyBox v1.19.2 (2025-03-28 14:13:40 CST) multi-call binary.\nhelp\n")
        for args in ([], ["--help"]):
            command, _, executed = self.busybox(args, help_path)
            self.assertEqual(bytes(command.out), help_path.read_bytes(), args)
            self.assertEqual((command.err, executed), ("", []))

    def test_busybox_without_the_key_keeps_upstream_help(self):
        command, _, _ = self.busybox([])
        self.assertIn("BusyBox v1.20.2 (Debian 1:1.20.0-7) multi-call binary.", command.err)

    def command(self, module, cls, values, args=()):
        source = (self.patched() / f"src/cowrie/commands/{module}.py").read_text()
        namespace = module_all(source, {"HoneyPotCommand": FakeCommand, "CowrieConfig": FakeConfig(values)})
        return namespace, namespace[cls](list(args))

    def test_perl_v_names_the_persona_perl(self):
        values = {("shell", "perl_version"): "5.36.0", ("shell", "perl_archname"): "x86_64-linux-gnu-thread-multi",
                  ("shell", "perl_copyright"): "2022"}
        _, perl = self.command("perl", "Command_perl", values)
        perl.version()
        text = bytes(perl.out).decode()
        self.assertIn("This is perl 5, version 36, subversion 0 (v5.36.0) built for x86_64-linux-gnu-thread-multi\n", text)
        self.assertIn("Copyright 1987-2022, Larry Wall\n", text)
        self.assertNotIn("5.14.2", text)
        _, perl = self.command("perl", "Command_perl", {})
        perl.version()
        self.assertIn("(v5.14.2) built for x86_64-linux-thread-multi", bytes(perl.out).decode())

    def test_python3_is_a_command_with_the_persona_version(self):
        namespace, python = self.command("python", "Command_python", {("shell", "python_version"): "3.12.3"})
        self.assertIs(namespace["commands"]["python3"], namespace["Command_python"])
        self.assertIs(namespace["commands"]["/usr/bin/python3"], namespace["Command_python"])
        python.version()
        self.assertEqual((bytes(python.out), python.err), (b"Python 3.12.3\n", ""))
        _, python = self.command("python", "Command_python", {("shell", "python_version"): "2.7.5"})
        python.version()
        # Python 2 prints its version on stderr
        self.assertEqual((bytes(python.out), python.err), (b"", "Python 2.7.5\n"))

    def test_git_version_of_the_persona(self):
        _, git = self.command("git", "Command_git", {("shell", "git_version"): "2.43.0"}, ["--version"])
        git.call()
        self.assertEqual(bytes(git.out), b"git version 2.43.0\n")

    def twisted_command(self, module, cls, values, args, uid=0):
        """A command with inlineCallbacks: the generators run through at once, sleep returns."""
        def inline_callbacks(function):
            def run(*a, **k):
                result = function(*a, **k)
                if hasattr(result, "send"):
                    for _ in result:
                        pass
            return run

        source = (self.patched() / f"src/cowrie/commands/{module}.py").read_text()
        namespace = module_all(source, {"HoneyPotCommand": FakeCommand, "CowrieConfig": FakeConfig(values),
                                        "inlineCallbacks": inline_callbacks, "defer": None, "reactor": None,
                                        "Logger": lambda: type("L", (), {"info": lambda *a, **k: None})()})
        command = namespace[cls](list(args))
        command.user = {"uid": uid, "gid": uid}
        command.sleep = lambda *a, **k: None

        class Fs:
            def mkfile(self, *a, **k):
                pass

        command.fs = Fs()
        command.protocol.commands = {}
        command.start()
        return namespace, command

    APT = {("shell", "apt_version"): "2.8.3", ("shell", "deb_arch"): "amd64",
           ("shell", "apt_sources"): "http://archive.ubuntu.com/ubuntu noble,http://archive.ubuntu.com/ubuntu "
                                     "noble-updates,http://security.ubuntu.com/ubuntu noble-security"}

    def test_apt_version_of_the_persona(self):
        _, apt = self.twisted_command("apt", "Command_aptget", self.APT, ["-v"])
        self.assertTrue(bytes(apt.out).startswith(b"apt 2.8.3 (amd64)\nSupported modules:"), bytes(apt.out)[:60])
        _, apt = self.twisted_command("apt", "Command_aptget", {}, ["-v"])
        self.assertTrue(bytes(apt.out).startswith(b"apt 1.0.9.8.1 for amd64 compiled on Jun 10 2015"))

    def test_apt_update_as_root_fetches_the_persona_sources(self):
        _, apt = self.twisted_command("apt", "Command_aptget", self.APT, ["update"])
        self.assertEqual(bytes(apt.out).decode(), "Hit:1 http://archive.ubuntu.com/ubuntu noble InRelease\n"
                         "Hit:2 http://archive.ubuntu.com/ubuntu noble-updates InRelease\n"
                         "Hit:3 http://security.ubuntu.com/ubuntu noble-security InRelease\n"
                         "Reading package lists... Done\n")
        _, apt = self.twisted_command("apt", "Command_aptget", self.APT, ["update"], uid=1000)
        self.assertIn("Could not open lock file /var/lib/apt/lists/lock", apt.err)

    def test_apt_install_takes_no_option_as_a_package(self):
        _, apt = self.twisted_command("apt", "Command_aptget", self.APT, ["install", "-y", "curl"])
        text = bytes(apt.out).decode()
        self.assertIn("The following NEW packages will be installed:\n  curl \n", text)
        self.assertIn("Get:1 http://archive.ubuntu.com/ubuntu noble/main amd64 curl ", text)
        self.assertIn("_amd64.deb) ...", text)
        self.assertNotIn("i386", text)
        self.assertNotIn("ftp.debian.org", text)
        _, apt = self.twisted_command("apt", "Command_aptget", self.APT, ["-y", "install", "wget"])
        self.assertIn("  wget \n", bytes(apt.out).decode())

    YUM = {("shell", "yum_plugins"): "fastestmirror"}

    def test_yum_install_y_of_centos(self):
        _, yum = self.twisted_command("yum", "Command_yum", self.YUM, ["install", "-y", "curl"])
        text = bytes(yum.out).decode()
        self.assertIn("Loaded plugins: fastestmirror\n", text)
        self.assertIn("---> Package curl.", text)
        self.assertNotIn("Package y.", text)
        self.assertNotIn("priority protections", text)
        self.assertNotIn("Kernel Module Plugin", text)
        self.assertNotIn("Is this ok", text)
        self.assertIn("Complete!\n", text)
        _, yum = self.twisted_command("yum", "Command_yum", self.YUM, ["install", "curl"])
        self.assertTrue(bytes(yum.out).decode().endswith("Is this ok [y/d/N]: "))

    def test_yum_update_as_root(self):
        _, yum = self.twisted_command("yum", "Command_yum", self.YUM, ["-y", "update"])
        self.assertEqual(bytes(yum.out).decode(), "Loaded plugins: fastestmirror\n"
                         "Loading mirror speeds from cached hostfile\nNo packages marked for update\n")
        _, yum = self.twisted_command("yum", "Command_yum", self.YUM, ["update"], uid=1000)
        self.assertIn("You need to be root to perform this command.", yum.err)

    def test_sudo_version_of_the_persona(self):
        values = {("shell", "sudo_version"): "1.9.15p5", ("shell", "sudo_grammar"): "50"}
        namespace, sudo = self.command("sudo", "Command_sudo", values)
        sudo.version()
        self.assertEqual(bytes(sudo.out).decode(), "Sudo version 1.9.15p5\nSudoers policy plugin version 1.9.15p5\n"
                         "Sudoers file grammar version 50\nSudoers I/O plugin version 1.9.15p5\n"
                         "Sudoers audit plugin version 1.9.15p5\n")
        self.assertIs(namespace["commands"]["/usr/bin/sudo"], namespace["Command_sudo"])
        _, sudo = self.command("sudo", "Command_sudo", {("shell", "sudo_version"): "1.8.23",
                                                        ("shell", "sudo_grammar"): "46"})
        sudo.version()
        self.assertNotIn("audit", bytes(sudo.out).decode())

    def test_service_status_all_of_the_persona(self):
        path = tmpdir(self) / "service-status.txt"
        path.write_text(" [ + ]  cron\n [ - ]  hwclock.sh\n")
        _, service = self.command("service", "Command_service", {("shell", "service_status_all"): str(path)})
        service.status_all()
        self.assertEqual(bytes(service.out), b" [ + ]  cron\n [ - ]  hwclock.sh\n")
        _, service = self.command("service", "Command_service", {})
        service.status_all()
        self.assertIn(b" [ + ]  ssh\n", bytes(service.out))
        self.assertNotIn(b"lightdm", bytes(service.out))

    def test_which_prints_the_first_match_and_fails_for_a_missing_name(self):
        _, which = self.command("which", "Command_which", {}, ["python3", "nothere", "perl"])
        which.environ = {"PATH": ":".join(PATH)}
        which.fs = TreeFs(base_tree())
        which.exit_code = 0
        which.call()
        # bin -> usr/bin: /usr/bin/python3 and /bin/python3 are one file, which names the first
        self.assertEqual((bytes(which.out), which.exit_code), (b"/usr/bin/python3\n/usr/bin/perl\n", 1))
        _, which = self.command("which", "Command_which", {}, ["perl"])
        which.environ, which.fs, which.exit_code = {"PATH": ":".join(PATH)}, TreeFs(base_tree()), 0
        which.call()
        self.assertEqual((bytes(which.out), which.exit_code), (b"/usr/bin/perl\n", 0))

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

    PS = (b"USER       PID %CPU %MEM    VSZ   RSS TTY      STAT START   TIME COMMAND\n"
          b"root         1  0.0  0.1 167744 11788 ?        Ss   Oct09   0:04 /sbin/init\n"
          b"root      4211  0.0  0.0   6480  2280 pts/0    S+   10:02   0:00 grep --color=auto xmrig\n")

    def test_grep_v_inverts(self):
        # ps aux | grep -v grep
        done = self.grep("-v", "grep", input_data=self.PS)
        self.assertEqual((bytes(done.out), done.code), (self.PS.rsplit(b"root      4211", 1)[0], 0))
        done = self.grep("-vc", "processor", "/proc/cpuinfo")
        self.assertEqual((bytes(done.out), done.code), (b"8\n", 0))
        done = self.grep("-v", "-m", "1", "processor", "/proc/cpuinfo")
        self.assertEqual(bytes(done.out), b"model name\t: Intel(R) Xeon(R)\n")
        done = self.grep("-v", "", "/proc/meminfo")
        self.assertEqual((bytes(done.out), done.code), (b"", 1))

    def test_grep_i_ignores_case(self):
        done = self.grep("-ci", "PROCESSOR", "/proc/cpuinfo")
        self.assertEqual((bytes(done.out), done.code), (b"4\n", 0))
        done = self.grep("-i", "memtotal", "/proc/meminfo")
        self.assertEqual(bytes(done.out), b"MemTotal:        8023548 kB\n")
        done = self.grep("-vi", "MODEL NAME", "/proc/meminfo")
        self.assertEqual(bytes(done.out), MEMINFO)
        done = self.grep("-c", "PROCESSOR", "/proc/cpuinfo")
        self.assertEqual((bytes(done.out), done.code), (b"0\n", 1))

    def test_grep_q_only_sets_the_status(self):
        # grep -qi "model name" /proc/cpuinfo && echo yes
        done = self.grep("-qi", "model name", "/proc/cpuinfo")
        self.assertEqual((bytes(done.out), done.err, done.code), (b"", "", 0))
        done = self.grep("-q", "model name", "/proc/meminfo")
        self.assertEqual((bytes(done.out), done.code), (b"", 1))
        done = self.grep("-qc", "processor", "/proc/cpuinfo", "/proc/meminfo")
        self.assertEqual((bytes(done.out), done.code), (b"", 0))
        done = self.grep("-q", "init", input_data=self.PS)
        self.assertEqual((bytes(done.out), done.code), (b"", 0))
        done = self.grep("-q", "a")
        done.lineReceived("abc")
        done.eofReceived()
        self.assertEqual((bytes(done.out), done.code), (b"", 0))

    def test_grep_exit_status_2_on_errors(self):
        done = self.grep("processor", "/nope")
        self.assertEqual((done.err, done.code), ("grep: /nope: No such file or directory\n", 2))
        # an error wins over a match, unless -q found one
        done = self.grep("processor", "/proc/cpuinfo", "/nope")
        self.assertEqual(done.code, 2)
        done = self.grep("-q", "processor", "/nope", "/proc/cpuinfo")
        self.assertEqual((done.code, done.err), (0, "grep: /nope: No such file or directory\n"))
        done = self.grep("-q", "nothing", "/proc/cpuinfo", "/nope")
        self.assertEqual(done.code, 2)
        done = self.grep("processor", "/proc")
        self.assertEqual(done.code, 2)
        done = self.grep("-c", "MemTotal", "nope", cwd="/proc")
        self.assertEqual((bytes(done.out), done.err, done.code), (b"", "grep: nope: No such file or directory\n", 2))
        done = self.grep("-K", "x", "/proc/cpuinfo")
        self.assertEqual(done.code, 2)
        self.assertIn("grep: invalid option -- K", done.err)

    def prompt(self, cwd, user="root", uid=0, home="/root", style="redhat", hostname="build39.corp.local",
               config=None):
        import posixpath
        honeypot = (self.patched() / "src/cowrie/shell/honeypot.py").read_text()
        values = {} if style is None else {("shell", "prompt_style"): style}
        show_prompt = method(honeypot, "HoneyPotShell", "showPrompt",
                             {"CowrieConfig": FakeConfig(values if config is None else config),
                              "posixpath": posixpath})
        written = []

        class Terminal:
            def write(self, data):
                written.append(data)

        class Protocol:
            terminal = Terminal()
            ps = None

        Protocol.hostname = hostname

        class Shell:
            interactive = True
            protocol = Protocol()

        shell = Shell()
        shell.user = {"username": user, "uid": uid, "home": home}
        shell.cwd = cwd
        show_prompt(shell)
        self.assertEqual(shell.protocol.ps, (written[0], b"> "))
        return written[0].decode("utf-8")

    def test_redhat_prompt(self):
        # bash PS1 of RHEL / CentOS: [\u@\h \W]\$ (short host name, basename of the cwd)
        self.assertEqual(self.prompt("/root"), "[root@build39 ~]# ")
        self.assertEqual(self.prompt("/"), "[root@build39 /]# ")
        self.assertEqual(self.prompt("/var/log"), "[root@build39 log]# ")
        self.assertEqual(self.prompt("/root/bin"), "[root@build39 bin]# ")
        self.assertEqual(self.prompt("/home/cloud-user", "cloud-user", 1000, "/home/cloud-user"),
                         "[cloud-user@build39 ~]$ ")
        self.assertEqual(self.prompt("/tmp", "cloud-user", 1000, "/home/cloud-user", hostname="rhel9-web12"),
                         "[cloud-user@rhel9-web12 tmp]$ ")

    def test_debian_prompt_stays_upstream(self):
        for style in (None, "debian", "anything else"):
            with self.subTest(style=style):
                self.assertEqual(self.prompt("/root", style=style), "root@build39.corp.local:~# ")
                self.assertEqual(self.prompt("/var/log", style=style), "root@build39.corp.local:/var/log# ")
                self.assertEqual(self.prompt("/home/pi/src", "pi", 1000, "/home/pi", style=style, hostname="pihole"),
                                 "pi@pihole:~/src$ ")

    def test_configured_prompt_still_wins(self):
        config = {("honeypot", "prompt"): "$", ("shell", "prompt_style"): "redhat"}
        self.assertEqual(self.prompt("/root", config=config), "$ ")

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
                 ("src/cowrie/telnet/factory.py", "\nimport time\n", "\nimport time as clock\n"),
                 ("src/cowrie/commands/fs.py", "        return re.compile(bmatch)\n",
                  "        return re.compile(bmatch, 0)\n"),
                 ("src/cowrie/commands/fs.py", "            if matcher.search(line):\n",
                  "            if matcher.search(line) is not None:\n"),
                 ("src/cowrie/commands/fs.py",
                  "            self.errorWrite(f\"grep: invalid option -- {err.opt}\\n\")\n            self.help()\n",
                  "            self.errorWrite(f\"grep: invalid option -- {err.opt}\\n\")\n"),
                 ("src/cowrie/commands/fs.py",
                  "            self.interactive = True\n            return\n\n        self.exit(0 if",
                  "            self.interactive = True\n            return\n        self.exit(0 if"),
                 ("src/cowrie/shell/honeypot.py",
                  "            prompt = f\"{username}@{self.protocol.hostname}:{cwd}\"\n",
                  "            prompt = f\"{username}@{self.protocol.hostname}:{cwd} \"\n"),
                 ("src/cowrie/shell/protocol.py", "                return self.txtcmd(operator_path.read_bytes())",
                  "                return self.txtcmd(operator_path.read_text())"),
                 ("src/cowrie/commands/busybox.py", "        if len(self.args) == 0:\n",
                  "        if not self.args:\n"),
                 ("src/cowrie/commands/perl.py", "Copyright 1987-2014, Larry Wall", "Copyright 1987-2015, Larry Wall"),
                 ("src/cowrie/commands/python.py", 'ver = "Python 2.7.11+"', 'ver = "Python 2.7.18"'),
                 ("src/cowrie/commands/git.py", 'self.write("git version 2.34.1\\n")',
                  'self.write("git version 2.43.0\\n")'))
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
