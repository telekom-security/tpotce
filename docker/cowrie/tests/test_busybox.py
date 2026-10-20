"""BusyBox of the device personas: the help of `busybox` as each version prints it (fixtures/
busybox_help.json: busybox:1.27.2 / 1.31.1 / 1.36.1 and BusyBox 1.19.4 of the Archer C7 v5 image), the
applets of the Archer C7 firmware and the program folders of each of its images (firmware_images)."""
import json
import re
import unittest
from pathlib import Path

from tests.helpers import base_tree
from tpot_personas import catalog, devices, firmware, render, system, tree
from tpot_personas.identity import draw

HELP = json.loads((Path(__file__).parent / "fixtures" / "busybox_help.json").read_text())["help"]


def applets_of(text):
    body = text.split("Currently defined functions:\n", 1)[1]
    return [name.strip() for name in body.replace("\n", " ").split(",") if name.strip()]


class HelpTest(unittest.TestCase):
    def test_the_help_of_each_version(self):
        for version, text in HELP.items():
            with self.subTest(version=version):
                banner = text.split(" multi-call binary.", 1)[0]
                self.assertEqual(devices.busybox_help(banner, applets_of(text)), text)

    def test_older_and_vendor_versions_take_the_form_of_their_release(self):
        old = devices.busybox_help("BusyBox v1.14.1 (2016-06-28 10:53:08 CST)", ["ash", "cat", "sh"])
        self.assertIn("Copyright (C) 1998-2008 Erik Andersen", old)
        self.assertIn("   or: busybox --list[-full]\n", old)
        self.assertTrue(old.endswith("\tash, cat, sh\n\n"))
        new = devices.busybox_help("BusyBox v1.24.1 (2025-08-18 02:27:40 CST)", ["ash", "cat", "sh"])
        self.assertIn("Usage: busybox [function [arguments]...]\n   or: busybox --list[-full]\n"
                      "   or: busybox --install [-s] [DIR]\n   or: function [arguments]...\n", new)
        self.assertTrue(new.endswith("\tash, cat, sh\n"))


