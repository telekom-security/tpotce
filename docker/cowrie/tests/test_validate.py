import configparser
import dataclasses
import json
import re
import time
import unittest

from tests.helpers import base_tree
from tests.test_render import NOW, render_ident, render_one
from tpot_personas import PersonaError, catalog, tree, validate
from tpot_personas.identity import draw, stale_at
from tpot_personas.render import render


class ValidateNegativeTest(unittest.TestCase):
    def setUp(self):
        self.persona = catalog.by_id("ubuntu-jammy")
        self.ident, self.out, self.keys = render_one(self, self.persona, 5)

    def problems(self):
        return " | ".join(validate.problems(self.persona, self.ident, self.out, self.out, self.keys, min_pickle=0))

    def edit(self, rel, old, new):
        path = self.out / rel
        text = path.read_text()
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1))

    def test_clean_render_has_no_problems(self):
        self.assertEqual(self.problems(), "")
        validate.validate(self.persona, self.ident, self.out, self.out, self.keys, min_pickle=0)

    def test_validate_raises_with_persona_id(self):
        (self.out / "txtcmds/usr/bin/nproc").write_text("97\n")
        with self.assertRaisesRegex(PersonaError, "^ubuntu-jammy: .*nproc"):
            validate.validate(self.persona, self.ident, self.out, self.out, self.keys, min_pickle=0)

    def test_missing_file(self):
        (self.out / "cmdoutput.json").unlink()
        self.assertIn("missing cmdoutput.json", self.problems())

    def test_kernel_mismatch(self):
        self.edit("honeyfs/proc/version", self.ident.release.kernel_version, "9.9.9-0-generic")
        self.assertIn("kernel", self.problems())

    def test_kernel_mismatch_in_dmesg(self):
        self.edit("txtcmds/bin/dmesg", self.ident.release.kernel_version, "9.9.9-0-generic")
        self.assertIn("kernel", self.problems())

    def test_cpu_count_mismatch(self):
        (self.out / "txtcmds/usr/bin/nproc").write_text("97\n")
        self.assertIn("nproc", self.problems())

    def test_cpuinfo_mismatch(self):
        self.edit("honeyfs/proc/cpuinfo", "processor\t: 0", "cpu\t: 0")
        self.assertIn("cpuinfo", self.problems())

    def test_lscpu_mismatch(self):
        self.edit("txtcmds/usr/bin/lscpu", self.ident.hardware.cpu_model, "Other CPU")
        self.assertIn("lscpu", self.problems())

    def test_memory_mismatch(self):
        self.edit("honeyfs/proc/meminfo", str(self.ident.hardware.mem_kb), "1")
        self.assertIn("meminfo", self.problems())

    def test_meminfo_lacks_a_key_of_free(self):
        self.edit("honeyfs/proc/meminfo", "Shmem:", "ShmemX:")
        self.assertIn("meminfo lacks Shmem", self.problems())

    def test_top_memory_mismatch(self):
        self.edit("txtcmds/usr/bin/top", f"{self.ident.hardware.mem_kb / 1024:.1f} total", "1.0 total")
        self.assertIn("top", self.problems())

    def test_hostname_mismatch(self):
        (self.out / "honeyfs/etc/hostname").write_text("other\n")
        self.assertIn("hostname", self.problems())

    def test_cfg_mismatch(self):
        self.edit("cowrie.cfg", f"boot_offset = {self.ident.boot_offset}", "boot_offset = 1")
        self.assertIn("cfg boot_offset", self.problems())

    def test_banner_and_ssh_version_disagree(self):
        cfg = configparser.ConfigParser(interpolation=None)
        cfg.read(self.out / "cowrie.cfg")
        cfg["shell"]["ssh_version"] = "OpenSSH_6.0p1, OpenSSL 1.0.1"
        with (self.out / "cowrie.cfg").open("w") as handle:
            cfg.write(handle)
        self.assertIn("ssh_version", self.problems())

    def test_unsupported_cipher(self):
        self.edit("cowrie.cfg", "aes128-ctr", "blowfish-cbc")
        self.assertIn("cipher", self.problems())

    def test_os_release_mismatch(self):
        self.edit("honeyfs/etc/os-release", "ID=ubuntu", "ID=debian")
        self.assertIn("os-release", self.problems())

    def test_future_date_in_ps(self):
        self.edit("cmdoutput.json", '"START": "', '"START": "Dec31x')
        self.assertIn("START", self.problems())

    def test_duplicate_pid(self):
        path = self.out / "cmdoutput.json"
        data = json.loads(path.read_text())
        data["command"]["ps"][1]["PID"] = 1
        path.write_text(json.dumps(data))
        self.assertIn("PID", self.problems())

    def test_log_date_outside_lifetime(self):
        self.edit("honeyfs/var/log/apt/history.log", "Start-Date: ", "Start-Date: 2001-01-01  00:00:00\n#")
        self.assertIn("date", self.problems())

    def test_shadow_in_the_future(self):
        text = (self.out / "honeyfs/etc/shadow").read_text().splitlines()
        fields = text[0].split(":")
        fields[2] = str(self.ident.now // 86400 + 10)
        text[0] = ":".join(fields)
        (self.out / "honeyfs/etc/shadow").write_text("\n".join(text) + "\n")
        self.assertIn("date", self.problems())

    def test_forbidden_marker(self):
        (self.out / "honeyfs/etc/motd").write_text("Linux svr04 2.6.26-2-686\n")
        self.assertIn("forbidden", self.problems())

    def test_marker_in_the_runtime_path_is_not_persona_content(self):
        out = self.out.parent / "philtmp"
        render(self.persona, self.ident, base_tree(), out, runtime_dir=out, keys_dir=self.keys)
        self.assertEqual(validate.problems(self.persona, self.ident, out, out, self.keys, min_pickle=0), [])

    def test_package_manager_without_command(self):
        (self.out / "txtcmds/usr/bin/dpkg").unlink()
        self.assertIn("package manager", self.problems())

    def test_ctime_in_the_future(self):
        fs = tree.load_pickle(self.out / "fs.pickle")
        tree.node_set(tree.find_node(fs, "etc/passwd"), tree.A_CTIME, self.ident.now + 86400)
        tree.dump_pickle(fs, self.out / "fs.pickle")
        self.assertIn("ctime", self.problems())

    def test_open_root_home(self):
        fs = tree.load_pickle(self.out / "fs.pickle")
        tree.ensure_dir(fs, "root")
        tree.dump_pickle(fs, self.out / "fs.pickle")
        self.assertIn("owner", self.problems())

    def test_identity_json_that_is_no_object(self):
        (self.out / "identity.json").write_text("[1, 2, 3]\n")
        self.assertIn("identity.json", self.problems())

    def test_grep_c_processor_counts_the_model_name_too(self):
        self.edit("honeyfs/proc/cpuinfo", f"model name\t: {self.ident.hardware.cpu_model}",
                  "model name\t: Common KVM processor")
        self.assertIn("grep -c processor", self.problems())

    def test_openssh_upgrade_after_boot(self):
        path = self.out / "honeyfs/var/log/apt/history.log"
        late = time.strftime("%Y-%m-%d  %H:%M:%S", time.gmtime(self.ident.now - 60))
        blocks = path.read_text().split("\n\n")
        changed = [re.sub(r"End-Date: .*", f"End-Date: {late}", b) if "\nUpgrade: openssh" in b else b
                   for b in blocks]
        self.assertNotEqual(blocks, changed)
        path.write_text("\n\n".join(changed))
        self.assertIn("sshd", self.problems())

    def test_log_ctime_older_than_its_newest_entry(self):
        fs = tree.load_pickle(self.out / "fs.pickle")
        tree.node_set(tree.find_node(fs, "var/log/apt/history.log"), tree.A_CTIME, self.ident.installed_at)
        tree.dump_pickle(fs, self.out / "fs.pickle")
        self.assertIn("ctime of /var/log/apt/history.log", self.problems())

    def test_shadow_ctime_older_than_its_last_change(self):
        fs = tree.load_pickle(self.out / "fs.pickle")
        tree.node_set(tree.find_node(fs, "etc/shadow"), tree.A_CTIME, self.ident.installed_at - 86400)
        tree.dump_pickle(fs, self.out / "fs.pickle")
        self.assertIn("ctime of /etc/shadow", self.problems())

    def test_kernel_built_after_boot(self):
        late = dataclasses.replace(self.ident, boot_offset=self.ident.now - 1_000_000_000)
        found = " | ".join(validate.problems(self.persona, late, self.out, self.out, self.keys, min_pickle=0))
        self.assertIn("kernel: built", found)

    def test_host_name_of_another_provider(self):
        # the persona of these tests runs on KVM; an AWS VPC name does not fit it
        wrong = dataclasses.replace(self.ident, hostname="ip-172-31-4-20")
        found = " | ".join(validate.problems(self.persona, wrong, self.out, self.out, self.keys, min_pickle=0))
        self.assertIn("fits no host name of the kvm hardware", found)

    def test_log_entry_after_the_versions_went_stale(self):
        stale = stale_at(self.ident.release)
        self.assertTrue(stale, self.ident.release.label)
        superseded = time.strftime("%Y-%m-%d", time.gmtime(stale))
        path = self.out / "honeyfs/var/log/apt/history.log"
        late = time.strftime("%Y-%m-%d  %H:%M:%S",
                             time.gmtime(time.mktime(time.strptime(superseded, "%Y-%m-%d")) + 5 * 86400))
        path.write_text(path.read_text() + f"\nStart-Date: {late}\nCommandline: apt-get install -y git\n"
                        f"End-Date: {late}\n")
        self.assertIn("after they went stale", self.problems())

    def test_dmesg_root_is_not_the_mounted_root(self):
        self.edit("txtcmds/bin/dmesg", "root=", "root=/dev/sdz9 x=")
        self.assertIn("boot: root=/dev/sdz9", self.problems())

    def test_dmesg_root_is_not_the_fstab_root(self):
        self.edit("txtcmds/bin/dmesg", "root=", "root=UUID=00000000-0000-4000-8000-000000000000 x=")
        self.assertIn("is not the / entry", self.problems())

    def test_dmesg_without_root(self):
        self.edit("txtcmds/bin/dmesg", "root=", "rootX=")
        self.assertIn("no root=", self.problems())

    def test_small_pickle(self):
        self.assertIn("pickle", " ".join(
            validate.problems(self.persona, self.ident, self.out, self.out, self.keys, min_pickle=10**9)))


class ValidateDeviceTest(unittest.TestCase):
    def test_busybox_top_memory_mismatch(self):
        persona = catalog.by_id("dlink-dir859")
        ident, out, keys = render_one(self, persona, 2)
        path = out / "txtcmds/usr/bin/top"
        text = path.read_text()
        path.write_text(text.replace("K free", "1K free", 1))
        self.assertIn("top", " ".join(validate.problems(persona, ident, out, out, keys, min_pickle=0)))

    def test_dropbear_version_mismatch(self):
        persona = catalog.by_id("tplink-wr841n")
        ident, out, keys = render_one(self, persona, 2)
        path = out / "cowrie.cfg"
        path.write_text(path.read_text().replace("Dropbear sshd 2015.67", "Dropbear sshd 2019.78"))
        self.assertIn("ssh_version", " ".join(validate.problems(persona, ident, out, out, keys, min_pickle=0)))

    def test_ubuntu_marker_outside_ubuntu(self):
        persona = catalog.by_id("debian-bookworm")
        ident, out, keys = render_one(self, persona, 2)
        (out / "honeyfs/etc/issue").write_text("Ubuntu 22.04.4 LTS \\n \\l\n")
        self.assertIn("forbidden", " ".join(validate.problems(persona, ident, out, out, keys, min_pickle=0)))


class ValidateRpmTest(unittest.TestCase):
    def test_dnf_openssh_upgrade_after_boot(self):
        persona = catalog.by_id("rhel-9")
        ident, out, keys = render_one(self, persona, 3)
        path = out / "honeyfs/var/log/dnf.log"
        late = time.strftime("%Y-%m-%dT%H:%M:%S+0000", time.gmtime(ident.now - 60))
        lines = [re.sub(r"^\S+", late, line) if "Upgraded: openssh" in line else line
                 for line in path.read_text().splitlines()]
        path.write_text("\n".join(lines) + "\n")
        self.assertIn("sshd", " ".join(validate.problems(persona, ident, out, out, keys, min_pickle=0)))

    def test_yum_log_entry_before_installation(self):
        persona = catalog.by_id("centos-7")
        ident, out, keys = render_one(self, persona, 3)
        path = out / "honeyfs/var/log/yum.log"
        path.write_text(path.read_text() + "Jan 01 00:00:01 Installed: wget-1.14-18.el7_6.1.x86_64\n")
        year = time.gmtime(ident.now).tm_year
        if time.gmtime(ident.installed_at).tm_year < year:  # Jan 1 of this year is after the installation
            path.write_text(path.read_text().replace("Jan 01 00:00:01", "Dec 31 23:59:59"))
        self.assertIn("date", " ".join(validate.problems(persona, ident, out, out, keys, min_pickle=0)))

    def test_clean_renders_of_rpm_systems(self):
        for pid in ("rhel-9", "centos-7"):
            persona = catalog.by_id(pid)
            for seed in range(5):
                ident, out, keys = render_one(self, persona, seed)
                self.assertEqual(validate.problems(persona, ident, out, out, keys, min_pickle=0), [], (pid, seed))


class ValidateBootTest(unittest.TestCase):
    def test_pi_kernel_gets_cmdline_txt(self):
        persona = catalog.by_id("raspios")
        ident, out, keys = render_one(self, persona, 1)
        self.assertEqual(validate.problems(persona, ident, out, out, keys, min_pickle=0), [])
        path = out / "txtcmds/bin/dmesg"
        path.write_text(path.read_text().replace("console=serial0,115200 ", "", 1))
        self.assertIn("cmdline.txt", " ".join(validate.problems(persona, ident, out, out, keys, min_pickle=0)))

    def test_dev_root_needs_a_partuuid(self):
        persona = catalog.by_id("ubuntu-noble")
        for seed in range(400):
            ident = draw(persona, seed, NOW)
            if ident.hardware.provider == "aws":
                break
        _, out, keys = render_ident(self, persona, ident)
        self.assertEqual(validate.problems(persona, ident, out, out, keys, min_pickle=0), [])
        path = out / "txtcmds/bin/dmesg"
        path.write_text(re.sub(r"root=PARTUUID=\S+", "root=LABEL=cloudimg-rootfs", path.read_text()))
        self.assertIn("/dev/root (no initramfs)", " ".join(validate.problems(persona, ident, out, out, keys, min_pickle=0)))
