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

    def test_fails_loudly_on_unknown_upstream(self):
        cases = (("src/cowrie/shell/protocol.py", "for x in paths if x", "for x in paths"),
                 ("src/cowrie/commands/free.py", 'open("/proc/meminfo")', 'open("/proc/meminfo", "rb")'),
                 ("src/cowrie/telnet/factory.py", "Failed to load /etc/issue.net", "No /etc/issue.net"),
                 ("src/cowrie/commands/service.py", "        for line in output:", "        for row in output:"))
        for rel, old, new in cases:
            with self.subTest(rel):
                root = tmpdir(self) / "cowrie"
                shutil.copytree(FIXTURE, root)
                path = root / rel
                path.write_text(path.read_text().replace(old, new))
                done = subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(root)],
                                      capture_output=True, text=True)
                self.assertNotEqual(done.returncode, 0)
                self.assertIn("Could not find expected text", done.stderr)


if __name__ == "__main__":
    unittest.main()