class TplinkTest(unittest.TestCase):
    def test_the_applets_of_the_firmware(self):
        persona = catalog.by_id("tplink-archer-c7")
        for seed in range(3):
            applets = system.busybox_applets(persona, draw(persona, seed))
            self.assertEqual(applets, applets_of(HELP["1.19.4"]))
            for name in ("grep", "awk", "head", "wget", "uname", "which", "dd"):
                self.assertIn(name, applets)
            for name in ("nproc", "netstat", "ip", "history"):
                self.assertNotIn(name, applets)

    def test_the_program_folders_are_those_of_the_image(self):
        persona = catalog.by_id("tplink-archer-c7")
        labels = set()
        for seed in range(12):
            ident = draw(persona, seed)
            if ident.release.label in labels:
                continue
            labels.add(ident.release.label)
            with self.subTest(release=ident.release.label):
                t = render.apply_to_tree(base_tree(), persona, ident, persona.builder(persona, ident))
                found = {}
                for folder in ("bin", "sbin", "usr/bin", "usr/sbin"):
                    node = tree.find_node(t, folder)
                    self.assertEqual(node[tree.A_TYPE], tree.T_DIR, folder)
                    for child in tree.node_children(node):
                        rel = f"{folder}/{child[tree.A_NAME]}"
                        found[rel] = child[tree.A_TARGET] if child[tree.A_TYPE] == tree.T_LINK \
                            else child[tree.A_SIZE]
                # the programs of that image only (ulimit is a builtin of its ash, no program), and the telnetd of
                #   the telnet sessions T-Pot serves (the image has no telnet daemon: devices.SESSION_TELNETD)
                self.assertIsInstance(found.pop("usr/sbin/telnetd"), int)
                self.assertEqual(found, firmware.programs(persona, ident))
                for rel, target in (("usr/bin/wget", "../../bin/busybox"), ("bin/uname", "busybox"), ("bin/grep", "busybox"),
                                    ("usr/bin/head", "../../bin/busybox"), ("usr/bin/awk", "../../bin/busybox"),
                                    ("usr/bin/which", "../../bin/busybox"), ("usr/bin/scp", "../sbin/dropbear")):
                    self.assertEqual(found.get(rel), target, rel)
                self.assertIsInstance(found["usr/sbin/ip"], int)
                self.assertIsNone(tree.find_node(t, "linuxrc"))
                for rel in ("rom", "overlay", "www"):
                    self.assertEqual(tree.find_node(t, rel)[tree.A_TYPE], tree.T_DIR, rel)

    def test_the_shell_answers_only_its_builtins_and_programs(self):
        import configparser
        persona = catalog.by_id("tplink-archer-c7")
        ident = draw(persona, 0)
        cfg = configparser.ConfigParser(interpolation=None)
        cfg.read_string(render.render_cfg(persona, ident, Path("/tmp/r"), Path("/tmp/k")))
        builtins = set(cfg.get("shell", "builtins").split(","))
        self.assertEqual(builtins, set(firmware.TPLINK_ASH_BUILTINS))
        self.assertNotIn("history", builtins)
        for name in ("cd", "echo", "export", "kill", "pwd", "test", "[", "help", "printf", "getopts", "ulimit"):
            self.assertIn(name, builtins)

    def test_the_help_builtin_lists_the_builtins(self):
        # help of the ash of the Archer C7 v5 (220715 image, qemu-mips-static): a txtcmd, no program
        persona = catalog.by_id("tplink-archer-c7")
        ident = draw(persona, 0)
        content = persona.builder(persona, ident)
        self.assertEqual(content.txtcmds["bin/help"], (
            "Built-in commands:\n------------------\n"
            "\t. : [ [[ alias bg break cd chdir command continue echo eval exec\n"
            "\texit export false fg getopts hash help jobs kill let local printf\n"
            "\tpwd read readonly return set shift source test times trap true\n"
            "\ttype ulimit umask unalias unset wait\n\n"))
        t = render.apply_to_tree(base_tree(), persona, ident, content)
        self.assertIsNone(tree.resolve(t, "bin/help"))
        # Cowrie's help (the builtins of bash) is skipped there, so the builtin answers with that text
        import configparser
        cfg = configparser.ConfigParser(interpolation=None)
        cfg.read_string(render.render_cfg(persona, ident, Path("/tmp/r"), Path("/tmp/k")))
        self.assertIn("help", cfg.get("shell", "skip_python_commands").split(","))
        self.assertNotIn("ulimit", cfg.get("shell", "skip_python_commands").split(","))
        # the images have dropbear's scp (usr/bin/scp): Cowrie's scp answers there (an upload with scp -t)
        self.assertNotIn("scp", cfg.get("shell", "skip_python_commands").split(","))

    def test_other_personas_have_no_builtins_key(self):
        import configparser
        for persona in catalog.PERSONAS:
            if persona.id == "tplink-archer-c7":
                continue
            with self.subTest(persona=persona.id):
                cfg = configparser.ConfigParser(interpolation=None)
                cfg.read_string(render.render_cfg(persona, draw(persona, 0), Path("/tmp/r"), Path("/tmp/k")))
                self.assertFalse(cfg.has_option("shell", "builtins"))


class CpuCommandsTest(unittest.TestCase):
    def test_lscpu_and_nproc_only_where_the_system_has_them(self):
        # BusyBox has nproc from 1.27 (coreutils/nproc.c), no lscpu up to 1.36; util-linux has both
        expected = {"dlink-dir859": (False, False), "tplink-archer-c7": (False, False),
                    "zyxel-nas326": (False, False), "openwrt": (False, False), "qnap-qts": (False, False),
                    "ipcam": (False, False), "ubiquiti-edgerouter-x": (True, True), "synology-dsm": (True, True)}
        for pid, (has_lscpu, has_nproc) in expected.items():
            persona = catalog.by_id(pid)
            for seed in range(3):
                ident = draw(persona, seed)
                with self.subTest(persona=pid, seed=seed):
                    content = persona.builder(persona, ident)
                    self.assertEqual("usr/bin/lscpu" in content.txtcmds, has_lscpu)
                    self.assertEqual("usr/bin/nproc" in content.txtcmds, has_nproc)
                    # and no program of Cowrie's tree in their place (it would answer without a txtcmd)
                    t = render.apply_to_tree(base_tree(), persona, ident, content)
                    for folder in ("bin", "sbin", "usr/bin", "usr/sbin"):
                        self.assertEqual(tree.resolve(t, f"{folder}/lscpu") is not None and folder == "usr/bin",
                                         has_lscpu and folder == "usr/bin", (folder, "lscpu"))
                        if not has_nproc:
                            self.assertIsNone(tree.resolve(t, f"{folder}/nproc"), folder)



