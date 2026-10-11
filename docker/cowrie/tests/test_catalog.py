import calendar
import re
import time
import unittest
from pathlib import Path

from tests.helpers import DIST  # noqa: F401
from tpot_personas import PersonaError, catalog
from tpot_personas.identity import first_install, fits, kernel_time
from tpot_personas.ssh_profiles import PROFILES

ROOT = Path(__file__).resolve().parents[3]

SERVERS = ["ubuntu-noble", "ubuntu-jammy", "debian-trixie", "debian-bookworm",
           "rhel-9", "centos-7", "raspios", "gpu-ubuntu"]
DEVICES = ["dlink-dir859", "tplink-wr841n", "zyxel-nas326", "openwrt", "qnap-qts",
           "synology-dsm", "ubiquiti-edgerouter-x", "ipcam"]
DEVICE_PACKAGES = {"dlink-dir859": set(), "tplink-wr841n": set(), "zyxel-nas326": set(),
                   "openwrt": {"opkg"}, "qnap-qts": {"qpkg_cli"}, "synology-dsm": {"synopkg"},
                   "ubiquiti-edgerouter-x": {"apt-get", "dpkg"}, "ipcam": set()}
DEVICE_USERS = {"dlink-dir859": ("admin", "/bin/sh"), "tplink-wr841n": ("admin", "/bin/sh"),
                "zyxel-nas326": ("admin", "/bin/sh"), "openwrt": ("root", "/bin/ash"),
                "qnap-qts": ("admin", "/bin/sh"), "synology-dsm": ("admin", "/bin/sh"),
                "ubiquiti-edgerouter-x": ("ubnt", "/bin/vbash"), "ipcam": ("admin", "/bin/sh")}
PACKAGES = {"ubuntu-noble": {"apt", "apt-get", "dpkg"}, "ubuntu-jammy": {"apt", "apt-get", "dpkg"},
            "debian-trixie": {"apt", "apt-get", "dpkg"}, "debian-bookworm": {"apt", "apt-get", "dpkg"},
            "rhel-9": {"dnf", "rpm", "yum"}, "centos-7": {"rpm", "yum"},
            "raspios": {"apt", "apt-get", "dpkg"}, "gpu-ubuntu": {"apt", "apt-get", "dpkg"}}
USERS = {"ubuntu-noble": "ubuntu", "ubuntu-jammy": "ubuntu", "debian-trixie": "debian",
         "debian-bookworm": "debian", "rhel-9": "cloud-user", "centos-7": "centos", "raspios": "pi",
         "gpu-ubuntu": "ubuntu"}


class CatalogTest(unittest.TestCase):
    def test_ids_unique_and_safe(self):
        ids = catalog.ids()
        self.assertEqual(len(ids), len(set(ids)))
        for pid in ids:
            self.assertRegex(pid, r"^[a-z0-9][a-z0-9-]*$")

    def test_servers_then_the_devices(self):
        self.assertEqual(catalog.ids(), SERVERS + DEVICES)

    def test_by_id_rejects_unknown(self):
        with self.assertRaises(PersonaError):
            catalog.by_id("../etc")
        self.assertEqual(catalog.by_id("qnap-qts").id, "qnap-qts")

    def test_fields(self):
        for p in catalog.PERSONAS:
            self.assertIn(p.kind, ("server", "device"), p.id)
            self.assertIn(p.ssh_profile, PROFILES, p.id)
            self.assertTrue(p.releases and p.hardware and p.hostnames, p.id)
            for r in p.releases:
                self.assertTrue(r.ssh_banner.startswith("SSH-2.0-"), (p.id, r.label))
                product = "dropbear" if p.ssh_profile.startswith("dropbear") else "OpenSSH"
                self.assertIn(product, r.ssh_banner, (p.id, r.label))
            for hw in p.hardware:
                self.assertGreater(hw.cpus, 0, p.id)
                self.assertGreater(hw.mem_kb, 0, p.id)
                self.assertGreater(hw.disk_kb, 0, p.id)
            for name, kind in p.tokens:
                self.assertIn(kind, ("hf", "openai", "aws"), (p.id, name))

    def test_no_tpot_version_in_persona_data(self):
        version = (ROOT / "version").read_text().strip()
        for path in (DIST / "tpot_personas").glob("*.py"):
            self.assertNotIn(version, path.read_text(), path.name)

    def test_cpu_models_keep_grep_c_processor_honest(self):
        # `grep -c processor /proc/cpuinfo` counts every line with the lowercase word
        for p in catalog.PERSONAS:
            for hw in p.hardware:
                self.assertNotIn("processor", hw.cpu_model, (p.id, hw.cpu_model))


