import calendar
import configparser
import dataclasses
import json
import re
import unittest

from tests.helpers import base_tree, tmpdir
from tpot_personas import catalog, system, tree, validate
from tpot_personas.common import kernel_series
from tpot_personas.identity import DAY, draw, fits, kernel_time, specialize, updated_at
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


class BusyboxTest(unittest.TestCase):
    def test_devices_with_busybox_run_cowries_busybox_with_their_help(self):
        with_busybox = [p for p in catalog.PERSONAS if any(r.extra.get("busybox") for r in p.releases)]
        self.assertEqual({p.id for p in with_busybox}, {"dlink-dir859", "tplink-wr841n", "zyxel-nas326", "openwrt",
                                                       "ubiquiti-edgerouter-x", "ipcam"})
        for persona in with_busybox:
            for seed in range(3):
                ident, out, _ = render_one(self, persona, seed)
                banner = ident.release.extra["busybox"]
                self.assertNotIn("busybox", skipped(out), persona.id)
                self.assertNotIn("/bin/busybox", skipped(out), persona.id)
                self.assertEqual(cfg_of(out).get("shell", "busybox_help"), f"{out}/busybox-help.txt")
                text = (out / "busybox-help.txt").read_text()
                self.assertTrue(text.startswith(f"{banner} multi-call binary.\n"), (persona.id, text[:80]))
                self.assertIn("wget", text)
                self.assertFalse((out / "txtcmds/bin/busybox").exists(), persona.id)

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
    """Servers run Cowrie's sudo, service, apt / apt-get (Debian) and yum (CentOS 7) (F7)."""

    def test_python_commands_and_txtcmds(self):
        for persona in catalog.PERSONAS:
            ident, out, _ = render_one(self, persona, 1)
            fs, skip = fs_of(out), skipped(out)
            txtcmds = {str(p.relative_to(out / "txtcmds")) for p in (out / "txtcmds").rglob("*") if p.is_file()}
            server = persona.kind == "server"
            debian = server and "dpkg" in persona.package_managers
            answered = {"sudo": server, "service": server, "apt": debian, "apt-get": debian,
                        "yum": persona.family == "centos"}
            for name, python in answered.items():
                self.assertEqual(name not in skip, python, (persona.id, name))
                path = "usr/sbin/service" if name == "service" else f"usr/bin/{name}"
                if python:
                    self.assertNotIn(path, txtcmds, (persona.id, name))
                    self.assertIsNotNone(tree.find_node(fs, path), (persona.id, name))
            if debian:
                self.assertIn("usr/bin/dpkg", txtcmds, persona.id)
            if persona.family == "rhel":
                self.assertEqual({"usr/bin/dnf", "usr/bin/yum", "usr/bin/rpm"} & txtcmds,
                                 {"usr/bin/dnf", "usr/bin/yum", "usr/bin/rpm"})
            if persona.family == "centos":
                self.assertIn("usr/bin/rpm", txtcmds)

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

    def test_same_identity_same_output(self):
        persona = catalog.by_id("debian-bookworm")
        _, first, _ = render_one(self, persona, 9)
        _, second, _ = render_one(self, persona, 9)
        for rel in ("cmdoutput.json", "honeyfs/etc/shadow", "honeyfs/proc/meminfo", "txtcmds/usr/bin/top"):
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

    def test_one_second_after_utc_midnight(self):
        midnight = NOW // DAY * DAY + 1
        for persona in catalog.PERSONAS:
            for seed in range(3):
                ident = draw(persona, seed, midnight)
                self.check(persona, ident)
                self.check(persona, dataclasses.replace(ident, boot_offset=DAY))


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
        self.assertIn("/dev/root", (out / "txtcmds/bin/df").read_text())
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
