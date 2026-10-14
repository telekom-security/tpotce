import calendar
import configparser
import dataclasses
import json
import re
import time
import unittest

from tests.helpers import base_tree
from tests.test_render import NOW, render_ident, render_one
from tpot_personas import PersonaError, catalog, tree, validate
from tpot_personas.common import ps_start
from tpot_personas.identity import DAY, draw, restore, stale_at, to_state
from tpot_personas.render import render


class KeptIdentityTest(unittest.TestCase):
    def test_yum_log_of_an_identity_kept_over_new_year(self):
        # yum.log has no year in its lines: they are of the year the identity was drawn, not of now
        persona = catalog.by_id("centos-7")
        drawn = draw(persona, 0, calendar.timegm((2024, 12, 20, 12, 0, 0)))
        kept = restore(persona, to_state(persona, drawn), calendar.timegm((2025, 1, 5, 12, 0, 0)))
        _, out, keys = render_ident(self, persona, kept)
        self.assertTrue((out / "honeyfs/var/log/yum.log").read_text())
        self.assertEqual(validate.problems(persona, kept, out, out, keys, min_pickle=0), [])


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

    def test_static_top_shadows_the_live_one(self):
        (self.out / "txtcmds/usr/bin/top").write_text("top - 10:00:00 up 1 day\n")
        self.assertIn("static txtcmd usr/bin/top", self.problems())

    def test_top_keys_of_the_persona(self):
        self.edit("cowrie.cfg", "\ntop_style = procps\n", "\ntop_style = busybox\n")
        self.assertIn("top_style 'busybox' does not fit server ubuntu-jammy", self.problems())
        self.edit("cowrie.cfg", "\ntop_style = busybox\n", "\ntop_style = procps\n")
        self.edit("cowrie.cfg", "\nprocps_version = 3.3.17\n", "\nprocps_version = 3.3\n")
        self.assertIn("procps_version '3.3' is no procps-ng version", self.problems())
        self.edit("cowrie.cfg", "\nkernel_threads = ", "\nkernel_threads = 99")
        self.assertIn("kernel threads", self.problems())
        self.edit("cowrie.cfg", "\nload_base = ", "\nload_base = x")
        self.assertIn("load_base is no number", self.problems())

    def test_hostname_mismatch(self):
        (self.out / "honeyfs/etc/hostname").write_text("other\n")
        self.assertIn("hostname", self.problems())

    def test_cfg_mismatch(self):
        self.edit("cowrie.cfg", f"boot_offset = {self.ident.boot_offset}", "boot_offset = 1")
        self.assertIn("cfg boot_offset", self.problems())

    def test_refused_pairs_differ_from_the_list(self):
        self.edit("cowrie.cfg", '[["admin", "definitely_not_valid_creds"], ', "[")
        self.assertIn("cfg auth_refused_pairs", self.problems())
        self.edit("cowrie.cfg", "auth_refused_pairs = ", "auth_refused_pairs_x = ")
        self.assertIn("cfg auth_refused_pairs", self.problems())

    def test_prompt_style_mismatch(self):
        self.edit("cowrie.cfg", "prompt_style = debian", "prompt_style = redhat")
        self.assertIn("cfg prompt_style", self.problems())

    def test_prompt_style_missing(self):
        self.edit("cowrie.cfg", "prompt_style = debian\n", "")
        self.assertIn("cfg prompt_style", self.problems())

    def test_unknown_prompt_style(self):
        persona = dataclasses.replace(self.persona, prompt_style="zsh")
        self.edit("cowrie.cfg", "prompt_style = debian", "prompt_style = zsh")
        found = " | ".join(validate.problems(persona, self.ident, self.out, self.out, self.keys, min_pickle=0))
        self.assertIn("prompt_style 'zsh' is not one of", found)

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

    def test_ctime_bytes_of_a_marker_are_no_finding(self):
        # 0x6c696870 pickles as b"phil" (BININT, little endian): a ctime in August 2027
        phil = int.from_bytes(b"phil", "little")
        ident = draw(self.persona, 5, phil + 30 * 86400)
        _, out, keys = render_ident(self, self.persona, ident)
        fs = tree.load_pickle(out / "fs.pickle")
        tree.node_set(tree.find_node(fs, "etc/passwd"), tree.A_CTIME, phil)
        tree.dump_pickle(fs, out / "fs.pickle")
        self.assertIn(b"phil", (out / "fs.pickle").read_bytes())
        self.assertEqual(validate.problems(self.persona, ident, out, out, keys, min_pickle=0), [])

    def test_forbidden_marker_in_a_pickled_name_or_file(self):
        fs = tree.load_pickle(self.out / "fs.pickle")
        tree.ensure_dir(fs, "home/phil", ctime=self.ident.installed_at)
        tree.dump_pickle(fs, self.out / "fs.pickle")
        self.assertIn("forbidden phil in fs.pickle (/home/phil)", self.problems())
        fs = tree.load_pickle(self.out / "fs.pickle")
        tree.remove_node(fs, "home/phil")
        tree.node_set(tree.find_node(fs, "etc/hostname"), tree.A_CONTENTS, b"svr04\n")
        tree.dump_pickle(fs, self.out / "fs.pickle")
        self.assertIn("forbidden svr04 in fs.pickle (/etc/hostname)", self.problems())

    def test_program_of_size_0(self):
        fs = tree.load_pickle(self.out / "fs.pickle")
        tree.node_set(tree.find_node(fs, "usr/bin/top"), tree.A_SIZE, 0)
        tree.dump_pickle(fs, self.out / "fs.pickle")
        self.assertIn("program: size 0 for /usr/bin/top", self.problems())

    def test_txtcmd_with_contents_shows_no_elf_header(self):
        fs = tree.load_pickle(self.out / "fs.pickle")
        tree.ensure_file(fs, "usr/bin/nproc", b"4\n", mode=0o755)
        tree.dump_pickle(fs, self.out / "fs.pickle")
        self.assertIn("program: txtcmd usr/bin/nproc has contents", self.problems())

    def test_txtcmd_that_is_no_program(self):
        fs = tree.load_pickle(self.out / "fs.pickle")
        tree.remove_node(fs, "usr/bin/lscpu")
        tree.dump_pickle(fs, self.out / "fs.pickle")
        self.assertIn("program: txtcmd usr/bin/lscpu is no executable", self.problems())

    def test_busybox_on_a_server(self):
        self.edit("cowrie.cfg", "\n[ssh]\n", "\nbusybox_help = /x\n\n[ssh]\n")
        self.assertIn("busybox: Cowrie's busybox answers", self.problems())

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

    def test_uptime_of_a_fresh_draw(self):
        long = dataclasses.replace(self.ident, boot_offset=121 * DAY)
        found = " | ".join(validate.problems(self.persona, long, self.out, self.out, self.keys, min_pickle=0))
        self.assertIn("uptime: 121 days, a fresh draw has 1 to 120", found)

    def test_uptime_of_a_kept_identity_may_grow(self):
        kept = restore(self.persona, to_state(self.persona, self.ident), self.ident.now + 200 * DAY)
        found = validate.problems(self.persona, kept, self.out, self.out, self.keys, min_pickle=0)
        self.assertFalse([p for p in found if p.startswith("uptime")], found)

    def test_booted_after_now_or_installed_after_the_boot(self):
        for ident, text in ((dataclasses.replace(self.ident, boot_offset=-5), "uptime: booted after now"),
                            (dataclasses.replace(self.ident, installed_at=self.ident.booted_at + 1),
                             "uptime: installed after the boot")):
            with self.subTest(text):
                kept = dataclasses.replace(ident, chosen_at=ident.now - DAY)
                for case in (ident, kept):
                    found = " | ".join(validate.problems(self.persona, case, self.out, self.out, self.keys,
                                                         min_pickle=0))
                    self.assertIn(text, found)

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
    def test_busybox_top_without_a_procps_version(self):
        persona = catalog.by_id("dlink-dir859")
        ident, out, keys = render_one(self, persona, 2)
        cfg = (out / "cowrie.cfg").read_text()
        self.assertIn("\ntop_style = busybox\n", cfg)
        self.assertNotIn("procps_version", cfg)
        self.assertEqual(validate.problems(persona, ident, out, out, keys, min_pickle=0), [])
        (out / "cowrie.cfg").write_text(cfg.replace("\ntop_style = busybox\n", "\ntop_style = procps\n"))
        self.assertIn("procps_version '' is no procps-ng version",
                      " ".join(validate.problems(persona, ident, out, out, keys, min_pickle=0)))

    def test_dropbear_version_mismatch(self):
        persona = catalog.by_id("tplink-wr841n")
        ident, out, keys = render_one(self, persona, 2)
        path = out / "cowrie.cfg"
        # the v14 firmware has dropbear 2012.55 (the brief's 2015.67 is in no image)
        path.write_text(path.read_text().replace("Dropbear sshd 2012.55", "Dropbear sshd 2019.78"))
        self.assertIn("ssh_version", " ".join(validate.problems(persona, ident, out, out, keys, min_pickle=0)))

    def test_ubuntu_marker_outside_ubuntu(self):
        persona = catalog.by_id("debian-bookworm")
        ident, out, keys = render_one(self, persona, 2)
        (out / "honeyfs/etc/issue").write_text("Ubuntu 22.04.4 LTS \\n \\l\n")
        self.assertIn("forbidden", " ".join(validate.problems(persona, ident, out, out, keys, min_pickle=0)))