class StrictLookupTest(unittest.TestCase):
    """getCommand of the patched protocol.py with [shell] builtins: a Python command answers a bare
    name only as a builtin of the shell or as a program found on PATH."""

    def lookup(self, builtins):
        import shutil
        import subprocess
        import sys
        import tempfile

        from tests.test_patch import FIXTURE, PATCH, FakeConfig, FsModule, TreeFs, method, module_parts
        handle = tempfile.TemporaryDirectory()
        self.addCleanup(handle.cleanup)
        root = Path(handle.name) / "cowrie"
        shutil.copytree(FIXTURE, root)
        subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(root)], check=True, capture_output=True)
        source = (root / "src/cowrie/shell/protocol.py").read_text()
        values = {("honeypot", "txtcmds_path"): str(Path(handle.name) / "txtcmds")}
        if builtins is not None:
            values[("shell", "builtins")] = builtins
        config = FakeConfig(values)
        names = {"CowrieConfig": config}
        module_parts(source, ["tpot_skip_python_commands", "tpot_builtins"], names)
        get_command = method(source, "HoneyPotBaseProtocol", "getCommand",
                             {"CowrieConfig": config, "Path": Path, "fs": FsModule,
                              "read_data_bytes": lambda *parts: (_ for _ in ()).throw(FileNotFoundError()), **names})
        persona = catalog.by_id("tplink-archer-c7")
        ident = draw(persona, 0)
        t = render.apply_to_tree(base_tree(), persona, ident, persona.builder(persona, ident))

        class Protocol:
            fs = TreeFs(t)
            commands = {"grep": "py-grep", "cd": "py-cd", "cat": "py-cat", "/bin/cat": "py-cat", "history": "py-history",
                        "uname": "py-uname", "free": "py-free", "opkg": "py-opkg", "nproc": "py-nproc"}

            def txtcmd(self, txt):
                return ("txtcmd", txt)

            def scriptcmd(self, path, name):
                return ("script", path)

        protocol = Protocol()
        # the stand-in text of ash's builtin ulimit
        (Path(handle.name) / "txtcmds" / "bin").mkdir(parents=True)
        (Path(handle.name) / "txtcmds" / "bin" / "ulimit").write_text("unlimited\n")
        path = "/usr/sbin:/usr/bin:/sbin:/bin".split(":")
        return lambda name: get_command(protocol, name, path, "/root")

    def test_only_builtins_and_programs_of_the_firmware(self):
        find = self.lookup(",".join(firmware.TPLINK_ASH_BUILTINS))
        self.assertEqual(find("cd"), "py-cd")
        self.assertEqual(find("cat"), "py-cat")
        self.assertEqual(find("free"), "py-free")  # /usr/bin/free -> ../../bin/busybox
        self.assertEqual(find("grep"), "py-grep")  # /bin/grep -> busybox
        self.assertEqual(find("uname"), "py-uname")
        # no builtin history, no applet nproc, the opkg of the image left out (firmware.LEFT_OUT)
        for name in ("history", "nproc", "opkg"):
            self.assertIsNone(find(name), name)
        # a builtin without a Python command: its txtcmd, no program on PATH
        self.assertEqual(find("ulimit"), ("txtcmd", b"unlimited\n"))

    def test_without_the_key_every_python_command_answers(self):
        find = self.lookup(None)
        for name in ("grep", "history", "uname", "cd"):
            self.assertIsNotNone(find(name), name)


class EdgeosInitTest(unittest.TestCase):
    def test_the_init_scripts_of_the_image(self):
        # /etc/init.d of ER-e50.v2.0.9-hotfix.7 (squashfs); EdgeOS 2 boots with systemd: no rc links
        persona = catalog.by_id("ubiquiti-edgerouter-x")
        ident = draw(persona, 0)
        t = render.apply_to_tree(base_tree(), persona, ident, persona.builder(persona, ident))
        names = {child[tree.A_NAME] for child in tree.node_children(tree.find_node(t, "etc/init.d"))}
        self.assertEqual(names, set(system.EDGEOS_INIT))
        for name in ("vyatta-quagga", "udapi-bridge", "dnsmasq", "ssh", "rc", "rcS"):
            self.assertIn(name, names)
        for level in ("0", "1", "2", "3", "4", "5", "6", "S"):
            node = tree.find_node(t, f"etc/rc{level}.d")
            self.assertEqual([c for c in tree.node_children(node or [None] * 8) if c[tree.A_TYPE] == tree.T_LINK]
                             if node is not None else [], [], level)
        script = tree.node_get(tree.find_node(t, "etc/init.d/vyatta-quagga"), tree.A_CONTENTS)
        self.assertIn(b"# Provides:          vyatta-quagga", script)


