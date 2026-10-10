import configparser
import json
import unittest

from tests.helpers import base_tree
from tests.test_render import render_one
from tpot_personas import PersonaError, catalog, tree, validate
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
        persona = catalog.by_id("debian-bookworm-vuln")
        ident, out, keys = render_one(self, persona, 2)
        (out / "honeyfs/etc/issue").write_text("Ubuntu 22.04.4 LTS \\n \\l\n")
        self.assertIn("forbidden", " ".join(validate.problems(persona, ident, out, out, keys, min_pickle=0)))