class ServerCatalogTest(unittest.TestCase):
    def test_servers_first_in_order(self):
        self.assertEqual(catalog.ids()[:8], SERVERS)
        self.assertNotIn("fedora-36-vuln", catalog.ids())

    def test_pools(self):
        for pid in SERVERS:
            p = catalog.by_id(pid)
            self.assertGreaterEqual(len(p.releases), 3, pid)
            self.assertLessEqual(len(p.releases), 5, pid)
            self.assertGreaterEqual(len(p.hardware), 2, pid)
            self.assertLessEqual(len(p.hardware), 4, pid)
            self.assertEqual(set(p.package_managers), PACKAGES[pid], pid)
            self.assertEqual(p.kind, "server")
            self.assertEqual(p.shell, "/bin/bash")
            self.assertEqual(p.user, USERS[pid], pid)
            self.assertTrue(p.vulnerability, pid)

    def test_bookworm_has_a_pre_regresshion_release(self):
        banners = [r.ssh_banner for r in catalog.by_id("debian-bookworm").releases]
        self.assertTrue(any(b.endswith(("deb12u1", "deb12u2")) for b in banners), banners)
        self.assertTrue(any(not b.endswith(("deb12u1", "deb12u2")) for b in banners), banners)

    def test_gpu_has_tokens_and_gpus(self):
        p = catalog.by_id("gpu-ubuntu")
        self.assertEqual({name for name, _ in p.tokens}, {"hf", "openai"})
        self.assertTrue(all(h.gpus for h in p.hardware))
        self.assertEqual(sorted(len(h.gpus) for h in p.hardware), [1, 2, 8])
        for r in p.releases:
            self.assertRegex(r.extra["nvidia_driver"], r"^(550|570)\.\d+(\.\d+)?$", r.label)
            self.assertIn(r.extra["cuda"], ("12.4", "12.8"), r.label)
        for h in p.hardware:
            self.assertTrue(32 <= h.cpus <= 128 and "EPYC" in h.cpu_model, h)
            # 256 GiB to 1 TiB nominal, MemTotal a few percent less
            self.assertTrue(240 <= h.mem_kb / 1024 / 1024 <= 1024, h)

    def test_raspios_is_arm(self):
        p = catalog.by_id("raspios")
        self.assertEqual((p.arch, p.hardware_platform, p.user), ("linux-aarch64-lsb", "aarch64", "pi"))
        boards = {h.board for h in p.hardware}
        self.assertEqual(boards, {"Raspberry Pi 4 Model B Rev 1.5", "Raspberry Pi 5 Model B Rev 1.0"})

    def test_every_hardware_has_a_release(self):
        # Raspberry Pi kernels come per board (rpi-v8 on a Pi 4, rpi-2712 on a Pi 5)
        for p in catalog.PERSONAS:
            for hw in p.hardware:
                fitting = [r for r in p.releases
                           if not r.extra.get("flavors") or hw.board in r.extra["flavors"]]
                self.assertTrue(fitting, (p.id, hw))

    def test_no_ubuntu_point_release_of_the_tpot_version(self):
        for p in catalog.PERSONAS:
            for r in p.releases:
                self.assertNotIn(".".join(("24", "04", "2")), r.os_version, (p.id, r.label))

    def test_kernel_dates_parse_and_name_the_right_weekday(self):
        for pid in SERVERS:
            for r in catalog.by_id(pid).releases:
                stamp = kernel_time(r)
                self.assertIsNotNone(stamp, (pid, r.kernel_build))
                match = re.search(r"\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun) [A-Z][a-z]{2} [ \d]\d ", r.kernel_build)
                if match:
                    self.assertEqual(time.strftime("%a", time.gmtime(stamp)), match.group(1), (pid, r.label))
                    # date(1) pads the day with a space, as the kernel's UTS_VERSION does
                    self.assertNotRegex(r.kernel_build, r" [A-Z][a-z]{2} 0\d ", (pid, r.label))

    def test_release_dates_are_ordered(self):
        for pid in SERVERS:
            for r in catalog.by_id(pid).releases:
                first = first_install(r)
                self.assertGreater(first, calendar.timegm((2017, 1, 1, 0, 0, 0)), (pid, r.label))
                self.assertLess(kernel_time(r), calendar.timegm((2026, 10, 10, 0, 0, 0)), (pid, r.label))

    def test_ssh_version_names_the_banner(self):
        for pid in SERVERS:
            for r in catalog.by_id(pid).releases:
                product = r.ssh_banner[len("SSH-2.0-"):].split(" ", 1)[0]
                self.assertRegex(r.ssh_version, rf"^{re.escape(product)}(p\d+)?[ ,]", (pid, r.label))
                self.assertIn("OpenSSL", r.ssh_version, (pid, r.label))