class ValidateSystemTest(unittest.TestCase):
    """Foreign paths of Cowrie's Debian 12 x86_64 tree, /proc/modules, interpreters (F4, F6)."""

    def problems(self, pid, change, seed=1):
        persona = catalog.by_id(pid)
        ident, out, keys = render_one(self, persona, seed)
        self.assertEqual(validate.problems(persona, ident, out, out, keys, min_pickle=0), [], pid)
        fs = tree.load_pickle(out / "fs.pickle")
        change(fs, out)
        tree.dump_pickle(fs, out / "fs.pickle")
        return " | ".join(validate.problems(persona, ident, out, out, keys, min_pickle=0))

    def test_debian_files_on_red_hat(self):
        found = self.problems("rhel-9", lambda fs, out: tree.ensure_file(fs, "etc/debian_version", b"12.5\n"))
        self.assertIn("foreign: /etc/debian_version on rhel-9", found)
        found = self.problems("centos-7", lambda fs, out: tree.ensure_file(fs, "usr/bin/dpkg-query", b"", mode=0o755))
        self.assertIn("foreign: /usr/bin/dpkg-query", found)

    def test_x86_64_libraries_on_a_router(self):
        found = self.problems("openwrt", lambda fs, out: tree.ensure_file(fs, "usr/lib/x86_64-linux-gnu/libc.so.6", b""))
        self.assertIn("foreign: /usr/lib/x86_64-linux-gnu on openwrt", found)
        self.assertIn("foreign: x86_64 names on openwrt", found)

    def test_debian_tools_on_red_hat_and_devices(self):
        for pid in ("rhel-9", "tplink-wr841n"):
            found = self.problems(pid, lambda fs, out: (tree.ensure_file(fs, "etc/adduser.conf", b""),
                                                        tree.ensure_dir(fs, "etc/rcS.d"),
                                                        tree.ensure_file(fs, "usr/share/man/man1/which.debianutils.1.gz",
                                                                         b"")))
            self.assertIn(f"foreign: /etc/adduser.conf, /etc/rcS.d on {pid}", found)
            self.assertIn(f"foreign: debian names on {pid}: /usr/share/man/man1/which.debianutils.1.gz", found)

    def test_link_without_target(self):
        found = self.problems("ubuntu-noble", lambda fs, out: tree.ensure_link(fs, "proc/self/cwd", ""))
        self.assertIn("link: 1 links name nothing: /proc/969/cwd -> ", found)
        found = self.problems("ubuntu-noble", lambda fs, out: tree.ensure_link(fs, "etc/os-release2", "usr/lib/os-release"))
        self.assertIn("link: 1 links name nothing: /etc/os-release2 -> usr/lib/os-release", found)

    def test_link_with_a_size_other_than_its_target(self):
        def change(fs, out):
            tree.node_set(tree.find_node(fs, "bin"), tree.A_SIZE, 99)
        found = self.problems("ubuntu-noble", change)
        self.assertIn("link: 1 links show a size other than the length of their target: /bin -> usr/bin (99)", found)

    def test_link_that_names_nothing(self):
        found = self.problems("rhel-9", lambda fs, out: tree.ensure_link(
            fs, "usr/lib64/ld-linux-x86-64.so.2", "usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2"))
        self.assertIn("link: 1 links name nothing: /usr/lib64/ld-linux-x86-64.so.2 -> "
                      "usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2", found)
        found = self.problems("tplink-wr841n", lambda fs, out: tree.ensure_link(fs, "dev/stdout", "proc/969/fd/pipe:[7685]"))
        self.assertIn("link: 1 links name nothing: /dev/stdout -> proc/969/fd/pipe:[7685]", found)

    def test_sshd_config_and_systemd_on_dropbear(self):
        found = self.problems("openwrt", lambda fs, out: (tree.ensure_file(fs, "etc/ssh/sshd_config", b""),
                                                          tree.ensure_dir(fs, "etc/systemd/system")))
        self.assertIn("/etc/ssh", found)
        self.assertIn("/etc/systemd", found)

    def test_container_leftover(self):
        found = self.problems("debian-trixie", lambda fs, out: tree.ensure_file(fs, ".dockerenv", b""))
        self.assertIn("foreign: /.dockerenv", found)

    def test_raspios_without_its_multiarch_directory(self):
        found = self.problems("raspios", lambda fs, out: tree.remove_node(fs, "usr/lib/aarch64-linux-gnu"))
        self.assertIn("foreign: no multiarch directory /usr/lib/aarch64-linux-gnu", found)

    def test_proc_modules_on_a_device(self):
        found = self.problems("qnap-qts", lambda fs, out: tree.ensure_file(fs, "proc/modules", b"nft_chain_nat 12288\n"))
        self.assertIn("modules: /proc/modules is not the persona's", found)
        found = self.problems("tplink-wr841n", lambda fs, out: (tree.remove_node(fs, "proc/modules"),
                                                                (out / "honeyfs/proc/modules").unlink()))
        self.assertIn("modules: /proc/modules is not the persona's", found)

    def test_device_modules_have_32_bit_addresses(self):
        def change(fs, out):
            path = out / "honeyfs/proc/modules"
            text = path.read_text().replace("Live 0xc0", "Live 0x00000000c0")
            path.write_text(text)
            tree.ensure_file(fs, "proc/modules", text.encode())
        self.assertIn("is no /proc/modules line", self.problems("tplink-wr841n", change))

    def test_proc_modules_of_cowries_tree(self):
        found = self.problems("ubuntu-jammy", lambda fs, out: tree.ensure_file(
            fs, "proc/modules", b"nft_chain_nat 12288 0 - Live 0x0000000000000000\n"))
        self.assertIn("modules: /proc/modules is not the persona's", found)

    def test_module_used_by_one_not_loaded(self):
        def change(fs, out):
            path = out / "honeyfs/proc/modules"
            text = path.read_text().replace("1 ip_tables,", "1 nf_tables,")
            path.write_text(text)
            tree.ensure_file(fs, "proc/modules", text.encode())
        self.assertIn("modules: x_tables is used by nf_tables, which is not loaded", self.problems("debian-bookworm", change))

    def test_perl_answered_but_not_on_path(self):
        found = self.problems("ubuntu-noble", lambda fs, out: tree.remove_node(fs, "usr/bin/perl"))
        self.assertIn("which: perl is not on PATH, Cowrie answers it", found)

    def test_python3_on_path_but_skipped(self):
        found = self.problems("centos-7", lambda fs, out: tree.ensure_executable(fs, "usr/bin/python3", 7000))
        self.assertIn("which: python3 is on PATH, nothing answers it", found)

    def test_perl_version_of_another_release(self):
        def change(fs, out):
            path = out / "cowrie.cfg"
            path.write_text(path.read_text().replace("perl_version = 5.36.0", "perl_version = 5.14.2"))
        self.assertIn("cfg perl_version: '5.14.2', expected '5.36.0'", self.problems("debian-bookworm", change))

    def test_interpreter_versions_are_keys_of_every_server(self):
        for pid in ("ubuntu-noble", "rhel-9", "centos-7"):
            for key in ("python_version", "perl_version"):
                def change(fs, out, key=key):
                    path = out / "cowrie.cfg"
                    path.write_text("".join(line for line in path.read_text().splitlines(True)
                                            if not line.startswith(f"{key} =")))
                self.assertIn(f"cfg {key}: None, expected ", self.problems(pid, change), (pid, key))


