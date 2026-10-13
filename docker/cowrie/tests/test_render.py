import calendar
import configparser
import dataclasses
import json
import re
import unittest

from tests.helpers import base_tree, tmpdir
from tpot_personas import catalog, system, tree, validate
from tpot_personas.common import kernel_series
from tpot_personas.identity import DAY, draw, fits, kernel_time, restore, specialize, to_state, updated_at
from tpot_personas.render import render

NOW = 1_790_000_000


def render_ident(test, persona, ident):
    out = tmpdir(test) / persona.id
    keys = tmpdir(test) / "etc" / persona.id
    render(persona, ident, base_tree(), out, runtime_dir=out, keys_dir=keys)
    return ident, out, keys


def render_one(test, persona, seed, now=NOW):
    return render_ident(test, persona, draw(persona, seed, now))


def cfg_of(out):
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read(out / "cowrie.cfg")
    return cfg


def skipped(out):
    return {e.strip() for e in cfg_of(out).get("shell", "skip_python_commands").split(",")}


class ProgramTest(unittest.TestCase):
    """Commands are programs of fs.pickle: a size, no contents (Cowrie's cat / dd
    serve the ELF header of [shell] arch), the size of Cowrie's own program kept."""

    def test_txtcmds_are_programs_with_a_size(self):
        for persona in catalog.PERSONAS:
            ident, out, _ = render_one(self, persona, 1)
            fs = tree.load_pickle(out / "fs.pickle")
            content = persona.builder(persona, ident)
            for rel in set(content.txtcmds) | set(content.programs):
                node = tree.find_node(fs, rel)
                self.assertEqual(tree.node_get(node, tree.A_TYPE), tree.T_FILE, (persona.id, rel))
                self.assertTrue(tree.node_get(node, tree.A_MODE) & 0o111, (persona.id, rel))
                self.assertGreater(tree.node_get(node, tree.A_SIZE), 10_000, (persona.id, rel))
                self.assertNotIsInstance(tree.node_get(node, tree.A_CONTENTS), bytes, (persona.id, rel))
            # usr/bin/top of Cowrie's pickle keeps its size
            self.assertEqual(tree.node_get(tree.find_node(fs, "usr/bin/top"), tree.A_SIZE), 134736, persona.id)

    def test_bin_stays_a_link_to_usr_bin(self):
        # a /bin of its own held only the txtcmds: `ls /bin` showed five files, `df` ran Debian's binary
        for pid in ("ubuntu-noble", "tplink-wr841n"):
            _, out, _ = render_one(self, catalog.by_id(pid), 1)
            fs = tree.load_pickle(out / "fs.pickle")
            for name in ("bin", "sbin", "lib"):
                node = tree.find_node(fs, name)
                self.assertEqual((tree.node_get(node, tree.A_TYPE), tree.node_get(node, tree.A_TARGET)),
                                 (tree.T_LINK, f"usr/{name}"), (pid, name))
            self.assertIs(tree.find_node(fs, "bin/df"), tree.find_node(fs, "usr/bin/df"))
            self.assertEqual(tree.node_get(tree.find_node(fs, "usr/bin/df"), tree.A_SIZE), 105824)

    def test_sizes_are_drawn_per_identity(self):
        persona = catalog.by_id("openwrt")
        sizes = set()
        for seed in range(4):
            _, out, _ = render_one(self, persona, seed)
            sizes.add(tree.node_get(tree.find_node(tree.load_pickle(out / "fs.pickle"), "bin/busybox"), tree.A_SIZE))
        self.assertGreater(len(sizes), 1)
        self.assertTrue(all(380_000 <= size <= 1_100_000 for size in sizes), sizes)


class TextToolsTest(unittest.TestCase):
    """wc, sort and seq: coreutils on servers and DSM, the applets of the firmware's BusyBox on the
    other devices (TL-WR841N: none of them; the shared list: wc only)."""

    def test_the_programs_and_the_answers_agree(self):
        expected = {"tplink-wr841n": set(), "openwrt": {"wc", "sort", "seq"},
                    "ubiquiti-edgerouter-x": {"wc", "sort", "seq"}, "dlink-dir859": {"wc"},
                    "synology-dsm": {"wc", "sort", "seq"}, "debian-bookworm": {"wc", "sort", "seq"}}
        for persona in catalog.PERSONAS:
            if persona.id not in expected:
                continue
            ident, out, _ = render_one(self, persona, 1)
            fs = tree.load_pickle(out / "fs.pickle")
            with self.subTest(persona=persona.id):
                for name in system.TEXT_TOOLS:
                    present = name in expected[persona.id]
                    self.assertEqual(name not in skipped(out), present, name)
                    self.assertEqual(f"/usr/bin/{name}" not in skipped(out), present, name)
                    self.assertEqual(tree.find_node(fs, f"usr/bin/{name}") is not None, present, name)
                if persona.id == "openwrt":
                    node = tree.find_node(fs, "usr/bin/wc")
                    self.assertEqual((node[tree.A_TYPE], node[tree.A_TARGET]), (tree.T_LINK, "../../bin/busybox"))
                if persona.id == "ubiquiti-edgerouter-x":
                    self.assertEqual(tree.find_node(fs, "usr/bin/sort")[tree.A_TARGET], "/bin/busybox")

    def test_the_keys_name_the_persona_s_coreutils_or_busybox(self):
        cases = {"debian-trixie": {"text_style": "gnu", "text_version": "9.7", "text_packager": "Debian (9.7-3)",
                                   "text_collate": "c"},
                 "ubuntu-noble": {"text_style": "gnu", "text_version": "9.4", "text_year": "2023"},
                 "rhel-9": {"text_version": "8.32", "text_collate": "en", "text_fold": "bytes"},
                 "centos-7": {"text_version": "8.22", "text_collate": "en-2.17", "text_fold": "bytes"},
                 "raspios": {"text_version": "9.1", "text_collate": "en"},
                 "synology-dsm": {"text_style": "gnu", "text_version": "8.31", "text_collate": "posix"},
                 "openwrt": {"text_style": "busybox", "text_libc": "musl"},
                 "tplink-wr841n": {"text_style": "busybox", "text_libc": "uclibc", "text_version": "1.19.2"},
                 "ubiquiti-edgerouter-x": {"text_style": "busybox", "text_libc": "glibc", "text_version": "1.27.2"}}
        for persona_id, keys in cases.items():
            persona = catalog.by_id(persona_id)
            ident, out, _ = render_one(self, persona, 2)
            cfg = cfg_of(out)
            with self.subTest(persona=persona_id):
                for key, value in keys.items():
                    self.assertEqual(cfg.get("shell", key), value, key)
                if keys.get("text_style") == "busybox":
                    self.assertEqual(cfg.get("shell", "text_banner"), ident.release.extra["busybox"])
                self.assertEqual(cfg.has_option("shell", "text_packager"), persona_id == "debian-trixie")


class NetToolsTest(unittest.TestCase):
    """ip, ss, ifconfig and netstat: iproute2 on every server, net-tools where it is installed, the
    programs of the firmware on the devices (BusyBox applets, iproute2 of the TL-WR841N and EdgeOS)."""

    def test_the_programs_and_the_answers_agree(self):
        expected = {"debian-bookworm": {"ip", "ss"}, "ubuntu-noble": {"ip", "ss", "ifconfig", "netstat"},
                    "rhel-9": {"ip", "ss"}, "centos-7": {"ip", "ss", "ifconfig", "netstat"},
                    "openwrt": {"ip", "ifconfig", "netstat"}, "tplink-wr841n": {"ip", "ifconfig", "netstat"},
                    "ubiquiti-edgerouter-x": {"ip", "ss", "ifconfig", "netstat"}, "dlink-dir859": {"ifconfig", "netstat"}}
        paths = {"rhel-9": {"ip": "usr/sbin/ip"}, "debian-bookworm": {"ip": "usr/bin/ip", "ss": "usr/bin/ss"},
                 "ubuntu-noble": {"ip": "usr/bin/ip", "ifconfig": "usr/sbin/ifconfig", "netstat": "usr/bin/netstat"},
                 "openwrt": {"ip": "sbin/ip"}, "tplink-wr841n": {"ip": "usr/bin/ip"}}
        for persona_id, names in expected.items():
            persona = catalog.by_id(persona_id)
            ident, out, _ = render_one(self, persona, 1)
            fs = tree.load_pickle(out / "fs.pickle")
            cfg = cfg_of(out)
            with self.subTest(persona=persona_id):
                self.assertEqual(cfg.get("shell", "network"), f"{out}/network.json")
                data = json.loads((out / "network.json").read_text())
                self.assertEqual(set(data["tools"]), names)
                for name in ("ip", "ss", "ifconfig", "netstat"):
                    self.assertEqual(name not in skipped(out), name in names, name)
                for name, rel in paths.get(persona_id, {}).items():
                    self.assertIsNotNone(tree.find_node(fs, rel), rel)
                if persona_id == "openwrt":
                    self.assertEqual(tree.find_node(fs, "sbin/ip")[tree.A_TARGET], "../bin/busybox")
                if persona_id in ("debian-bookworm", "ubuntu-noble"):
                    # root's PATH finds /usr/sbin/ip, the link of Debian's iproute2 (ubuntu:24.04, debian:12)
                    self.assertEqual(tree.find_node(fs, "usr/sbin/ip")[tree.A_TARGET], "/bin/ip")
                if persona_id == "tplink-wr841n":
                    self.assertEqual({i["qdisc"] for i in data["interfaces"]}, {"noqueue", "pfifo_fast"})
                if persona_id == "centos-7":
                    # yum knows the package of ip and ss as installed
                    self.assertIn("iproute 4.11.0-30.el7", cfg.get("shell", "yum_installed").split(","))


class BusyboxTest(unittest.TestCase):
    def test_devices_with_busybox_run_cowries_busybox_with_their_help(self):
        with_busybox = [p for p in catalog.PERSONAS if any(r.extra.get("busybox") for r in p.releases)]
        self.assertEqual({p.id for p in with_busybox}, {"dlink-dir859", "tplink-wr841n", "zyxel-nas326", "openwrt",
                                                       "ubiquiti-edgerouter-x", "ipcam", "qnap-qts"})
        for persona in with_busybox:
            for seed in range(3):
                ident, out, _ = render_one(self, persona, seed)
                banner = ident.release.extra["busybox"]
                self.assertNotIn("busybox", skipped(out), persona.id)
                self.assertNotIn("/bin/busybox", skipped(out), persona.id)
                self.assertEqual(cfg_of(out).get("shell", "busybox_help"), f"{out}/busybox-help.txt")
                text = (out / "busybox-help.txt").read_text()
                self.assertTrue(text.startswith(f"{banner} multi-call binary.\n"), (persona.id, text[:80]))
                listed = "".join(text.split("Currently defined functions:\n", 1)[1].split()).split(",")
                self.assertEqual(listed, system.busybox_applets(persona, ident), persona.id)
                self.assertFalse((out / "txtcmds/bin/busybox").exists(), persona.id)

    def test_the_busybox_of_qnap_agrees_with_itself(self):
        """The banner of busybox, of its applets' messages and of df is one; the program is in the tree."""
        persona = catalog.by_id("qnap-qts")
        ident, out, _ = render_one(self, persona, 2)
        banner = ident.release.extra.get("busybox", "")
        self.assertTrue(banner.startswith("BusyBox v1.24.1 ("), banner)
        cfg = cfg_of(out)
        self.assertEqual(cfg.get("shell", "text_banner"), banner)
        self.assertEqual(json.loads((out / "df.json").read_text())["banner"], banner)
        self.assertTrue((out / "busybox-help.txt").read_text().startswith(f"{banner} multi-call binary.\n"))
        self.assertNotIn("busybox", skipped(out))
        self.assertIsNotNone(tree.find_node(tree.load_pickle(out / "fs.pickle"), "bin/busybox"))

    def test_systems_without_busybox_skip_it(self):
        for persona in catalog.PERSONAS:
            ident, out, _ = render_one(self, persona, 0)
            if ident.release.extra.get("busybox"):
                continue
            self.assertIn("busybox", skipped(out), persona.id)
            self.assertFalse(cfg_of(out).has_option("shell", "busybox_help"), persona.id)
            self.assertFalse((out / "busybox-help.txt").exists(), persona.id)
            self.assertIsNone(tree.find_node(tree.load_pickle(out / "fs.pickle"), "bin/busybox"), persona.id)