class ProviderCatalogTest(unittest.TestCase):
    AWS_CPUS = ("Intel(R) Xeon(R) Platinum 8259CL CPU @ 2.50GHz", "AMD EPYC 7R13 Processor")

    def test_aws_cpus_only_on_aws_and_as_real_instance_sizes(self):
        for p in catalog.PERSONAS:
            for hw in p.hardware:
                if hw.cpu_model in self.AWS_CPUS:
                    self.assertEqual(hw.provider, "aws", (p.id, hw))
                if hw.provider == "aws":
                    self.assertIn(hw.cpu_model, self.AWS_CPUS, (p.id, hw))
                    self.assertGreaterEqual(hw.cpus, 2, (p.id, hw))  # t3 / m5 / m6a start at 2 vCPU

    def test_every_server_hardware_has_a_provider_and_host_names(self):
        for pid in SERVERS:
            p = catalog.by_id(pid)
            for hw in p.hardware:
                self.assertIn(hw.provider, ("aws", "kvm", "do", "pi", "dgx", "gpu"), (pid, hw))
                self.assertTrue([x for x in p.hostnames if x.split(":")[0] in (hw.provider,) or ":" not in x],
                                (pid, hw.provider))
                self.assertTrue(any(fits(r, hw) for r in p.releases), (pid, hw.provider))

    def test_chains_are_in_update_order(self):
        for pid in SERVERS:
            for r in catalog.by_id(pid).releases:
                if r.extra.get("superseded"):
                    self.assertGreater(r.extra["superseded"], r.extra["updated"], (pid, r.label))