class ValidateServerCommandTest(unittest.TestCase):
    def problems(self, pid, change):
        persona = catalog.by_id(pid)
        ident, out, keys = render_one(self, persona, 1)
        change(out)
        return " | ".join(validate.problems(persona, ident, out, out, keys, min_pickle=0))

    def edit_cfg(self, old, new):
        def change(out):
            path = out / "cowrie.cfg"
            self.assertIn(old, path.read_text())
            path.write_text(path.read_text().replace(old, new, 1))
        return change

    def test_sudo_skipped_on_a_server(self):
        found = self.problems("debian-trixie", self.edit_cfg("skip_python_commands = ", "skip_python_commands = sudo,"))
        self.assertIn("which: Cowrie's sudo is skipped on a server", found)
        self.assertIn("which: sudo is on PATH, nothing answers it", found)

    def test_apt_sources_of_another_system(self):
        found = self.problems("ubuntu-jammy", self.edit_cfg("http://archive.ubuntu.com/ubuntu jammy,",
                                                            "http://ftp.debian.org/debian stable,"))
        self.assertIn("cfg apt_sources", found)

    def test_service_status_of_another_system(self):
        def change(out):
            (out / "service-status.txt").write_text(" [ + ]  lightdm\n")
        self.assertIn("service: service-status.txt is not the persona's", self.problems("ubuntu-noble", change))

    def test_runtime_files_of_another_system(self):
        for name in ("systemd-units.json", "sudo-list.json", "apt-packages.json"):
            def change(out, name=name):
                path = out / name
                path.write_text(path.read_text().replace("(ALL : ALL) ALL", "(ALL) ALL").replace(
                    "OpenBSD Secure Shell server", "OpenSSH server daemon").replace('"curl"', '"wget"'))
            self.assertIn(f"runtime: {name} is not the persona's", self.problems("ubuntu-jammy", change), name)

    def test_program_of_a_package_apt_does_not_know(self):
        persona = catalog.by_id("debian-bookworm")
        ident, out, keys = render_one(self, persona, 1)
        path = out / "apt-packages.json"
        data = json.loads(path.read_text())
        del data["installed"]["wget"]
        path.write_text(json.dumps(data))
        found = " | ".join(validate.problems(persona, ident, out, out, keys, min_pickle=0))
        self.assertIn("apt: wget is on PATH, apt does not have it installed", found)

    def test_init_script_of_status_all_missing(self):
        persona = catalog.by_id("debian-bookworm")
        ident, out, keys = render_one(self, persona, 1)
        fs = tree.load_pickle(out / "fs.pickle")
        tree.remove_node(fs, "etc/init.d/cron")
        tree.dump_pickle(fs, out / "fs.pickle")
        found = " | ".join(validate.problems(persona, ident, out, out, keys, min_pickle=0))
        self.assertIn("service: --status-all names cron, /etc/init.d/cron is missing", found)

    def test_unit_file_without_text(self):
        persona = catalog.by_id("debian-bookworm")
        ident, out, keys = render_one(self, persona, 1)
        fs = tree.load_pickle(out / "fs.pickle")
        node = tree.find_node(fs, "usr/lib/systemd/system/cron.service")
        tree.node_set(node, tree.A_CONTENTS, [])
        tree.dump_pickle(fs, out / "fs.pickle")
        found = " | ".join(validate.problems(persona, ident, out, out, keys, min_pickle=0))
        self.assertIn("systemctl: /lib/systemd/system/cron.service of cron.service has no text", found)

    def test_run_level_links_follow_the_init_scripts(self):
        persona = catalog.by_id("debian-bookworm")
        ident, out, keys = render_one(self, persona, 1)
        fs = tree.load_pickle(out / "fs.pickle")
        tree.remove_node(fs, "etc/rc3.d/S01cron")
        tree.remove_node(fs, "etc/rc2.d/S01ssh")
        tree.ensure_link(fs, "etc/rc0.d/K01kmod", "../init.d/kmod")
        tree.ensure_link(fs, "etc/rcS.d/S01dbus", "../init.d/cron")
        tree.dump_pickle(fs, out / "fs.pickle")
        found = " | ".join(validate.problems(persona, ident, out, out, keys, min_pickle=0))
        self.assertIn("init: /etc/init.d/cron starts in run level 3, /etc/rc3.d has no S link to it", found)
        self.assertIn("init: /etc/init.d/ssh starts in run level 2, /etc/rc2.d has no S link to it", found)
        self.assertIn("init: /etc/rc0.d/K01kmod, /etc/init.d/kmod does not stop in run level 0", found)
        self.assertIn("init: /etc/rcS.d/S01dbus names /etc/init.d/cron, not /etc/init.d/dbus", found)
        persona = catalog.by_id("centos-7")
        ident, out, keys = render_one(self, persona, 1)
        fs = tree.load_pickle(out / "fs.pickle")
        tree.remove_node(fs, "etc/rc.d/rc0.d/K50netconsole")
        tree.dump_pickle(fs, out / "fs.pickle")
        found = " | ".join(validate.problems(persona, ident, out, out, keys, min_pickle=0))
        self.assertIn("init: /etc/init.d/netconsole stops in run level 0, /etc/rc0.d has no K link to it", found)

    def test_exec_start_names_a_missing_program(self):
        persona = catalog.by_id("raspios")
        ident, out, keys = render_one(self, persona, 1)
        fs = tree.load_pickle(out / "fs.pickle")
        tree.remove_node(fs, "usr/sbin/avahi-daemon")
        tree.dump_pickle(fs, out / "fs.pickle")
        found = " | ".join(validate.problems(persona, ident, out, out, keys, min_pickle=0))
        self.assertIn("systemctl: ExecStart of avahi-daemon.service names /usr/sbin/avahi-daemon, "
                      "which is not in fs.pickle", found)

    def test_tool_answers_without_its_package(self):
        def change(out):
            path = out / "apt-packages.json"
            data = json.loads(path.read_text())
            data["available"]["net-tools"] = data["installed"].pop("net-tools")
            path.write_text(json.dumps(data))
        found = self.problems("ubuntu-noble", change)
        self.assertIn("tools: ifconfig of net-tools answers, apt does not have net-tools installed", found)

    def test_unit_file_missing(self):
        persona = catalog.by_id("rhel-9")
        ident, out, keys = render_one(self, persona, 1)
        fs = tree.load_pickle(out / "fs.pickle")
        tree.remove_node(fs, "usr/lib/systemd/system/crond.service")
        tree.dump_pickle(fs, out / "fs.pickle")
        found = " | ".join(validate.problems(persona, ident, out, out, keys, min_pickle=0))
        self.assertIn("systemctl: /usr/lib/systemd/system/crond.service of crond.service is not in fs.pickle", found)

    def test_sudoers_of_another_system(self):
        def change(out):
            (out / "honeyfs/etc/sudoers").chmod(0o640)
            (out / "honeyfs/etc/sudoers").write_text("root ALL=(ALL) ALL\n")
        self.assertIn("sudoers: /etc/sudoers is not the persona's", self.problems("rhel-9", change))

    def test_txtcmd_beside_cowries_command(self):
        # the old static sudo: /usr/bin/sudo would print it, sudo would run Cowrie's
        def change(out):
            (out / "txtcmds/usr/bin/sudo").write_text("sudo: a password is required\n")
        found = self.problems("centos-7", change)
        self.assertIn("which: a txtcmd of sudo answers /usr/bin/sudo, Cowrie's sudo answers sudo", found)