def fs_of(out):
    return tree.load_pickle(out / "fs.pickle")


def names(fs):
    return [path for path, _ in tree.iter_nodes(fs)]


class SystemTest(unittest.TestCase):
    """Cowrie's Debian 12 x86_64 container tree becomes the persona's system (F4, F6)."""

    def test_no_debian_package_system_without_dpkg(self):
        for pid in ("rhel-9", "centos-7", "openwrt", "qnap-qts", "tplink-wr841n"):
            _, out, _ = render_one(self, catalog.by_id(pid), 1)
            fs = fs_of(out)
            for rel in ("etc/debian_version", "etc/apt", "etc/dpkg", "var/lib/dpkg", "usr/bin/dpkg",
                        "usr/bin/apt-get"):
                self.assertIsNone(tree.find_node(fs, rel), (pid, rel))
        _, out, _ = render_one(self, catalog.by_id("ubiquiti-edgerouter-x"), 1)
        self.assertIsNotNone(tree.find_node(fs_of(out), "var/lib/dpkg/status"))

    def test_no_container_leftovers(self):
        for persona in catalog.PERSONAS:
            _, out, _ = render_one(self, persona, 1)
            fs = fs_of(out)
            for rel in (".dockerenv", "etc/runit", "etc/sv"):
                self.assertIsNone(tree.find_node(fs, rel), (persona.id, rel))

    def test_no_x86_64_on_other_architectures(self):
        for persona in catalog.PERSONAS:
            _, out, _ = render_one(self, persona, 1)
            x86 = [p for p in names(fs_of(out)) if "x86_64" in p or "x86-64" in p]
            if persona.hardware_platform == "x86_64":
                if "dpkg" in persona.package_managers:
                    self.assertIn("/usr/lib/x86_64-linux-gnu/libc.so.6", x86, persona.id)
                else:
                    self.assertNotIn("/usr/lib/x86_64-linux-gnu", x86, persona.id)
            else:
                self.assertEqual(x86, [], persona.id)
        _, out, _ = render_one(self, catalog.by_id("raspios"), 1)
        fs = fs_of(out)
        self.assertIsNotNone(tree.find_node(fs, "usr/lib/aarch64-linux-gnu/libc.so.6"))
        self.assertIsNone(tree.find_node(fs, "lib64"))

    def test_systemd_and_openssh_only_where_the_system_has_them(self):
        for persona in catalog.PERSONAS:
            _, out, _ = render_one(self, persona, 1)
            fs = fs_of(out)
            systemd = persona.kind == "server" or persona.family in ("synology", "ubiquiti")
            self.assertEqual(tree.find_node(fs, "etc/systemd") is not None, systemd, persona.id)
            openssh = not persona.ssh_profile.startswith("dropbear")
            self.assertEqual(tree.find_node(fs, "etc/ssh/sshd_config") is not None, openssh, persona.id)

    def test_proc_modules_of_the_persona(self):
        for persona in catalog.PERSONAS:
            ident, out, _ = render_one(self, persona, 1)
            node = tree.find_node(fs_of(out), "proc/modules")
            text = (out / "honeyfs/proc/modules").read_text()
            self.assertEqual(tree.node_get(node, tree.A_CONTENTS), text.encode(), persona.id)
            self.assertNotIn("nft_chain_nat", text, persona.id)
            self.assertEqual(system.modules_problems(text, system.address_width(persona)), [], persona.id)
            sizes = [int(line.split()[1]) for line in text.splitlines()]
            if persona.kind == "device":
                # a vendor kernel shows its module addresses, 32 bits on the routers and cameras
                width = 16 if persona.hardware_platform == "x86_64" else 8
                addresses = [line.split()[5] for line in text.splitlines()]
                self.assertTrue(all(len(a) == width + 2 and int(a, 16) for a in addresses), persona.id)
                self.assertEqual(addresses, sorted(addresses, reverse=True), persona.id)
                old = kernel_series(ident.release.kernel_version) < (4, 0)
                self.assertEqual(any(size % 4096 for size in sizes), old, persona.id)
                continue
            if persona.family == "centos":
                self.assertTrue(any(size % 4096 for size in sizes))
            else:
                self.assertTrue(all(size % 4096 == 0 for size in sizes), persona.id)
            self.assertEqual("nvidia " in text, bool(ident.hardware.gpus), persona.id)

    def test_servers_have_perl_and_their_python_git_where_installed(self):
        expected = {"ubuntu-noble": "3.12", "ubuntu-jammy": "3.10", "debian-trixie": "3.13",
                    "debian-bookworm": "3.11", "rhel-9": "3.9", "raspios": "3.11"}
        git = {"ubuntu-noble", "ubuntu-jammy", "raspios", "gpu-ubuntu"}
        for persona in catalog.PERSONAS:
            if persona.kind == "server":
                for seed in range(4):
                    ident, out, _ = render_one(self, persona, seed)
                    on_path = tree.find_node(fs_of(out), "usr/bin/git") is not None
                    self.assertEqual((on_path, "git" not in skipped(out), cfg_of(out).has_option("shell", "git_version"),
                                      "git" in ident.release.packages), (persona.id in git,) * 4, persona.id)
        for pid, version in expected.items():
            ident, out, _ = render_one(self, catalog.by_id(pid), 1)
            fs, cfg = fs_of(out), cfg_of(out)
            self.assertEqual(tree.node_get(tree.find_node(fs, "usr/bin/perl"), tree.A_SIZE), 3804432, pid)
            self.assertEqual(tree.node_get(tree.find_node(fs, "usr/bin/python3"), tree.A_TARGET),
                             f"python{version}", pid)
            self.assertIsNotNone(tree.find_node(fs, f"usr/bin/python{version}"), pid)
            self.assertIsNotNone(tree.find_node(fs, f"usr/lib/python{version}"), pid)
            if version != "3.11":
                self.assertFalse([p for p in names(fs) if "3.11" in p and "python" in p], pid)
            self.assertIsNone(tree.find_node(fs, "usr/bin/python"), pid)
            self.assertTrue(cfg.get("shell", "python_version").startswith(version + "."), pid)
            self.assertIn("python", skipped(out), pid)
            self.assertNotIn("python3", skipped(out), pid)
            self.assertNotIn("perl", skipped(out), pid)
            if pid in git:
                self.assertEqual(cfg.get("shell", "git_version"),
                                 ident.release.packages["git"].split(":")[-1].split("-")[0], pid)

    def test_centos_has_python_2_only(self):
        ident, out, _ = render_one(self, catalog.by_id("centos-7"), 1)
        fs, cfg = fs_of(out), cfg_of(out)
        self.assertEqual(tree.node_get(tree.find_node(fs, "usr/bin/python"), tree.A_TARGET), "python2")
        self.assertEqual(tree.node_get(tree.find_node(fs, "usr/bin/python2"), tree.A_TARGET), "python2.7")
        self.assertIsNotNone(tree.find_node(fs, "usr/bin/python2.7"))
        self.assertFalse([p for p in names(fs) if "python3" in p])
        self.assertEqual((cfg.get("shell", "python_version"), cfg.get("shell", "perl_version")), ("2.7.5", "5.16.3"))
        self.assertIn("python3", skipped(out))
        self.assertNotIn("python", skipped(out))
        self.assertEqual(cfg.get("shell", "perl_archname"), "x86_64-linux-thread-multi")

    def test_devices_have_no_interpreters_but_edgeos_perl(self):
        for persona in catalog.PERSONAS:
            if persona.kind != "device":
                continue
            _, out, _ = render_one(self, persona, 1)
            fs, cfg = fs_of(out), cfg_of(out)
            edgeos = persona.family == "ubiquiti"
            for name in ("git", "python", "python3") + (() if edgeos else ("perl",)):
                self.assertIn(name, skipped(out), (persona.id, name))
                self.assertIsNone(tree.find_node(fs, f"usr/bin/{name}"), (persona.id, name))
            self.assertFalse([p for p in names(fs) if "python3" in p], persona.id)
            self.assertEqual(bool([p for p in names(fs) if "/perl" in p]), edgeos, persona.id)
            self.assertEqual(cfg.has_option("shell", "perl_version"), edgeos, persona.id)
        _, out, _ = render_one(self, catalog.by_id("ubiquiti-edgerouter-x"), 1)
        cfg = cfg_of(out)
        self.assertNotIn("perl", skipped(out))
        self.assertIsNotNone(tree.find_node(fs_of(out), "usr/bin/perl"))
        self.assertEqual((cfg.get("shell", "perl_version"), cfg.get("shell", "perl_archname"),
                          cfg.get("shell", "perl_copyright")), ("5.24.1", "mipsel-linux-gnu-thread-multi", "2017"))
        self.assertFalse(cfg.has_option("shell", "sudo_version"))

    def test_no_debian_tools_without_dpkg(self):
        for persona in catalog.PERSONAS:
            _, out, _ = render_one(self, persona, 1)
            fs = fs_of(out)
            debian = "dpkg" in persona.package_managers
            for rel in ("etc/adduser.conf", "etc/rcS.d", "usr/bin/which.debianutils"):
                self.assertEqual(tree.find_node(fs, rel) is not None, debian, (persona.id, rel))
            which = tree.find_node(fs, "usr/bin/which")
            self.assertEqual(tree.node_get(which, tree.A_TYPE), tree.T_LINK if debian else tree.T_FILE, persona.id)

    def test_links_have_the_targets_of_a_real_system(self):
        _, out, _ = render_one(self, catalog.by_id("debian-bookworm"), 1)
        fs = fs_of(out)
        targets = {path: tree.node_get(node, tree.A_TARGET) for path, node in tree.iter_nodes(fs)
                   if tree.node_get(node, tree.A_TYPE) == tree.T_LINK}
        self.assertEqual(targets["/bin"], "usr/bin")
        self.assertEqual(targets["/usr/bin/which"], "which.debianutils")
        self.assertEqual(targets["/etc/systemd/system/sshd.service"], "/lib/systemd/system/ssh.service")
        self.assertEqual(targets["/proc/self"], "969")
        self.assertEqual(targets["/usr/lib64/ld-linux-x86-64.so.2"], "/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2")
        self.assertTrue(all(targets.values()), [p for p, t in targets.items() if not t])
        sizes = [path for path, node in tree.iter_nodes(fs) if tree.node_get(node, tree.A_TYPE) == tree.T_LINK
                 and tree.node_get(node, tree.A_SIZE) != len(tree.node_get(node, tree.A_TARGET))]
        self.assertEqual(sizes, [])

    def test_terminal_group_is_one_the_system_has(self):
        for pid, gid, mode in (("ubuntu-noble", 5, 0o620), ("openwrt", 0, 0o600)):
            _, out, _ = render_one(self, catalog.by_id(pid), 1)
            pts = tree.find_node(fs_of(out), "dev/pts/0")
            self.assertEqual((tree.node_get(pts, tree.A_GID), tree.node_get(pts, tree.A_MODE) & 0o777), (gid, mode), pid)

    def test_red_hat_init_directories_and_loader(self):
        for pid in ("rhel-9", "centos-7"):
            _, out, _ = render_one(self, catalog.by_id(pid), 1)
            fs = fs_of(out)
            self.assertEqual(tree.node_get(tree.find_node(fs, "etc/init.d"), tree.A_TARGET), "rc.d/init.d")
            self.assertEqual(tree.node_get(tree.find_node(fs, "etc/rc3.d"), tree.A_TARGET), "rc.d/rc3.d")
            self.assertIsNotNone(tree.find_dir(fs, "etc/rc3.d"))
            self.assertIn(b"systemctl", tree.node_get(tree.find_node(fs, "etc/init.d/README"), tree.A_CONTENTS))
            self.assertIn("Description=OpenSSH server daemon",
                          (out / "honeyfs/usr/lib/systemd/system/sshd.service").read_text())
            self.assertIsNone(tree.find_node(fs, "usr/lib/systemd/system/ssh.service"), pid)
            self.assertEqual(tree.node_get(tree.find_node(fs, "usr/sbin/adduser"), tree.A_TARGET), "useradd")
            loader = tree.find_node(fs, "usr/lib64/ld-linux-x86-64.so.2")
            if pid == "centos-7":
                self.assertEqual(tree.node_get(loader, tree.A_TARGET), "ld-2.17.so")
                self.assertEqual(tree.node_get(tree.find_node(fs, "etc/rc.d/rc3.d/S10network"), tree.A_TARGET),
                                 "../init.d/network")
            else:
                self.assertEqual(tree.node_get(loader, tree.A_TYPE), tree.T_FILE)
            self.assertIsNone(tree.find_node(fs, "usr/bin/ld.so"), pid)
            # grubby's installkernel
            self.assertIsNotNone(tree.find_node(fs, "usr/sbin/installkernel"), pid)
        _, out, _ = render_one(self, catalog.by_id("openwrt"), 1)
        self.assertIsNone(tree.find_node(fs_of(out), "usr/sbin/installkernel"))

    def test_login_shell_streams_are_its_terminal(self):
        for pid in ("ubuntu-noble", "tplink-wr841n"):
            persona = catalog.by_id(pid)
            _, out, _ = render_one(self, persona, 1)
            fs = fs_of(out)
            self.assertEqual(tree.node_get(tree.find_node(fs, "dev/stdout"), tree.A_TARGET), "/proc/self/fd/1")
            self.assertEqual(tree.node_get(tree.find_node(fs, "proc/self/fd/1"), tree.A_TARGET), "/dev/pts/0")
            pts = tree.find_node(fs, "dev/pts/0")
            self.assertEqual((tree.node_get(pts, tree.A_TYPE), tree.node_get(pts, tree.A_UID)),
                             (tree.T_CHR, persona.uid))
            self.assertEqual(tree.node_get(tree.find_node(fs, "proc/self/exe"), tree.A_TARGET),
                             "/usr/bin/bash" if persona.kind == "server" else "/bin/busybox")
            self.assertEqual(tree.node_get(tree.find_node(fs, "proc/self/cwd"), tree.A_TARGET),
                             "/" + ("root" if persona.uid == 0 else f"home/{persona.user}"))
            self.assertEqual(tree.node_get(tree.find_node(fs, "proc/self/root"), tree.A_TARGET), "/")