class DeviceCatalogTest(unittest.TestCase):
    def test_sixteen_in_order(self):
        self.assertEqual(catalog.ids(), SERVERS + DEVICES)
        self.assertEqual(len(catalog.ids()), 16)
        self.assertNotIn("openwrt-1806", catalog.ids())

    def test_device_fields(self):
        for pid in DEVICES:
            p = catalog.by_id(pid)
            self.assertEqual(p.kind, "device", pid)
            self.assertEqual(set(p.package_managers), DEVICE_PACKAGES[pid], pid)
            self.assertGreaterEqual(len(p.releases), 2, pid)
            self.assertLessEqual(len(p.releases), 4, pid)
            self.assertGreaterEqual(len(p.hardware), 1, pid)
            self.assertLessEqual(len(p.hardware), 3, pid)
            self.assertEqual((p.user, p.shell), DEVICE_USERS[pid], pid)
            self.assertTrue(p.vulnerability, pid)

    def test_arch_is_the_byte_order_of_the_soc(self):
        # [shell] arch is the ELF header of `cat /bin/busybox`: ath79 (QCA956x) is big endian
        arches = {p.id: p.arch for p in catalog.PERSONAS}
        self.assertEqual(arches["dlink-dir859"], "linux-mips-msb")
        for pid in ("tplink-wr841n", "openwrt", "ubiquiti-edgerouter-x"):
            self.assertEqual(arches[pid], "linux-mips-lsb", pid)
        for pid in ("zyxel-nas326", "ipcam"):
            self.assertEqual(arches[pid], "linux-arm-lsb", pid)
        self.assertEqual(arches["raspios"], "linux-aarch64-lsb")

    def test_ipcam_is_a_busybox_camera(self):
        p = catalog.by_id("ipcam")
        self.assertEqual((p.ssh_profile, p.hardware_platform), ("dropbear", "armv7l"))
        for hw in p.hardware:
            self.assertEqual((hw.cpus, hw.cpu_model), (1, "ARMv7 Processor rev 5 (v7l)"))
            self.assertTrue(64 * 1024 * 0.9 <= hw.mem_kb <= 256 * 1024, hw)
        self.assertTrue(all(r.extra["busybox"].startswith(("BusyBox v1.19.4 ", "BusyBox v1.26.2 "))
                            for r in p.releases))

    def test_openwrt_runs_the_last_releases_of_two_eol_branches(self):
        p = catalog.by_id("openwrt")
        self.assertEqual(sorted(r.os_version for r in p.releases), ["21.02.7", "22.03.7"])
        self.assertEqual({r.kernel_version.rsplit(".", 1)[0] for r in p.releases}, {"5.4", "5.10"})
        for r in p.releases:
            self.assertEqual(r.extra["target"], "ramips/mt7621")
            # OpenWrt's dropbear hides its version in the banner
            self.assertEqual(r.ssh_banner, "SSH-2.0-dropbear")

    def test_device_hardware_is_realistic(self):
        for pid in DEVICES:
            for hw in catalog.by_id(pid).hardware:
                if "nas" in pid or pid in ("qnap-qts", "synology-dsm"):
                    self.assertTrue(1 <= hw.cpus <= 4, (pid, hw))
                    self.assertTrue(450 * 1024 <= hw.mem_kb <= 8 * 1024 * 1024, (pid, hw))
                else:
                    self.assertLessEqual(hw.mem_kb, 256 * 1024, (pid, hw))
                self.assertNotIn("processor", hw.cpu_model, (pid, hw))

    def test_firmware_levels_have_dates_and_sources(self):
        source = (DIST / "tpot_personas" / "catalog.py").read_text()
        for pid in DEVICES:
            for r in catalog.by_id(pid).releases:
                self.assertTrue(r.extra.get("firmware"), (pid, r.label))
                self.assertTrue(first_install(r), (pid, r.label))
                self.assertIsNotNone(kernel_time(r), (pid, r.label))
                # the kernel was built before the firmware came out
                self.assertLessEqual(kernel_time(r), first_install(r) + 86400, (pid, r.label))
                if r.extra.get("superseded"):
                    self.assertGreater(r.extra["superseded"], r.extra["updated"], (pid, r.label))
        self.assertGreaterEqual(source.count("# source: "), 20)

    def test_device_ssh_version_names_the_banner(self):
        for pid in DEVICES:
            p = catalog.by_id(pid)
            for r in p.releases:
                product = r.ssh_banner[len("SSH-2.0-"):].split(" ", 1)[0]
                if p.ssh_profile.startswith("dropbear"):
                    self.assertTrue(r.ssh_version.startswith("Dropbear "), (pid, r.label))
                    if "_" in product:
                        self.assertIn(product.split("_", 1)[1], r.ssh_version, (pid, r.label))
                else:
                    self.assertRegex(r.ssh_version, rf"^{re.escape(product)}(p\d+)?[ ,]", (pid, r.label))

    def test_prompt_style_fits_the_family(self):
        # bash PS1 of Red Hat is [\u@\h \W]\$, Debian / Ubuntu and the devices keep Cowrie's \u@\h:\w\$
        from tpot_personas.model import PROMPT_STYLES
        for p in catalog.PERSONAS:
            self.assertIn(p.prompt_style, PROMPT_STYLES, p.id)
            self.assertEqual(p.prompt_style, "redhat" if p.family in ("rhel", "centos") else "debian", p.id)

    def test_uname_names_gnu_linux(self):
        # BusyBox uname -o / -a prints "GNU/Linux" (uname.c of 1.14.1, 1.19.2; CONFIG_UNAME_OSNAME since)
        for p in catalog.PERSONAS:
            self.assertEqual(p.operating_system, "GNU/Linux", p.id)

    def test_dropbear_profile_fits_the_version(self):
        # dropbear 2020.79 and later builds without CBC / 3DES / MD5; the legacy profile is for 2012-2017
        for pid in DEVICES:
            p = catalog.by_id(pid)
            for r in p.releases:
                if "dropbear" not in r.ssh_version.lower():
                    continue
                year = int(re.search(r"(\d{4})\.\d+", r.ssh_version).group(1))
                self.assertEqual(p.ssh_profile, "dropbear-openwrt" if year >= 2019 else "dropbear", (pid, r.label))


class SchemaTest(unittest.TestCase):
    def test_env_schema_values_are_the_catalog(self):
        text = (ROOT / "docker/tpotinit/dist/etc/env.schema.yml").read_text()
        block = re.search(r"^  COWRIE_PERSONA:\n((?:    .*\n)+)", text, re.M)
        self.assertIsNotNone(block, "COWRIE_PERSONA missing in env.schema.yml")
        values = re.search(r"^    values: \[(.*)\]$", block.group(1), re.M).group(1)
        self.assertEqual([v.strip() for v in values.split(",")], catalog.ids())
        self.assertIn("COWRIE_PERSONA=", (ROOT / "env.example").read_text())