class ValidateBusyboxTest(unittest.TestCase):
    def setUp(self):
        self.persona = catalog.by_id("tplink-wr841n")
        self.ident, self.out, self.keys = render_one(self, self.persona, 1)

    def problems(self):
        return " | ".join(validate.problems(self.persona, self.ident, self.out, self.out, self.keys, min_pickle=0))

    def test_clean(self):
        self.assertEqual(self.problems(), "")

    def test_skipped_busybox(self):
        path = self.out / "cowrie.cfg"
        path.write_text(path.read_text().replace("skip_python_commands = ", "skip_python_commands = busybox,"))
        self.assertIn("busybox: Cowrie's busybox is skipped", self.problems())

    def test_help_of_another_version(self):
        path = self.out / "busybox-help.txt"
        path.write_text(path.read_text().replace("BusyBox v1.19.2", "BusyBox v1.20.2"))
        self.assertIn("busybox: the help does not start with", self.problems())

    def test_no_busybox_program(self):
        fs = tree.load_pickle(self.out / "fs.pickle")
        tree.remove_node(fs, "bin/busybox")
        tree.dump_pickle(fs, self.out / "fs.pickle")
        self.assertIn("busybox: /bin/busybox is no program", self.problems())

    def test_forbidden_marker_in_the_help(self):
        path = self.out / "busybox-help.txt"
        path.write_text(path.read_text() + "svr04\n")
        self.assertIn("forbidden svr04 in busybox-help.txt", self.problems())


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

    def rhel(self):
        persona = catalog.by_id("rhel-9")
        ident, out, keys = render_one(self, persona, 2)
        return persona, ident, out, keys, lambda: " | ".join(validate.problems(persona, ident, out, out, keys,
                                                                               min_pickle=0))

    def edit_dnf(self, out, change):
        path = out / "dnf-packages.json"
        data = json.loads(path.read_text())
        change(data)
        path.write_text(json.dumps(data))

    def test_dnf_kernel_is_not_unames(self):
        persona, ident, out, keys, problems = self.rhel()
        self.edit_dnf(out, lambda data: data["installed"]["kernel"].update(evr="5.14.0-1.el9"))
        self.assertIn("dnf: kernel is 5.14.0-1.el9, the system shows", problems())

    def test_dnf_program_without_its_package(self):
        persona, ident, out, keys, problems = self.rhel()
        self.edit_dnf(out, lambda data: data["installed"].pop("perl-interpreter"))
        self.assertIn("dnf: perl is on PATH, rpm does not have perl-interpreter installed", problems())

    def test_dnf_tool_without_its_package(self):
        persona, ident, out, keys, problems = self.rhel()
        self.edit_dnf(out, lambda data: data["installed"].pop("ethtool"))
        self.assertIn("dnf: ethtool is installed, rpm has no ethtool", problems())

    def test_dnf_package_without_its_program(self):
        persona, ident, out, keys, problems = self.rhel()
        self.edit_dnf(out, lambda data: data["installed"].update(
            nmap={"evr": "3:7.92-3.el9", "arch": "x86_64", "from": "@System", "summary": "", "size": 1}))
        self.assertIn("dnf: nmap is installed, its program nmap is not on PATH", problems())

    def test_rhel_installs_only_packages_with_their_programs(self):
        persona = catalog.by_id("rhel-9")
        for seed in range(8):
            ident, out, _ = render_one(self, persona, seed)
            data = json.loads((out / "dnf-packages.json").read_text())
            self.assertNotIn("tmux", data["installed"], seed)
            self.assertIn("tmux", data["available"], seed)
            self.assertNotIn("tmux", (out / "honeyfs/var/log/dnf.log").read_text(), seed)

    def test_apt_package_without_its_program(self):
        persona = catalog.by_id("ubuntu-jammy")
        ident, out, keys = render_one(self, persona, 2)
        path = out / "apt-packages.json"
        data = json.loads(path.read_text())
        data["installed"]["nmap"] = "7.91+dfsg1+really7.80+dfsg1-2ubuntu0.1"
        path.write_text(json.dumps(data))
        self.assertIn("apt: nmap is installed, its program nmap is not on PATH",
                      " | ".join(validate.problems(persona, ident, out, out, keys, min_pickle=0)))

    def test_program_without_an_owner(self):
        persona, ident, out, keys, problems = self.rhel()
        self.edit_dnf(out, lambda data: data["owners"].pop("/usr/bin/bash"))
        self.assertIn("dnf: /usr/bin/bash is on PATH, rpm names no owner of it", problems())

    def test_debian_programs_are_gone_on_red_hat(self):
        for pid in ("rhel-9", "centos-7"):
            persona = catalog.by_id(pid)
            ident, out, keys = render_one(self, persona, 1)
            fs = tree.load_pickle(out / "fs.pickle")
            for rel in ("usr/bin/mawk", "usr/bin/dash", "usr/bin/run-parts"):
                self.assertIsNone(tree.find_node(fs, rel), (pid, rel))
            for rel, target in (("usr/bin/awk", "gawk"), ("usr/bin/sh", "bash")):
                self.assertEqual(tree.node_get(tree.find_node(fs, rel), tree.A_TARGET), target, (pid, rel))

    def test_redhat_repo_enables_other_repositories(self):
        persona, ident, out, keys, problems = self.rhel()
        path = out / "honeyfs/etc/yum.repos.d/redhat.repo"
        path.write_text(path.read_text().replace("enabled = 1", "enabled = 0", 1))
        self.assertIn("dnf: redhat.repo enables", problems())

    def test_dnf_log_installs_what_rpm_does_not_have(self):
        persona, ident, out, keys, problems = self.rhel()
        path = out / "honeyfs/var/log/dnf.log"
        path.write_text(path.read_text() + "2026-01-01T00:00:00+0000 SUBDEBUG Installed: nmap-7.92-3.el9.x86_64\n")
        self.assertIn("dnf: dnf.log names Installed nmap-7.92-3.el9.x86_64, rpm does not have it installed",
                      problems())

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