class ServerCommandTest(unittest.TestCase):
    """Servers run Cowrie's sudo, service, apt / apt-get (Debian), yum (CentOS 7) and the
    patched dnf / rpm (RHEL 9) (F7)."""

    def test_python_commands_and_txtcmds(self):
        for persona in catalog.PERSONAS:
            ident, out, _ = render_one(self, persona, 1)
            fs, skip = fs_of(out), skipped(out)
            txtcmds = {str(p.relative_to(out / "txtcmds")) for p in (out / "txtcmds").rglob("*") if p.is_file()}
            server = persona.kind == "server"
            debian = server and "dpkg" in persona.package_managers
            rhel = persona.family == "rhel"
            # apt-get of EdgeOS is the patched apt-get too (apt 1.4 without sources)
            answered = {"sudo": server, "service": server, "apt": debian,
                        "apt-get": debian or persona.family == "ubiquiti",
                        "yum": persona.family in ("centos", "rhel"), "dnf": rhel, "dnf-3": rhel, "rpm": rhel}
            for name, python in answered.items():
                self.assertEqual(name not in skip, python, (persona.id, name))
                path = "usr/sbin/service" if name == "service" else f"usr/bin/{name}"
                if python:
                    self.assertNotIn(path, txtcmds, (persona.id, name))
                    self.assertIsNotNone(tree.find_node(fs, path), (persona.id, name))
            if debian:
                self.assertIn("usr/bin/dpkg", txtcmds, persona.id)
            if rhel:
                # dnf 4: dnf and yum are links to the script dnf-3
                for name in ("dnf", "yum"):
                    node = tree.find_node(fs, f"usr/bin/{name}")
                    self.assertEqual((tree.node_get(node, tree.A_TYPE), tree.node_get(node, tree.A_TARGET)),
                                     (tree.T_LINK, "dnf-3"))
                script = tree.find_node(fs, "usr/bin/dnf-3")
                self.assertTrue(tree.node_get(script, tree.A_CONTENTS).startswith(b"#!/usr/bin/python3\n"))
                self.assertEqual(tree.node_get(script, tree.A_MODE) & 0o777, 0o755)
                self.assertEqual(cfg_of(out).get("shell", "dnf_packages"), f"{out}/dnf-packages.json")
            else:
                self.assertIsNone(cfg_of(out).get("shell", "dnf_packages", fallback=None), persona.id)
            if persona.family == "centos":
                self.assertIn("usr/bin/rpm", txtcmds)

    def test_dnf_packages_of_rhel(self):
        persona = catalog.by_id("rhel-9")
        seen = set()
        for seed in range(12):
            ident, out, _ = render_one(self, persona, seed)
            r = ident.release
            data = json.loads((out / "dnf-packages.json").read_text())
            installed = data["installed"]
            self.assertEqual(installed["openssh-server"]["evr"], r.packages["openssh-server"])
            self.assertEqual(installed["kernel"]["evr"] + ".x86_64", r.kernel_version)
            self.assertEqual(data["version"], r.packages["dnf"])
            self.assertTrue(installed["dnf"]["evr"].startswith(r.packages["dnf"] + "-"))
            self.assertFalse(set(installed) & set(data["available"]))
            eus = r.extra["updated"] >= system.RHEL_NEXT_MINOR[r.os_version_id]
            seen.add(eus)
            enabled = sorted(repo["id"] for repo in data["repos"] if repo["enabled"])
            self.assertEqual(enabled, [f"rhel-9-for-x86_64-appstream{'-eus' if eus else ''}-rpms",
                                       f"rhel-9-for-x86_64-baseos{'-eus' if eus else ''}-rpms"])
            repo = (out / "honeyfs/etc/yum.repos.d/redhat.repo").read_text()
            self.assertIn("# Managed by (rhsm) subscription-manager\n", repo)
            for repo_id in enabled:
                self.assertIn(f"[{repo_id}]\n", repo)
            for line in (out / "honeyfs/var/log/dnf.log").read_text().splitlines():
                if "SUBDEBUG Installed: " in line:
                    name = line.split("Installed: ")[1].rsplit("-", 2)[0]
                    self.assertIn(installed[name]["from"], {f"@{repo_id}" for repo_id in enabled})
            self.assertEqual(data["available"]["glibc-devel"]["evr"], installed["glibc"]["evr"])
            self.assertEqual(data["available"]["kernel-headers"]["evr"], installed["kernel"]["evr"])
        self.assertEqual(seen, {True, False})

    def test_versions_and_sources_of_the_persona(self):
        ident, out, _ = render_one(self, catalog.by_id("ubuntu-noble"), 1)
        cfg = cfg_of(out)
        self.assertEqual(cfg.get("shell", "apt_version"), ident.release.packages["apt"])
        self.assertEqual(cfg.get("shell", "deb_arch"), "amd64")
        self.assertEqual(cfg.get("shell", "apt_sources"),
                         "http://archive.ubuntu.com/ubuntu noble,http://archive.ubuntu.com/ubuntu noble-updates,"
                         "http://archive.ubuntu.com/ubuntu noble-backports,http://security.ubuntu.com/ubuntu "
                         "noble-security")
        self.assertEqual((cfg.get("shell", "sudo_version"), cfg.get("shell", "sudo_grammar")), ("1.9.15p5", "50"))
        self.assertEqual(cfg.get("shell", "service_status_all"), f"{out}/service-status.txt")
        self.assertIn(" [ + ]  ssh\n", (out / "service-status.txt").read_text())
        _, out, _ = render_one(self, catalog.by_id("raspios"), 1)
        cfg = cfg_of(out)
        self.assertEqual(cfg.get("shell", "deb_arch"), "arm64")
        self.assertTrue(cfg.get("shell", "apt_sources").endswith(",http://archive.raspberrypi.com/debian bookworm"))
        self.assertEqual(cfg.get("shell", "perl_archname"), "aarch64-linux-gnu-thread-multi")
        ident, out, _ = render_one(self, catalog.by_id("debian-bookworm"), 1)
        self.assertEqual(cfg_of(out).get("shell", "sudo_version"),
                         ident.release.packages["sudo"].split("-")[0])
        _, out, _ = render_one(self, catalog.by_id("centos-7"), 1)
        cfg = cfg_of(out)
        self.assertEqual((cfg.get("shell", "yum_plugins"), cfg.get("shell", "sudo_version")), ("fastestmirror", "1.8.23"))
        self.assertFalse(cfg.has_option("shell", "apt_version"))
        self.assertIn("Currently active devices:", (out / "service-status.txt").read_text())
        _, out, _ = render_one(self, catalog.by_id("rhel-9"), 1)
        self.assertEqual((out / "service-status.txt").read_text(), "")
        _, out, _ = render_one(self, catalog.by_id("openwrt"), 1)
        self.assertFalse((out / "service-status.txt").exists())
        self.assertFalse(cfg_of(out).has_option("shell", "sudo_version"))