class WhichTest(unittest.TestCase):
    """which on a device whose /bin and /usr/bin are folders of their own (BusyBox and EdgeOS's Debian 9;
    Cowrie's tree has them as one): a program of the table is named in the folder the system has it
    in, once also with -a (busybox:1.27.2 / 1.36.1 and debian:9: `which -a sh ls` is /bin/sh, /bin/ls)."""

    def which(self, args, table):
        from tests import test_patch
        case = test_patch.PatchTest("test_which_a_prints_every_match")
        # the borrowed case is never run: its temporary folders go with this test
        self.addCleanup(case.doCleanups)
        path = Path(self.id().replace(".", "_") + ".txt")
        import tempfile
        handle = tempfile.TemporaryDirectory()
        self.addCleanup(handle.cleanup)
        path = Path(handle.name) / "program-dirs.txt"
        path.write_text("".join(f"{name} {folder}\n" for name, folder in table.items()))  # one folder each
        _, which = case.command("which", "Command_which", {("shell", "which_dirs"): str(path)}, args)
        which.environ = {"PATH": ":".join(test_patch.PATH)}
        which.fs, which.exit_code = test_patch.TreeFs(base_tree()), 0
        which.call()
        return bytes(which.out).decode(), which.exit_code

    def test_the_folder_of_the_system(self):
        table = {"sh": "bin", "ls": "bin", "perl": "usr/bin"}
        self.assertEqual(self.which(["-a", "sh", "ls"], table), ("/bin/sh\n/bin/ls\n", 0))
        self.assertEqual(self.which(["sh"], table), ("/bin/sh\n", 0))
        self.assertEqual(self.which(["-a", "perl"], table), ("/usr/bin/perl\n", 0))
        # a program the table does not name: as before, every match with -a
        self.assertEqual(self.which(["-a", "python3"], table), ("/usr/bin/python3\n/bin/python3\n", 0))
        self.assertEqual(self.which(["nothere"], {"nothere": "bin"}), ("", 1))

    def test_the_tables_of_the_devices(self):
        expected = {"openwrt": {"sh": "bin", "ls": "bin", "wc": "usr/bin", "ifconfig": "sbin"},
                    "ubiquiti-edgerouter-x": {"sh": "bin", "ls": "bin", "ip": "bin,sbin"},
                    "dlink-dir859": {"sh": "bin", "ifconfig": "sbin", "wget": "usr/bin"}}
        for pid, part in expected.items():
            persona = catalog.by_id(pid)
            table = firmware.program_dirs(persona, system.busybox_applets(persona, draw(persona, 0)))
            for name, folder in part.items():
                self.assertEqual(table.get(name), tuple(folder.split(",")), (pid, name))
        for pid in ("ubuntu-noble", "synology-dsm", "tplink-archer-c7"):
            persona = catalog.by_id(pid)
            self.assertIsNone(firmware.program_dirs(persona, ()), pid)
        # names with a hyphen stay whole
        persona = catalog.by_id("openwrt")
        table = firmware.program_dirs(persona, system.busybox_applets(persona, draw(persona, 0)))
        self.assertEqual(table.get("start-stop-daemon"), ("sbin",))
        for tables in (firmware.BUSYBOX_DIRS, firmware.DEBIAN9_DIRS):
            for names in tables.values():
                self.assertFalse([n for n in "".join(names).split() if n.endswith("-") or n.startswith("-")])

    def test_fails_loudly_on_unknown_upstream(self):
        import shutil
        import subprocess
        import sys
        import tempfile

        from tests.test_patch import FIXTURE, PATCH
        old = "from cowrie.shell.command import HoneyPotCommand\n"
        with tempfile.TemporaryDirectory() as handle:
            root = Path(handle) / "cowrie"
            shutil.copytree(FIXTURE, root)
            path = root / "src/cowrie/commands/which.py"
            text = path.read_text()
            self.assertEqual(text.count(old), 1)
            path.write_text(text.replace(old, "from cowrie.shell import command\n"))
            done = subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(root)], capture_output=True,
                                  text=True)
            self.assertNotEqual(done.returncode, 0)
            self.assertIn("Could not find expected text", done.stderr)


if __name__ == "__main__":
    unittest.main()