def _problems(persona, ident, out, keys):
    return " | ".join(validate.problems(persona, ident, out, out, keys, min_pickle=0))


def _edit(test, path, old, new):
    text = path.read_text()
    test.assertIn(old, text)
    path.write_text(text.replace(old, new, 1))


class PsStartTest(unittest.TestCase):
    """procps prints HH:MM for a process of today, MonDD in this year, else the year."""

    def test_formats(self):
        now = 1_790_000_000  # 2026-09-21 14:13 UTC
        self.assertEqual(ps_start(now - 3600, now), "13:13")
        self.assertEqual(ps_start(now - 3 * 86400, now), "Sep18")
        self.assertEqual(ps_start(now - 300 * 86400, now), "2025")
        # yesterday 23:59 is another day, Dec 31 another year
        midnight = now // 86400 * 86400
        self.assertEqual(ps_start(midnight - 60, midnight + 60), "Sep20")
        new_year = 1_767_225_600  # 2026-01-01 00:00 UTC
        self.assertEqual(ps_start(new_year - 60, new_year + 60), "2025")

    def test_boot_process_in_the_wrong_format(self):
        persona = catalog.by_id("openwrt")
        for seed in range(200):
            ident = draw(persona, seed, NOW)
            if time.gmtime(ident.booted_at).tm_year < time.gmtime(NOW).tm_year:
                break
        else:
            self.fail("no device booted last year")
        _, out, keys = render_ident(self, persona, ident)
        self.assertEqual(_problems(persona, ident, out, keys), "")
        boot_year = time.strftime("%Y", time.gmtime(ident.booted_at))
        _edit(self, out / "cmdoutput.json", f'"START": "{boot_year}"',
              f'"START": "{time.strftime("%b%d", time.gmtime(ident.booted_at))}"')
        self.assertIn("START", _problems(persona, ident, out, keys))

    def test_login_shell_in_the_future(self):
        persona = catalog.by_id("ubuntu-jammy")
        ident, out, keys = render_one(self, persona, 5)
        path = out / "cmdoutput.json"
        data = json.loads(path.read_text())
        shell = [p for p in data["command"]["ps"] if p["TTY"].startswith("pts/")][0]
        shell["START"] = time.strftime("%H:%M", time.gmtime(ident.now + 3600))
        path.write_text(json.dumps(data))
        self.assertIn("START", _problems(persona, ident, out, keys))