class ServerRuntimeTest(unittest.TestCase):
    """systemctl, sudo -l and apt of a server read the runtime files of its persona."""

    def test_runtime_files_and_keys(self):
        for persona in catalog.PERSONAS:
            ident, out, _ = render_one(self, persona, 2)
            cfg, fs = cfg_of(out), fs_of(out)
            server = persona.kind == "server"
            debian = server and "dpkg" in persona.package_managers
            for name, key, present in ((system.SYSTEMD_UNITS, "systemctl_units", server),
                                       (system.SUDO_LIST, "sudo_list", server),
                                       (system.APT_PACKAGES, "apt_packages", debian)):
                self.assertEqual((out / name).is_file(), present, (persona.id, name))
                self.assertEqual(cfg.get("shell", key, fallback=None), f"{out}/{name}" if present else None,
                                 (persona.id, key))
            self.assertEqual("systemctl" not in skipped(out), server, persona.id)
            self.assertEqual(tree.find_node(fs, "usr/bin/systemctl") is not None, server, persona.id)
            if not server:
                continue
            units = json.loads((out / system.SYSTEMD_UNITS).read_text())
            pids = {p["PID"]: p["COMMAND"] for p in json.loads((out / "cmdoutput.json").read_text())["command"]["ps"]}
            for unit in units["units"]:
                if unit["sub"] == "running":
                    self.assertEqual(pids[unit["pid"]], unit["command"], (persona.id, unit["name"]))
                elif unit["sub"] == "exited":
                    self.assertNotIn(unit["pid"], pids, (persona.id, unit["name"]))
            rules = json.loads((out / system.SUDO_LIST).read_text())["rules"]
            self.assertEqual(rules[persona.user][-1], "(ALL) NOPASSWD: ALL", persona.id)
        _, out, _ = render_one(self, catalog.by_id("ubuntu-noble"), 1)
        self.assertEqual(cfg_of(out).get("shell", "apt_fallback"), "ubuntu")
        packages = json.loads((out / system.APT_PACKAGES).read_text())
        installed, available = packages["installed"], packages["available"]
        self.assertEqual(installed["man-db"], "2.12.0-4build2")
        self.assertEqual(installed["git"], "1:2.43.0-1ubuntu7.3")
        for name in ("wget", "curl", "perl", "python3", "tar", "openssh-server", "sudo"):
            self.assertIn(name, installed, name)
        self.assertTrue(available["nmap"].startswith("7.94"), available["nmap"])
        for name in ("nmap", "masscan", "socat", "gcc", "make", "tor", "hydra", "unzip", "screen", "tmux",
                     "iproute2"):
            self.assertIn(name, {**installed, **available}, name)
        fs = fs_of(out)
        self.assertIsNone(tree.find_node(fs, "etc/systemd/system/sshd.service"))
        self.assertEqual(tree.node_get(tree.find_node(fs, "etc/systemd/system/sockets.target.wants/ssh.socket"),
                                       tree.A_TARGET), "/usr/lib/systemd/system/ssh.socket")
        _, out, _ = render_one(self, catalog.by_id("debian-trixie"), 1)
        self.assertEqual(cfg_of(out).get("shell", "apt_fallback"), "debian")
        self.assertIsNotNone(tree.find_node(fs_of(out), "etc/systemd/system/sshd.service"))


class PackageNamesTest(unittest.TestCase):
    """apt, yum 3 and opkg know the names of the release's packages (package_names)."""

    def test_names_of_the_release_and_of_the_persona(self):
        samples = {"debian-bookworm": ("htop", "raspi-config"), "debian-trixie": ("htop", "ubuntu-server"),
                   "ubuntu-noble": ("ubuntu-server", "raspi-config"), "ubuntu-jammy": ("ubuntu-server", "raspi-config"),
                   "gpu-ubuntu": ("htop", "yum"), "raspios": ("raspi-config", "ubuntu-server"),
                   "centos-7": ("bash", "htop"), "openwrt": ("htop packages", "apt base")}
        for persona in catalog.PERSONAS:
            ident, out, _ = render_one(self, persona, 2)
            path = out / system.PACKAGE_NAMES
            server = persona.kind == "server"
            self.assertEqual(path.is_file(), persona.id in samples or persona.family == "ubiquiti", persona.id)
            if persona.family == "ubiquiti":
                # EdgeOS names no apt sources
                self.assertEqual(path.read_text(), "")
                self.assertEqual(cfg_of(out).get("shell", "apt_version"), "1.4.10")
            if persona.id not in samples:
                continue
            lines = path.read_text().splitlines()
            self.assertEqual(len(lines), len(set(lines)), persona.id)
            has, lacks = samples[persona.id]
            self.assertIn(has, lines, persona.id)
            self.assertNotIn(lacks, lines, persona.id)
            self.assertEqual(cfg_of(out).get("shell", "package_names", fallback=None),
                             f"{out}/{system.PACKAGE_NAMES}", persona.id)
            if "dpkg" in persona.package_managers:
                apt = json.loads((out / system.APT_PACKAGES).read_text())
                self.assertFalse((set(apt["installed"]) | set(apt["available"])) - set(lines), persona.id)
            if persona.family == "centos":
                self.assertFalse(set(ident.release.packages) - set(lines))
                installed = dict(e.split(" ", 1) for e in cfg_of(out).get("shell", "yum_installed").split(","))
                self.assertEqual(installed, {**system.CENTOS_BASE, **ident.release.packages})
                self.assertIn("iproute", lines)

    def test_names_of_the_kernel_virtual_names_and_providers(self):
        for pid in ("debian-bookworm", "debian-trixie", "ubuntu-noble", "ubuntu-jammy", "raspios", "gpu-ubuntu"):
            persona = catalog.by_id(pid)
            ident = draw(persona, 2)
            lines = system.package_names(persona, ident)
            names = {line for line in lines if " " not in line}
            virtuals = dict(line.split(" ") for line in lines if " " in line)
            release = ident.release.kernel_version
            self.assertIn(f"linux-headers-{release}", names, pid)
            apt = system.apt_packages(persona, ident)
            installed, available = apt["installed"], apt["available"]
            build = ident.release.kernel_build
            version = installed[f"linux-image-{release}"]
            if persona.family == "ubuntu":
                # 5.15.0-164.174 for #174-Ubuntu
                self.assertEqual(version, f"{release.rsplit('-', 1)[0]}.{build.split('-Ubuntu')[0].lstrip('#')}", pid)
                for name in (f"linux-modules-{release}", f"linux-headers-{release}"):
                    self.assertEqual(installed[name], version, (pid, name))
                self.assertEqual(available[f"linux-modules-extra-{release}"], version, pid)
            else:
                self.assertIn(f"Debian {version} (", build, pid)
                self.assertEqual(available[f"linux-headers-{release}"], version, pid)
            self.assertIn(f"linux-image-{release}", names, pid)
            self.assertIn("awk", virtuals, pid)
            self.assertFalse(set(virtuals) & names, pid)
            for providers in virtuals.values():
                self.assertFalse(set(providers.split(",")) - names, pid)
            unavailable = system.apt_packages(persona, ident)["unavailable"]
            self.assertFalse(set(unavailable) & (names | set(virtuals)), pid)
            self.assertEqual("yum" in unavailable, ident.release.extra["codename"] in ("bookworm", "noble"), pid)
            if pid == "gpu-ubuntu":
                branch = ident.release.extra["nvidia_driver"].split(".")[0]
                for name in (f"nvidia-driver-{branch}", f"nvidia-utils-{branch}", f"nvidia-driver-{branch}-server"):
                    self.assertIn(name, names, name)
        for pid, sample in (("ubuntu-noble", "steam-installer"), ("ubuntu-noble", "intel-microcode"),
                            ("raspios", "firmware-misc-nonfree")):
            persona = catalog.by_id(pid)
            self.assertIn(sample, system.package_names(persona, draw(persona, 2)), (pid, sample))
        persona = catalog.by_id("centos-7")
        lines = system.package_names(persona, draw(persona, 2))
        self.assertIn("nc nmap-ncat", lines)
        self.assertIn("mysql mariadb", lines)
        self.assertNotIn("nmap-ncat", {line.split(" ")[0] for line in lines if " " in line})

    def test_name_lists_stay_small(self):
        files = sorted(system.NAMES_DIR.glob("*.txt.gz"))
        self.assertEqual([f.name for f in files], ["apt-virtual.txt.gz", "apt.txt.gz", "opkg.txt.gz", "yum.txt.gz"])
        self.assertLess(sum(f.stat().st_size for f in files), 300_000)
        for row in system._name_rows("apt.txt.gz"):
            self.assertTrue(len(row) == 2 and set(row[1]) <= set("btrjn"), row)
        for row in system._name_rows("opkg.txt.gz"):
            self.assertTrue(len(row) == 3 and set(row[1]) <= set("ab"), row)
            self.assertIn(row[2], ("core", "base", "luci", "packages", "routing", "telephony"), row)


class SystemdTreeTest(unittest.TestCase):
    """The units systemctl shows are unit files of the tree, enabled ones are wanted, and
    service --status-all says the same."""

    def test_every_unit_has_its_file_and_link(self):
        for persona in catalog.PERSONAS:
            if persona.kind != "server":
                continue
            _, out, _ = render_one(self, persona, 1)
            fs = fs_of(out)
            units = json.loads((out / system.SYSTEMD_UNITS).read_text())["units"]
            names = {u["name"] for u in units}
            for unit in units:
                node = tree.find_node(fs, unit["path"])
                self.assertIsNotNone(node, (persona.id, unit["path"]))
                if unit["state"] == "enabled" and unit["path"].startswith(("/usr/lib/systemd/", "/lib/systemd/")):
                    wants = [path for path, n in tree.iter_nodes(fs) if path.startswith("/etc/systemd/system/")
                             and path.endswith(f".wants/{unit['name']}") and tree.node_get(n, tree.A_TYPE) == tree.T_LINK]
                    self.assertTrue(wants, (persona.id, unit["name"]))
            status = (out / system.SERVICE_STATUS).read_text()
            for line in status.splitlines():
                if line.startswith(" [ + ]"):
                    script = line.split()[-1]
                    self.assertTrue(any(name.startswith(script) or name in system.INIT_UNITS.get(script, ())
                                        for name in names), (persona.id, script))
            self.assertIn("dbus.socket", names, persona.id)

    def test_every_init_script_of_status_all_is_in_init_d(self):
        for pid in ("ubuntu-noble", "ubuntu-jammy", "debian-bookworm", "raspios", "gpu-ubuntu"):
            _, out, _ = render_one(self, catalog.by_id(pid), 1)
            fs = fs_of(out)
            names = [line.split()[-1] for line in (out / system.SERVICE_STATUS).read_text().splitlines()]
            self.assertTrue(names, pid)
            for name in names:
                node = tree.find_node(fs, f"etc/init.d/{name}")
                self.assertIsNotNone(node, (pid, name))
                self.assertEqual(tree.node_get(node, tree.A_MODE) & 0o777, 0o755, (pid, name))
                if name not in ("hwclock.sh", "procps", "ssh"):
                    text = tree.node_get(node, tree.A_CONTENTS).decode()
                    self.assertIn(f"# Provides:          {name.removesuffix('.sh')}\n", text, (pid, name))
        _, out, _ = render_one(self, catalog.by_id("ubuntu-jammy"), 1)
        units = {u["name"]: u for u in json.loads((out / system.SYSTEMD_UNITS).read_text())["units"]}
        self.assertEqual((units["apport.service"]["path"], units["apport.service"]["state"]),
                         ("/etc/init.d/apport", "generated"))

    def test_unit_files_run_programs_of_the_tree(self):
        for pid in ("ubuntu-noble", "raspios", "rhel-9", "centos-7", "gpu-ubuntu", "debian-trixie"):
            _, out, _ = render_one(self, catalog.by_id(pid), 1)
            fs = fs_of(out)
            for unit in json.loads((out / system.SYSTEMD_UNITS).read_text())["units"]:
                node = tree.find_node(fs, unit["path"])
                text = tree.node_get(node, tree.A_CONTENTS)
                if not isinstance(text, bytes) or unit["path"].startswith("/etc/"):
                    continue
                for line in text.decode().splitlines():
                    if line.startswith("ExecStart="):
                        program = system.exec_program(line)
                        self.assertNotIn(":", program, (pid, unit["name"], line))
                        self.assertIsNotNone(system.program_node(fs, program), (pid, unit["name"], line))
            ps = json.loads((out / "cmdoutput.json").read_text())["command"]["ps"]
            for entry in ps:
                first = entry["COMMAND"].split()[0]
                if first.startswith("/"):
                    self.assertIsNotNone(tree.find_node(fs, first), (pid, first))

    def test_noble_listens_on_ssh_socket(self):
        _, out, _ = render_one(self, catalog.by_id("ubuntu-noble"), 1)
        units = {u["name"]: u for u in json.loads((out / system.SYSTEMD_UNITS).read_text())["units"]}
        self.assertEqual((units["ssh.socket"]["sub"], units["ssh.socket"]["state"]), ("listening", "enabled"))
        self.assertEqual(units["ssh.socket"]["triggers"], "ssh.service")
        self.assertEqual(units["ssh.service"]["triggered_by"], "ssh.socket")
        self.assertIn("irqbalance.service", units)
        self.assertEqual(units["apport.service"]["sub"], "exited")
        ps = json.loads((out / "cmdoutput.json").read_text())["command"]["ps"]
        self.assertTrue(any("irqbalance" in p["COMMAND"] for p in ps))
        _, out, _ = render_one(self, catalog.by_id("raspios"), 1)
        units = {u["name"]: u for u in json.loads((out / system.SYSTEMD_UNITS).read_text())["units"]}
        self.assertEqual(units["triggerhappy.service"]["sub"], "running")

    def test_centos_network_scripts(self):
        _, out, _ = render_one(self, catalog.by_id("centos-7"), 1)
        fs = fs_of(out)
        for name in ("functions", "netconsole", "network"):
            node = tree.find_node(fs, f"etc/rc.d/init.d/{name}")
            self.assertEqual(tree.node_get(node, tree.A_MODE) & 0o7777, 0o755 if name != "functions" else 0o644, name)
            self.assertTrue(tree.node_get(node, tree.A_CONTENTS).startswith(b"#"), name)
        units = {u["name"]: u for u in json.loads((out / system.SYSTEMD_UNITS).read_text())["units"]}
        self.assertEqual((units["network.service"]["path"], units["network.service"]["sub"]),
                         ("/etc/rc.d/init.d/network", "exited"))
        self.assertIsNotNone(tree.find_node(fs, "etc/rc.d/rc3.d/S10network"))


class ToolPackageTest(unittest.TestCase):
    """A tool Cowrie answers by name (ifconfig, unzip, nc ...) runs exactly where its package is
    installed, and is then a program on PATH."""

    def test_tools_answer_where_their_package_is_installed(self):
        for persona in catalog.PERSONAS:
            if persona.kind != "server":
                continue
            ident, out, _ = render_one(self, persona, 1)
            fs, skip = fs_of(out), skipped(out)
            installed = system.installed_tools(persona, ident)
            if "dpkg" in persona.package_managers:
                apt = json.loads((out / system.APT_PACKAGES).read_text())
                self.assertEqual({t for t in system.TOOL_PROGRAMS if t in apt["installed"]}, installed, persona.id)
                self.assertFalse({t for t in system.TOOL_PROGRAMS if t not in installed} - set(apt["available"]),
                                 persona.id)
            for tool in system.TOOL_PROGRAMS:
                for name, rel in system.tool_paths(persona, tool).items():
                    answered = name not in skip and name in system.COWRIE_TOOL_NAMES
                    on_path = tree.find_node(fs, rel) is not None
                    self.assertEqual(on_path, tool in installed, (persona.id, tool, name))
                    if name in system.COWRIE_TOOL_NAMES:
                        self.assertEqual(answered, tool in installed, (persona.id, tool, name))
        expected = {"ubuntu-noble": {"net-tools", "netcat-openbsd", "iptables", "ethtool", "bind9-dnsutils",
                                     "iputils-ping", "iproute2"},
                    "debian-bookworm": {"netcat-openbsd", "iputils-ping", "iproute2"},
                    "rhel-9": {"ethtool", "iputils-ping", "iproute2"}}
        for pid, tools in expected.items():
            persona = catalog.by_id(pid)
            self.assertEqual(system.installed_tools(persona, draw(persona, 1, NOW)), tools, pid)


class FamilyFilesTest(unittest.TestCase):
    """Small files and links of the family: dpkg's vendor, rmt, the zone link, Red Hat's installkernel,
    the cloud-init that wrote the sudoers rule."""

    def test_family_files(self):
        _, out, _ = render_one(self, catalog.by_id("ubuntu-noble"), 1)
        fs = fs_of(out)
        self.assertEqual(tree.node_get(tree.find_node(fs, "etc/dpkg/origins/default"), tree.A_TARGET), "ubuntu")
        self.assertIn(b"Vendor: Ubuntu", tree.node_get(tree.find_node(fs, "etc/dpkg/origins/ubuntu"), tree.A_CONTENTS))
        for pid in ("rhel-9", "centos-7", "openwrt"):
            _, out, _ = render_one(self, catalog.by_id(pid), 1)
            fs = fs_of(out)
            self.assertIsNone(tree.find_node(fs, "etc/rmt"), pid)
            if pid != "openwrt":
                self.assertEqual(tree.node_get(tree.find_node(fs, "etc/localtime"), tree.A_TARGET),
                                 "../usr/share/zoneinfo/Etc/UTC", pid)
                node = tree.find_node(fs, "usr/sbin/installkernel")
                self.assertTrue(tree.node_get(node, tree.A_CONTENTS).startswith(b"#!"), pid)
                self.assertEqual(tree.node_get(node, tree.A_SIZE), len(tree.node_get(node, tree.A_CONTENTS)), pid)
        _, out, _ = render_one(self, catalog.by_id("debian-bookworm"), 1)
        self.assertIsNotNone(tree.find_node(fs_of(out), "etc/rmt"))
        self.assertEqual(tree.node_get(tree.find_node(fs_of(out), "etc/localtime"), tree.A_TARGET),
                         "/usr/share/zoneinfo/Etc/UTC")

    def test_cloud_init_version_fits_the_installation(self):
        for persona in catalog.PERSONAS:
            if persona.kind != "server" or persona.family == "raspios":
                continue
            for seed in range(6):
                ident = draw(persona, seed, NOW)
                version, released = system.cloud_init_version(persona, ident)
                text = system.sudoers_files(persona, ident)["etc/sudoers.d/90-cloud-init-users"]
                self.assertIn(f"cloud-init v. {version} on", text)
                self.assertLessEqual(released, ident.installed_at, (persona.id, seed, version))


class UnitDetailTest(unittest.TestCase):
    """The units run what ps shows, scripts are text, init scripts have their run level links,
    nc goes through dpkg's alternatives."""

    def units(self, out):
        return json.loads((out / system.SYSTEMD_UNITS).read_text())["units"]

    def test_running_units_start_the_program_of_ps(self):
        for persona in catalog.PERSONAS:
            if persona.kind != "server":
                continue
            _, out, _ = render_one(self, persona, 1)
            fs = fs_of(out)
            for unit in self.units(out):
                if unit["sub"] != "running" or unit["path"].startswith("/etc/"):
                    continue
                text = tree.node_get(tree.find_node(fs, unit["path"]), tree.A_CONTENTS).decode()
                lines = [line for line in text.splitlines() if line.startswith("ExecStart=")]
                first = unit["command"].split()[0]
                if "Type=forking" in text or first.endswith(":") or first.rsplit("/", 1)[-1].startswith("python"):
                    continue
                self.assertEqual(system.exec_program(lines[0]), first, (persona.id, unit["name"]))
            if persona.family == "centos":
                postfix = tree.node_get(tree.find_node(fs, "usr/lib/systemd/system/postfix.service"), tree.A_CONTENTS)
                self.assertIn(b"Type=forking\n", postfix)
        _, out, _ = render_one(self, catalog.by_id("ubuntu-noble"), 1)
        dbus = tree.node_get(tree.find_node(fs_of(out), "usr/lib/systemd/system/dbus.service"), tree.A_CONTENTS)
        ps = json.loads((out / "cmdoutput.json").read_text())["command"]["ps"]
        self.assertIn(f"ExecStart={next(p['COMMAND'] for p in ps if 'dbus-daemon' in p['COMMAND'])}\n".encode(), dbus)

    def test_script_programs_are_text(self):
        for pid, paths in (("ubuntu-noble", ("usr/share/apport/apport", "lib/apparmor/apparmor.systemd",
                                             "usr/share/unattended-upgrades/unattended-upgrade-shutdown",
                                             "usr/bin/cloud-init")),
                           ("centos-7", ("usr/lib/systemd/rhel-dmesg", "usr/bin/kdumpctl")),
                           ("raspios", ("sbin/dphys-swapfile", "sbin/fake-hwclock")),
                           ("debian-bookworm", ("sbin/e2scrub_all",))):
            _, out, _ = render_one(self, catalog.by_id(pid), 1)
            fs = fs_of(out)
            for rel in paths:
                node = tree.find_node(fs, rel)
                text = tree.node_get(node, tree.A_CONTENTS)
                self.assertTrue(isinstance(text, bytes) and text.startswith(b"#!"), (pid, rel))
                self.assertEqual(tree.node_get(node, tree.A_MODE) & 0o777, 0o755, (pid, rel))

    def test_unit_files_of_cowries_tree_get_their_text(self):
        # Cowrie's pickle has ssh.service, ssh.socket and e2scrub_reap.service without contents
        for pid, names in (("ubuntu-noble", ("ssh.service", "ssh.socket")),
                           ("debian-bookworm", ("ssh.service", "e2scrub_reap.service"))):
            _, out, _ = render_one(self, catalog.by_id(pid), 1)
            fs = fs_of(out)
            for unit in self.units(out):
                if unit["name"] not in names:
                    continue
                text = tree.node_get(tree.find_node(fs, unit["path"]), tree.A_CONTENTS)
                self.assertIsInstance(text, bytes, (pid, unit["name"]))
                self.assertIn(f"Description={unit['description']}\n".encode(), text)
            names_found = {unit["name"] for unit in self.units(out)}
            self.assertTrue(set(names) <= names_found, (pid, names_found))
        _, out, _ = render_one(self, catalog.by_id("debian-bookworm"), 1)
        reap = tree.node_get(tree.find_node(fs_of(out), "lib/systemd/system/e2scrub_reap.service"), tree.A_CONTENTS)
        self.assertIn(b"ExecStart=/sbin/e2scrub_all -A -r\n", reap)

    def test_init_scripts_have_their_run_level_links(self):
        _, out, _ = render_one(self, catalog.by_id("ubuntu-noble"), 1)
        fs = fs_of(out)
        self.assertEqual(tree.node_get(tree.find_node(fs, "etc/rc2.d/S01cron"), tree.A_TARGET), "../init.d/cron")
        self.assertEqual(tree.node_get(tree.find_node(fs, "etc/rc0.d/K01cron"), tree.A_TARGET), "../init.d/cron")
        self.assertEqual(tree.node_get(tree.find_node(fs, "etc/rcS.d/S01kmod"), tree.A_TARGET), "../init.d/kmod")
        self.assertIsNone(tree.find_node(fs, "etc/rc0.d/K01kmod"))

    def test_netcat_goes_through_the_alternatives(self):
        _, out, _ = render_one(self, catalog.by_id("debian-bookworm"), 1)
        fs = fs_of(out)
        for name in ("nc", "netcat"):
            self.assertEqual(tree.node_get(tree.find_node(fs, f"usr/bin/{name}"), tree.A_TARGET), f"/etc/alternatives/{name}")
            self.assertEqual(tree.node_get(tree.find_node(fs, f"etc/alternatives/{name}"), tree.A_TARGET),
                             "/bin/nc.openbsd")