class ValidateDeviceBootTest(unittest.TestCase):
    def test_clean_device_renders(self):
        for persona in catalog.PERSONAS:
            if persona.kind == "device":
                ident, out, keys = render_one(self, persona, 4)
                self.assertEqual(_problems(persona, ident, out, keys), "", persona.id)

    def test_root_file_system_type_differs_from_the_mount(self):
        persona = catalog.by_id("openwrt")
        ident, out, keys = render_one(self, persona, 1)
        _edit(self, out / "txtcmds/bin/mount", "/dev/root on /rom type squashfs", "/dev/root on /rom type jffs2")
        self.assertIn("boot:", _problems(persona, ident, out, keys))

    def test_mtd_root_on_another_device(self):
        persona = catalog.by_id("tplink-wr841n")
        ident, out, keys = render_one(self, persona, 1)
        _edit(self, out / "txtcmds/bin/dmesg", "root=/dev/mtdblock5", "root=/dev/mtdblock3")
        self.assertIn("boot:", _problems(persona, ident, out, keys))

    def test_initramfs_with_a_root_device(self):
        persona = catalog.by_id("qnap-qts")
        ident, out, keys = render_one(self, persona, 1)
        path = out / "txtcmds/bin/dmesg"
        path.write_text(re.sub(r"(Kernel command line: .*)", r"\1 root=/dev/sda1", path.read_text()))
        self.assertIn("boot:", _problems(persona, ident, out, keys))

    def test_df_names_a_file_system_mount_does_not_have(self):
        persona = catalog.by_id("synology-dsm")
        ident, out, keys = render_one(self, persona, 1)
        _edit(self, out / "df.json", "/volume1", "/volume9")
        self.assertIn("df:", _problems(persona, ident, out, keys))

    def test_df_of_a_server_names_what_proc_mounts_does_not_have(self):
        persona = catalog.by_id("ubuntu-noble")
        ident, out, keys = render_one(self, persona, 1)
        _edit(self, out / "honeyfs/proc/mounts", "/run/lock", "/run/lck")
        self.assertIn("is not in /proc/mounts", _problems(persona, ident, out, keys))

    def test_a_static_df_answers_instead_of_the_table(self):
        persona = catalog.by_id("rhel-9")
        ident, out, keys = render_one(self, persona, 1)
        (out / "txtcmds/bin").mkdir(parents=True, exist_ok=True)
        (out / "txtcmds/bin/df").write_text("Filesystem 1K-blocks Used Available Use% Mounted on\n")
        self.assertIn("df: a txtcmd /bin/df answers df", _problems(persona, ident, out, keys))

    def test_df_style_and_awk_follow_the_persona(self):
        persona = catalog.by_id("openwrt")
        ident, out, keys = render_one(self, persona, 1)
        _edit(self, out / "df.json", '"style": "busybox"', '"style": "gnu"')
        _edit(self, out / "cowrie.cfg", "awk_style = busybox", "awk_style = gawk")
        found = _problems(persona, ident, out, keys)
        self.assertIn("df: df.json is not the persona's", found)
        self.assertIn("awk: cfg awk_style", found)

    def test_df_root_of_a_server_is_its_disk(self):
        persona = catalog.by_id("debian-bookworm")
        ident, out, keys = render_one(self, persona, 1)
        data = json.loads((out / "df.json").read_text())
        root = next(row for row in data["filesystems"] if row["target"] == "/")
        root["size"] += 1024
        (out / "df.json").write_text(json.dumps(data))
        self.assertIn("df: the root of the server is not one disk", _problems(persona, ident, out, keys))

    def test_dmesg_bogomips_differs_from_cpuinfo(self):
        persona = catalog.by_id("dlink-dir859")
        ident, out, keys = render_one(self, persona, 1)
        path = out / "txtcmds/bin/dmesg"
        path.write_text(re.sub(r"\d+\.\d+ BogoMIPS", "1.00 BogoMIPS", path.read_text()))
        self.assertIn("BogoMIPS", _problems(persona, ident, out, keys))