class SudoersTest(unittest.TestCase):
    """sudo -l lists what /etc/sudoers and /etc/sudoers.d of the persona say."""

    def test_sudoers_files_back_sudo_l(self):
        expected = {"ubuntu-noble": "90-cloud-init-users", "debian-bookworm": "90-cloud-init-users",
                    "raspios": "010_pi-nopasswd", "rhel-9": "90-cloud-init-users", "centos-7": "90-cloud-init-users"}
        for pid, name in expected.items():
            persona = catalog.by_id(pid)
            _, out, _ = render_one(self, persona, 1)
            fs = fs_of(out)
            for rel in ("etc/sudoers", f"etc/sudoers.d/{name}"):
                node = tree.find_node(fs, rel)
                self.assertIsNotNone(node, (pid, rel))
                self.assertEqual(tree.node_get(node, tree.A_MODE) & 0o7777, 0o440, (pid, rel))
                self.assertEqual(tree.node_get(node, tree.A_CONTENTS), (out / "honeyfs" / rel).read_bytes(), (pid, rel))
            sudoers = (out / "honeyfs/etc/sudoers").read_text()
            rule = (out / f"honeyfs/etc/sudoers.d/{name}").read_text()
            listed = json.loads((out / system.SUDO_LIST).read_text())
            self.assertIn(f"{persona.user} ALL=(ALL) NOPASSWD", rule, pid)
            group = "%wheel\tALL=(ALL)\tALL" if pid in ("rhel-9", "centos-7") else "%sudo\tALL=(ALL:ALL) ALL"
            self.assertIn(group, sudoers, pid)
            self.assertEqual((tree.find_node(fs, "etc/sudoers.d/README") is not None),
                             pid not in ("rhel-9", "centos-7"), pid)
            for entry in listed["defaults"]:
                self.assertIn(entry.split("=", 1)[0].rstrip("+"), sudoers, (pid, entry))
            self.assertEqual(tree.node_get(tree.find_dir(fs, "etc/sudoers.d"), tree.A_MODE) & 0o7777,
                             0o750 if pid in ("rhel-9", "centos-7") else 0o755, pid)


class RenderTest(unittest.TestCase):
    def test_cfg_names_the_prompt_style(self):
        for persona in catalog.PERSONAS:
            _, out, _ = render_one(self, persona, 0)
            self.assertIn(f"\nprompt_style = {persona.prompt_style}\n", (out / "cowrie.cfg").read_text(), persona.id)
        _, out, _ = render_one(self, catalog.by_id("rhel-9"), 0)
        self.assertIn("\nprompt_style = redhat\n", (out / "cowrie.cfg").read_text())

    def test_every_persona_renders_and_validates(self):
        for persona in catalog.PERSONAS:
            for seed in range(20):
                ident, out, keys = render_one(self, persona, seed)
                self.assertEqual(
                    validate.problems(persona, ident, out, out, keys, min_pickle=0), [],
                    (persona.id, seed))

    def test_files_that_exist_upstream_get_the_ctime_of_the_persona(self):
        # Cowrie's fs.pickle already has /etc/shadow, /var/log/... with ctimes of its own
        for persona in catalog.PERSONAS:
            for seed in range(3):
                ident = draw(persona, seed, NOW)
                base = base_tree()
                for rel in persona.builder(persona, ident).files:
                    tree.ensure_file(base, rel, b"upstream\n", ctime=1_300_000_000)
                out = tmpdir(self) / persona.id
                keys = tmpdir(self) / "etc" / persona.id
                render(persona, ident, base, out, runtime_dir=out, keys_dir=keys)
                self.assertEqual(validate.problems(persona, ident, out, out, keys, min_pickle=0), [],
                                 (persona.id, seed))

    def test_identity_json_round_trips(self):
        persona = catalog.PERSONAS[0]
        ident, out, _ = render_one(self, persona, 3)
        data = json.loads((out / "identity.json").read_text())
        self.assertEqual(data["persona"], persona.id)
        self.assertEqual(data["hostname"], ident.hostname)
        self.assertEqual(data["boot_offset"], ident.boot_offset)

    def test_phil_is_gone(self):
        persona = catalog.PERSONAS[0]
        _, out, _ = render_one(self, persona, 1)
        self.assertNotIn(b"phil", (out / "fs.pickle").read_bytes().lower())

    def test_top_is_live_with_the_style_of_each_system(self):
        wanted = {"ubuntu-noble": ("procps", "4.0.4"), "ubuntu-jammy": ("procps", "3.3.17"),
                  "debian-bookworm": ("procps", "4.0.2"), "rhel-9": ("procps", "3.3.17"),
                  "centos-7": ("procps", "3.3.10"), "ubiquiti-edgerouter-x": ("procps", "3.3.12"),
                  "openwrt": ("busybox", None), "tplink-wr841n": ("busybox", None)}
        for pid, (style, version) in wanted.items():
            with self.subTest(pid):
                persona = catalog.by_id(pid)
                ident, out, _ = render_one(self, persona, 2)
                cfg = cfg_of(out)
                self.assertFalse((out / "txtcmds/usr/bin/top").exists())
                self.assertEqual(cfg.get("shell", "top_style"), style)
                self.assertEqual(cfg.get("shell", "procps_version", fallback=None), version)
                processes = json.loads((out / "cmdoutput.json").read_text())["command"]["ps"]
                threads = cfg.getint("shell", "kernel_threads")
                # the tasks of the old static top: the ps list, the session and the kernel threads
                self.assertEqual(len(processes) + 2 + threads,
                                 persona.builder(persona, ident).tasks)
                self.assertTrue(0.0 <= cfg.getfloat("shell", "load_base") <= 0.12)

    def test_same_identity_same_output(self):
        persona = catalog.by_id("debian-bookworm")
        _, first, _ = render_one(self, persona, 9)
        _, second, _ = render_one(self, persona, 9)
        for rel in ("cmdoutput.json", "honeyfs/etc/shadow", "honeyfs/proc/meminfo", "df.json"):
            self.assertEqual((first / rel).read_text(), (second / rel).read_text(), rel)

    def test_restricted_directories_keep_owner_and_mode(self):
        # tree.ensure_file resets parent directories to 0:0 0755; the renderer
        # must put /root, /proc, /tmp and the home of the persona user back.
        for persona in catalog.PERSONAS:
            _, out, _ = render_one(self, persona, 0)
            fs = tree.load_pickle(out / "fs.pickle")
            mode = lambda rel: tree.node_get(tree.find_node(fs, rel), tree.A_MODE) & 0o7777  # noqa: E731
            self.assertEqual(mode("root"), 0o700, persona.id)
            self.assertEqual(mode("proc"), 0o555, persona.id)
            self.assertEqual(mode("tmp"), 0o1777, persona.id)
            home = "root" if persona.user == "root" else f"home/{persona.user}"
            node = tree.find_node(fs, home)
            self.assertEqual((tree.node_get(node, tree.A_UID), tree.node_get(node, tree.A_GID)),
                             (persona.uid, persona.gid), persona.id)
            for path, child in tree.iter_nodes(node, "/" + home):
                self.assertEqual(tree.node_get(child, tree.A_UID), persona.uid, (persona.id, path))


class LinkSizeTest(unittest.TestCase):
    def test_a_renamed_link_target_gives_the_link_its_size(self):
        # ls -l shows the length of the target as the size of a link, also after the renames of the
        # release (python3.11 -> python3.9, the multiarch triplet of the hardware)
        base = base_tree()
        # targets from the root, as Cowrie's pickle stores them
        tree.ensure_link(base, "usr/bin/pydoc3", "usr/bin/pydoc3.11")
        tree.ensure_link(base, "usr/lib/python3.11/_sysconfigdata__linux_x86_64-linux-gnu.py",
                         "usr/lib/python3.11/_sysconfigdata__x86_64-linux-gnu.py")
        for pid in ("rhel-9", "raspios"):
            with self.subTest(pid):
                persona = catalog.by_id(pid)
                out = tmpdir(self) / pid
                render(persona, draw(persona, 1, NOW), base, out, runtime_dir=out, keys_dir=out / "keys")
                fs = tree.load_pickle(out / "fs.pickle")
                links = [(path, tree.node_get(node, tree.A_SIZE), tree.node_get(node, tree.A_TARGET))
                         for path, node in tree.iter_nodes(fs) if tree.node_get(node, tree.A_TYPE) == tree.T_LINK]
                self.assertTrue(any(target in ("pydoc3.9", "_sysconfigdata__aarch64-linux-gnu.py")
                                    for _, _, target in links), links)
                self.assertEqual([link for link in links if link[1] != len(link[2] or "")], [])


class BoundaryTest(unittest.TestCase):
    """The edges of the identity ranges: every persona must still render and validate."""

    def check(self, persona, ident):
        _, out, keys = render_ident(self, persona, ident)
        self.assertEqual(validate.problems(persona, ident, out, out, keys, min_pickle=0), [],
                         (persona.id, ident.boot_offset, ident.installed_at, ident.now))

    def test_minimum_uptime_with_install_at_the_minimum_age(self):
        for persona in catalog.PERSONAS:
            for seed in range(3):
                ident = draw(persona, seed, NOW)
                self.check(persona, dataclasses.replace(ident, boot_offset=DAY, installed_at=NOW - 90 * DAY))

    def test_install_exactly_at_boot(self):
        for persona in catalog.PERSONAS:
            for seed in range(3):
                ident = draw(persona, seed, NOW)
                # the oldest kernel of the pool that fits, so the boot can be 100 days back
                oldest = min((r for r in persona.releases if fits(r, ident.hardware)), key=kernel_time)
                release = specialize(oldest, ident.hardware)
                self.check(persona, dataclasses.replace(ident, release=release, boot_offset=100 * DAY,
                                                        installed_at=NOW - 100 * DAY))

    def test_kept_identity_a_year_after_its_draw(self):
        # COWRIE_PERSONA_ROTATION: the boot stays, the uptime grows beyond a fresh draw's
        for persona in catalog.PERSONAS:
            for seed in range(2):
                first = draw(persona, seed, NOW)
                self.check(persona, restore(persona, to_state(persona, first), NOW + 365 * DAY))

    def test_one_second_after_utc_midnight(self):
        midnight = NOW // DAY * DAY + 1
        for persona in catalog.PERSONAS:
            for seed in range(3):
                ident = draw(persona, seed, midnight)
                self.check(persona, ident)
                self.check(persona, dataclasses.replace(ident, boot_offset=DAY))


class KeptHistoryTest(unittest.TestCase):
    """COWRIE_PERSONA_ROTATION: a kept identity shows the history it showed when it
    was drawn; only what runs now (the clock, uptime, the ps START of today) moves."""

    # what follows the clock of the start: the runtime config, the identity, the units
    # with their CPU time, the date nvidia-smi prints; /proc and /dev/pts are compared apart
    CLOCK_FILES = {"cowrie.cfg", "identity.json", "systemd-units.json", "txtcmds/usr/bin/nvidia-smi"}

    def files(self, out):
        return {str(p.relative_to(out)): p.read_bytes() for p in sorted(out.rglob("*"))
                if p.is_file() and p.name not in ("fs.pickle", "cmdoutput.json")}

    def nodes(self, out):
        fs = tree.load_pickle(out / "fs.pickle")
        # a folder without its children: those are compared on their own
        return {path: [v for i, v in enumerate(node) if not (i == tree.A_CONTENTS and node[tree.A_TYPE] == tree.T_DIR)]
                for path, node in tree.iter_nodes(fs)
                if not path.startswith(("/proc/", "/dev/pts/")) and path not in ("/proc", "/dev/pts")}

    def processes(self, out, ident):
        rows = json.loads((out / "cmdoutput.json").read_text())["command"]["ps"]
        return [{k: v for k, v in row.items() if k != "START"} for row in rows]

    def test_kept_identity_renders_the_same_history(self):
        for persona, seed in ((p, s) for p in catalog.PERSONAS for s in (3, 33)):
            first = draw(persona, seed, NOW)
            _, out, _ = render_ident(self, persona, first)
            files, nodes = self.files(out), self.nodes(out)
            procs = self.processes(out, first)
            for days in (1, 7, 30, 365):
                with self.subTest(persona=persona.id, seed=seed, days=days):
                    kept = restore(persona, to_state(persona, first), NOW + days * DAY + 3600)
                    _, again, _ = render_ident(self, persona, kept)
                    later = self.files(again)
                    self.assertEqual(sorted(later), sorted(files))
                    changed = sorted(rel for rel in files if later[rel] != files[rel]
                                     and rel.split("/", 1)[-1] not in self.CLOCK_FILES
                                     and rel not in self.CLOCK_FILES and not rel.startswith("honeyfs/proc/"))
                    self.assertEqual(changed, [])
                    again_nodes = self.nodes(again)
                    self.assertEqual(sorted(again_nodes), sorted(nodes))
                    self.assertEqual([path for path in nodes if again_nodes[path] != nodes[path]], [])
                    self.assertEqual(self.processes(again, kept), procs)