class ValidateCpuTest(unittest.TestCase):
    def test_hypervisor_flag_on_bare_metal(self):
        persona = catalog.by_id("qnap-qts")
        ident, out, keys = render_one(self, persona, 1)
        _edit(self, out / "honeyfs/proc/cpuinfo", "flags\t\t: fpu ", "flags\t\t: hypervisor fpu ")
        self.assertIn("cpuinfo: hypervisor", _problems(persona, ident, out, keys))

    def test_dropbear_without_version_needs_dropbear_in_ssh_version(self):
        persona = catalog.by_id("openwrt")
        ident, out, keys = render_one(self, persona, 1)
        self.assertEqual(_problems(persona, ident, out, keys), "")
        _edit(self, out / "cowrie.cfg", "ssh_version = Dropbear", "ssh_version = OpenSSH")
        self.assertIn("ssh_version", _problems(persona, ident, out, keys))


class ValidateHostKeyAlgorithmsTest(unittest.TestCase):
    def check(self, pid, old, new, wanted):
        persona = catalog.by_id(pid)
        ident, out, keys = render_one(self, persona, 1)
        self.assertEqual(_problems(persona, ident, out, keys), "")
        _edit(self, out / "cowrie.cfg", f"\nhost_key_algorithms = {old}", f"\nhost_key_algorithms = {new}")
        self.assertIn(wanted, _problems(persona, ident, out, keys))

    def test_openssh_9_offers_no_ssh_rsa(self):
        self.check("ubuntu-noble", "rsa-sha2-512,rsa-sha2-256,", "rsa-sha2-512,rsa-sha2-256,ssh-rsa,",
                   "OpenSSH 9.6 offers no ssh-rsa")

    def test_openssh_7_4_offers_rsa_sha2_and_ssh_rsa(self):
        self.check("centos-7", "ssh-rsa,rsa-sha2-512,rsa-sha2-256,", "ssh-rsa,",
                   "OpenSSH 7.4 offers rsa-sha2-512 and rsa-sha2-256")
        self.check("centos-7", "ssh-rsa,rsa-sha2-512,rsa-sha2-256,", "rsa-sha2-512,rsa-sha2-256,",
                   "OpenSSH 7.4 still offers ssh-rsa")
        self.check("centos-7", "ssh-rsa,rsa-sha2-512,rsa-sha2-256,", "rsa-sha2-512,rsa-sha2-256,ssh-rsa,",
                   "differ from openssh of SSH-2.0-OpenSSH_7.4")

    def test_rhel_9_policy_has_no_ssh_rsa(self):
        persona = catalog.by_id("rhel-9")
        ident, out, keys = render_one(self, persona, 1)
        cfg = (out / "cowrie.cfg").read_text()
        self.assertIn("\nhost_key_algorithms = rsa-sha2-512,rsa-sha2-256,ecdsa-sha2-nistp256,ssh-ed25519\n", cfg)
        self.check("rhel-9", "rsa-sha2-512,rsa-sha2-256,", "rsa-sha2-512,rsa-sha2-256,ssh-rsa,", "differ from")

    def test_old_dropbear_has_no_rsa_sha2(self):
        self.check("tplink-wr841n", "ssh-rsa", "rsa-sha2-256,ssh-rsa", "dropbear 2012.55 has no rsa-sha2")

    def test_dropbear_has_no_rsa_sha2_512(self):
        self.check("openwrt", "ssh-ed25519,rsa-sha2-256,ssh-rsa", "ssh-ed25519,rsa-sha2-512,rsa-sha2-256,ssh-rsa",
                   "dropbear has no rsa-sha2-512")

    def test_every_key_is_offered_and_every_algorithm_has_its_key(self):
        self.check("openwrt", "ssh-ed25519,rsa-sha2-256,ssh-rsa", "rsa-sha2-256,ssh-rsa",
                   "host key ssh-ed25519 is not offered")
        self.check("tplink-wr841n", "ssh-rsa", "ssh-rsa,ssh-ed25519", "ssh-ed25519 has no ssh-ed25519 key")
        self.check("ubuntu-noble", "rsa-sha2-512,", "ssh-ed448,rsa-sha2-512,", "host key algorithm not supported: ssh-ed448")