class FileSystemTableTest(unittest.TestCase):
    """mount, /proc/mounts and df show one table of file systems per persona."""

    def rows(self, persona_id, provider=None):
        persona = catalog.by_id(persona_id)
        for seed in range(400):
            ident = draw(persona, seed, NOW)
            if provider is None or ident.hardware.provider == provider:
                break
        content = persona.builder(persona, ident)
        return ident, content, {row["target"]: row for row in content.filesystems}

    def test_every_persona_mounts_what_df_lists(self):
        for persona in catalog.PERSONAS:
            ident = draw(persona, 3, NOW)
            content = persona.builder(persona, ident)
            mount = content.txtcmds["bin/mount"].splitlines()
            proc = content.files["proc/mounts"].splitlines()
            self.assertNotIn("bin/df", content.txtcmds, persona.id)
            self.assertEqual(len(mount), len(content.filesystems), persona.id)
            for row, line, entry in zip(content.filesystems, mount, proc):
                self.assertEqual(line, f"{row['source']} on {row['target']} type {row['fstype']} ({row['options']})")
                self.assertEqual(entry, f"{row['source']} {row['target']} {row['fstype']} {row['options']} 0 0")
                self.assertLessEqual(row["used"] + row["avail"], row["size"], persona.id)
                self.assertLessEqual(row["iused"], row["inodes"], persona.id)

    def test_red_hat_names_devtmpfs_and_its_own_shares(self):
        ident, _, rows = self.rows("rhel-9")
        self.assertEqual((rows["/dev"]["source"], rows["/dev"]["size"]), ("devtmpfs", 4096))
        self.assertEqual(rows["/run"]["size"], ident.hardware.mem_kb // 5)
        self.assertIn("seclabel", rows["/dev/shm"]["options"])
        self.assertEqual(rows["/"]["avail"], rows["/"]["size"] - rows["/"]["used"])  # xfs reserves nothing
        ident, _, rows = self.rows("centos-7")
        self.assertEqual(rows["/sys/fs/cgroup"]["fstype"], "tmpfs")
        self.assertEqual(rows["/run"]["size"], ident.hardware.mem_kb // 2)
        self.assertNotIn("inode64", rows["/dev/shm"]["options"])  # kernel 3.10

    def test_ubuntu_cloud_images_mount_the_efi_partition_of_their_fstab(self):
        for provider, device in (("kvm", "/dev/sda15"), ("aws", "/dev/nvme0n1p15")):
            ident, content, rows = self.rows("ubuntu-noble", provider)
            self.assertEqual((rows["/boot/efi"]["source"], rows["/boot/efi"]["fstype"]), (device, "vfat"))
            self.assertIn("LABEL=UEFI\t/boot/efi", content.files["etc/fstab"])
            self.assertEqual(rows["/"]["size"], ident.hardware.disk_kb)
            self.assertEqual(rows["/dev"]["source"], "udev")
        _, _, rows = self.rows("debian-bookworm")
        self.assertNotIn("/boot/efi", rows)

    def test_df_style_follows_the_userland(self):
        for persona in catalog.PERSONAS:
            ident = draw(persona, 2, NOW)
            content = persona.builder(persona, ident)
            data = system.df_table(persona, ident, content.filesystems)
            # df is a program of the coreutils or the BusyBox that wc, sort and seq are (EdgeOS: procps
            # top of Debian, the BusyBox df of its firmware)
            self.assertEqual(data["style"], system.text_keys(persona, ident)["text_style"], persona.id)
            if persona.family != "ubiquiti":
                self.assertEqual(data["style"], "gnu" if content.top_style == "procps" else "busybox", persona.id)
            if data["style"] == "busybox":
                self.assertEqual(len(data["busybox"]), 3)
            else:
                self.assertIn(data["coreutils"], system.COREUTILS_YEARS)


class ServerContentTest(unittest.TestCase):
    def test_gpu_tokens_differ_per_seed_and_land_in_files(self):
        persona = catalog.by_id("gpu-ubuntu")
        a, out_a, _ = render_one(self, persona, 1)
        b, _, _ = render_one(self, persona, 2)
        self.assertNotEqual(a.tokens["hf"], b.tokens["hf"])
        self.assertIn(a.tokens["hf"], (out_a / "honeyfs/home/ubuntu/.cache/huggingface/token").read_text())
        smi = (out_a / "txtcmds/usr/bin/nvidia-smi").read_text()
        self.assertEqual(smi.count(a.hardware.gpus[0]), len(a.hardware.gpus))

    def test_gpu_server_details(self):
        persona = catalog.by_id("gpu-ubuntu")
        for seed in range(12):
            ident, out, _ = render_one(self, persona, seed)
            r, hw = ident.release, ident.hardware
            smi = (out / "txtcmds/usr/bin/nvidia-smi").read_text()
            self.assertIn(f"Driver Version: {r.extra['nvidia_driver']}", smi)
            self.assertIn(f"CUDA Version: {r.extra['cuda']}", smi)
            self.assertEqual(smi.count(f"/  {hw.gpu_mem_mib}MiB"), len(hw.gpus))
            # every table line has the width of the nvidia-smi frame
            widths = {len(line) for line in smi.splitlines() if line.startswith(("|", "+"))}
            self.assertEqual(widths, {91}, seed)
            version = (out / "honeyfs/proc/driver/nvidia/version").read_text()
            self.assertIn(f"Kernel Module  {r.extra['nvidia_driver']}  ", version)
            lspci = (out / "txtcmds/usr/bin/lspci").read_text()
            self.assertEqual(len(re.findall(r"^\S+ (3D|VGA compatible) controller: NVIDIA Corporation", lspci, re.M)),
                             len(hw.gpus))
            ps = json.loads((out / "cmdoutput.json").read_text())["command"]["ps"]
            commands = [p["COMMAND"] for p in ps]
            self.assertIn("/usr/local/bin/ollama serve", commands)
            self.assertIn("/opt/conda/bin/python /opt/conda/bin/jupyter-lab --no-browser --port=8888", commands)
            home = out / "honeyfs/home/ubuntu"
            env = (home / ".env").read_text()
            self.assertIn(f"OPENAI_API_KEY={ident.tokens['openai']}", env)
            self.assertIn(f"HF_TOKEN={ident.tokens['hf']}", env)
            history = (home / ".bash_history").read_text()
            for line in ("nvidia-smi", "ollama pull llama3.1:8b", "conda activate torch"):
                self.assertIn(line, history)

    def test_raspberry_pi_names_its_board(self):
        persona = catalog.by_id("raspios")
        for seed in range(8):
            ident, out, _ = render_one(self, persona, seed)
            board = ident.hardware.board
            self.assertEqual((out / "honeyfs/proc/device-tree/model").read_text(), board + "\0")
            cpuinfo = (out / "honeyfs/proc/cpuinfo").read_text()
            self.assertIn(f"Model\t\t: {board}", cpuinfo)
            self.assertNotIn("model name", cpuinfo)  # arm64 cpuinfo has none, lscpu names the core
            self.assertIn(ident.hardware.cpu_model, (out / "txtcmds/usr/bin/lscpu").read_text())
            issue = (out / "honeyfs/etc/rpi-issue").read_text()
            self.assertRegex(issue, r"^Raspberry Pi reference \d{4}-\d{2}-\d{2}\nGenerated using pi-gen, "
                                    r"https://github.com/RPi-Distro/pi-gen, [0-9a-f]{40}, stage4\n$")
            self.assertIn("arm_64bit=1", (out / "honeyfs/boot/firmware/config.txt").read_text())
            ps = json.loads((out / "cmdoutput.json").read_text())["command"]["ps"]
            self.assertEqual(ps[0]["COMMAND"], "/sbin/init splash")
            self.assertTrue(any(p["COMMAND"].startswith("avahi-daemon: running") for p in ps))

    def test_centos_is_end_of_life_on_the_vault(self):
        persona = catalog.by_id("centos-7")
        ident, out, _ = render_one(self, persona, 4)
        for rel in ("etc/redhat-release", "etc/centos-release"):
            self.assertEqual((out / "honeyfs" / rel).read_text(), "CentOS Linux release 7.9.2009 (Core)\n")
        repo = (out / "honeyfs/etc/yum.repos.d/CentOS-Base.repo").read_text()
        self.assertIn("baseurl=http://vault.centos.org/7.9.2009/os/$basearch/", repo)
        self.assertNotIn("mirrorlist=", repo.replace("#mirrorlist=", ""))
        logs = sorted(p.name for p in (out / "honeyfs/var/log").iterdir() if p.name.startswith("yum.log"))
        self.assertTrue(logs and logs[0] == "yum.log", logs)
        ps = json.loads((out / "cmdoutput.json").read_text())["command"]["ps"]
        self.assertTrue(ps[0]["COMMAND"].startswith("/usr/lib/systemd/systemd "), ps[0])
        passwd = (out / "honeyfs/etc/passwd").read_text()
        self.assertIn("centos:x:1000:1000:Cloud User:/home/centos:/bin/bash", passwd)
        self.assertNotIn("www-data", passwd)

    def test_each_server_says_what_it_is(self):
        expected = {
            "ubuntu-noble": ("etc/os-release", 'VERSION_CODENAME=noble'),
            "ubuntu-jammy": ("etc/os-release", 'VERSION="22.04.5 LTS (Jammy Jellyfish)"'),
            "debian-trixie": ("etc/os-release", 'VERSION="13 (trixie)"'),
            "debian-bookworm": ("etc/os-release", 'VERSION="12 (bookworm)"'),
            "rhel-9": ("etc/redhat-release", "Red Hat Enterprise Linux release 9."),
            "centos-7": ("etc/os-release", 'PRETTY_NAME="CentOS Linux 7 (Core)"'),
            "raspios": ("etc/os-release", 'VERSION_CODENAME=bookworm'),
            "gpu-ubuntu": ("etc/os-release", "ID=ubuntu"),
        }
        for pid, (rel, text) in expected.items():
            _, out, _ = render_one(self, catalog.by_id(pid), 6)
            self.assertIn(text, (out / "honeyfs" / rel).read_text(), pid)
            version = (out / "honeyfs/proc/version").read_text()
            # a real /proc/version names the compiler between builder and build string
            self.assertRegex(version, r"^Linux version \S+ \(\S+@\S+\) \(.*(gcc|GCC).*\) #", pid)

    def test_upgrades_are_logged_between_the_update_and_the_boot(self):
        for pid in ("ubuntu-noble", "debian-bookworm", "rhel-9", "centos-7", "raspios"):
            persona = catalog.by_id(pid)
            for seed in range(10):
                ident, out, _ = render_one(self, persona, seed)
                for path in (out / "honeyfs/var/log").rglob("*"):
                    if not path.is_file():
                        continue
                    for y, m, d in re.findall(r"(\d{4})-(\d{2})-(\d{2})", path.read_text()):
                        day = calendar.timegm((int(y), int(m), int(d), 0, 0, 0))
                        self.assertGreaterEqual(day + DAY, updated_at(ident.release), (pid, seed, path.name))

    def test_noble_on_aws_looks_like_a_nitro_instance(self):
        persona = catalog.by_id("ubuntu-noble")
        for seed in range(400):
            ident = draw(persona, seed, NOW)
            if ident.hardware.provider == "aws":
                break
        else:
            self.fail("no AWS draw")
        _, out, keys = render_ident(self, persona, ident)
        lspci = (out / "txtcmds/usr/bin/lspci").read_text()
        self.assertIn("Amazon.com, Inc. Elastic Network Adapter (ENA)", lspci)
        self.assertIn("NVMe EBS Controller", lspci)
        self.assertNotIn("Virtio", lspci)
        self.assertIn("/dev/root", [row["source"] for row in json.loads((out / "df.json").read_text())["filesystems"]])
        self.assertTrue(ident.release.kernel_version.endswith("-aws"))
        self.assertNotIn("PREEMPT_DYNAMIC", ident.release.kernel_build)
        self.assertEqual(validate.problems(persona, ident, out, out, keys, min_pickle=0), [])

    def test_image_install_after_the_next_patch_level_logs_nothing(self):
        persona = catalog.by_id("debian-bookworm")
        for seed in range(400):
            ident = draw(persona, seed, NOW)
            if ident.release.ssh_banner.endswith("deb12u1"):
                break
        else:
            self.fail("no 12.2 draw")
        _, out, _ = render_ident(self, persona, ident)
        self.assertEqual((out / "honeyfs/var/log/apt/history.log").read_text(), "")

    def test_kernel_command_line_names_the_root_of_each_server(self):
        for persona in catalog.PERSONAS:
            if persona.kind != "server":
                continue
            for seed in range(6):
                _, out, _ = render_one(self, persona, seed)
                dmesg = (out / "txtcmds/bin/dmesg").read_text()
                # not the generic line of the devices
                self.assertNotIn("console=ttyS0 root=/dev/sda1 ro quiet", dmesg, persona.id)
                self.assertIn("root=", dmesg, persona.id)
                self.assertTrue((out / "honeyfs/etc/fstab").read_text(), persona.id)


class DeviceContentTest(unittest.TestCase):
    def ps(self, out):
        return json.loads((out / "cmdoutput.json").read_text())["command"]["ps"]

    def test_ipcam_is_a_davinci_camera(self):
        persona = catalog.by_id("ipcam")
        for seed in range(6):
            ident, out, _ = render_one(self, persona, seed)
            r = ident.release
            self.assertIn(r.extra["firmware_version"], (out / "honeyfs/etc/version").read_text())
            self.assertIn("davinci", (out / "honeyfs/etc/init.d/rcS").read_text())
            commands = [p["COMMAND"] for p in self.ps(out)]
            for command in ("/home/davinci", "/bin/sh /home/process_cfg.sh", "/usr/sbin/telnetd", "/sbin/dropbear"):
                self.assertTrue(any(c.startswith(command) for c in commands), (seed, command, commands))
            busybox = (out / "busybox-help.txt").read_text()
            self.assertTrue(busybox.startswith(f"{r.extra['busybox']} multi-call binary."), busybox)
            cpuinfo = (out / "honeyfs/proc/cpuinfo").read_text()
            self.assertIn("model name\t: ARMv7 Processor rev 5 (v7l)", cpuinfo)
            self.assertEqual(cpuinfo.count("processor\t:"), 1)

    def test_dlink_has_its_vendor_files_and_kernel(self):
        persona = catalog.by_id("dlink-dir859")
        ident, out, _ = render_one(self, persona, 2)
        r = ident.release
        self.assertEqual((out / "honeyfs/etc/config/buildver").read_text(), r.os_version + "\n")
        self.assertEqual((out / "honeyfs/etc/config/image_sign").read_text(), "wrgac37_dlink.2013gui_dir859\n")
        self.assertFalse((out / "honeyfs/etc/os-release").exists())
        self.assertIn("etc/os-release", persona.remove_paths)
        cpuinfo = (out / "honeyfs/proc/cpuinfo").read_text()
        self.assertIn("system type\t\t: QCA956x", cpuinfo)
        self.assertIn("cpu model\t\t: MIPS 74Kc", cpuinfo)
        self.assertNotIn("isa\t", cpuinfo)  # 2.6.31 has no isa line
        self.assertTrue(r.kernel_version.startswith("2.6.31-"))

    def test_openwrt_is_a_mt7621_router(self):
        persona = catalog.by_id("openwrt")
        for seed in range(6):
            ident, out, _ = render_one(self, persona, seed)
            r = ident.release
            release = (out / "honeyfs/etc/openwrt_release").read_text()
            self.assertIn(f"DISTRIB_RELEASE='{r.os_version}'", release)
            self.assertIn("DISTRIB_TARGET='ramips/mt7621'", release)
            os_release = (out / "honeyfs/etc/os-release").read_text()
            self.assertIn(f'OPENWRT_RELEASE="OpenWrt {r.os_version} {r.extra["revision"]}"', os_release)
            self.assertIn(f" OpenWrt {r.os_version}, {r.extra['revision']}\n", (out / "honeyfs/etc/banner").read_text())
            cpuinfo = (out / "honeyfs/proc/cpuinfo").read_text()
            self.assertIn("system type\t\t: MediaTek MT7621 ver:1 eco:3", cpuinfo)
            self.assertIn(f"machine\t\t\t: {ident.hardware.machine}", cpuinfo)
            self.assertEqual(cpuinfo.count("cpu model\t\t: MIPS 1004Kc"), 4)
            self.assertIn("overlayfs:/overlay on / type overlay", (out / "txtcmds/bin/mount").read_text())
            cfg = (out / "cowrie.cfg").read_text()
            # what ssh-audit sees of dropbear 2020.81 / 2022.82 on OpenWrt, and uname -a of BusyBox
            self.assertIn("\nciphers = aes128-ctr,aes256-ctr\n", cfg)
            self.assertIn("\nmacs = hmac-sha1,hmac-sha2-256\n", cfg)
            self.assertIn("\ncompression = none\n", cfg)
            self.assertIn("\npublic_key_auth = ssh-ed25519,ssh-rsa\n", cfg)
            # dropbear 2020.79 and later: rsa-sha2-256 before ssh-rsa, no rsa-sha2-512
            self.assertIn("\nhost_key_algorithms = ssh-ed25519,rsa-sha2-256,ssh-rsa\n", cfg)
            self.assertIn("\noperating_system = GNU/Linux\n", cfg)

    def test_bare_metal_x86_nas_cpus(self):
        no_avx = ("J1900", "C3538", "N5105", "J4125")
        for pid in ("qnap-qts", "synology-dsm"):
            persona = catalog.by_id(pid)
            for seed in range(8):
                ident, out, _ = render_one(self, persona, seed)
                cpuinfo = (out / "honeyfs/proc/cpuinfo").read_text()
                flags = re.search(r"^flags\t\t: (.*)$", cpuinfo, re.M).group(1).split()
                self.assertNotIn("hypervisor", flags, (pid, seed))
                self.assertTrue(any(m in ident.hardware.cpu_model for m in no_avx), ident.hardware)
                self.assertNotIn("avx", flags, (pid, seed))
                self.assertNotIn("avx2", flags, (pid, seed))
                lspci = (out / "txtcmds/usr/bin/lspci").read_text()
                self.assertIn("Intel Corporation", lspci)

    def test_ps_start_follows_procps(self):
        for persona in catalog.PERSONAS:
            for seed in range(4):
                ident, out, _ = render_one(self, persona, seed)
                for p in self.ps(out):
                    self.assertRegex(p["START"], r"^(\d{2}:\d{2}|[A-Z][a-z]{2}\d{2}|\d{4})$", (persona.id, p))

    def test_synology_unique_names_the_model_without_ds(self):
        persona = catalog.by_id("synology-dsm")
        seen = set()
        for seed in range(12):
            ident, out, _ = render_one(self, persona, seed)
            text = (out / "honeyfs/etc.defaults/synoinfo.conf").read_text()
            unique = re.search(r'^unique="(.*)"$', text, re.M).group(1)
            self.assertRegex(unique, r"^synology_(denverton|geminilake)_(1618|920)\+$")
            self.assertNotIn("_ds", unique)
            seen.add(unique)
        self.assertEqual(seen, {"synology_denverton_1618+", "synology_geminilake_920+"})

    def test_each_device_says_what_it_is(self):
        expected = {
            "tplink-wr841n": ("busybox-help.txt", "BusyBox v1.19.2 "),
            "zyxel-nas326": ("honeyfs/firmware/mnt/info/fwversion", "V5.21(AAZF."),
            "qnap-qts": ("honeyfs/etc/config/uLinux.conf", "Model = TS-"),
            "synology-dsm": ("honeyfs/etc.defaults/VERSION", 'productversion="7.'),
            "ubiquiti-edgerouter-x": ("honeyfs/etc/version", "EdgeRouter.ER-e50.v2.0.9-hotfix."),
        }
        for pid, (rel, text) in expected.items():
            _, out, _ = render_one(self, catalog.by_id(pid), 6)
            self.assertIn(text, (out / rel).read_text(), pid)
