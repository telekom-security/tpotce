import calendar
import re
import time
import unittest
from pathlib import Path

from tests.helpers import DIST  # noqa: F401
from tpot_personas import PersonaError, catalog
from tpot_personas.identity import first_install, kernel_time
from tpot_personas.ssh_profiles import PROFILES

ROOT = Path(__file__).resolve().parents[3]

SERVERS = ["ubuntu-noble", "ubuntu-jammy", "debian-trixie", "debian-bookworm",
           "rhel-9", "centos-7", "raspios", "gpu-ubuntu"]
DEVICES = ["dlink-dir859", "tplink-wr841n", "zyxel-nas326", "openwrt-1806", "qnap-qts", "synology-dsm",
           "ubiquiti-edgerouter-x"]
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

    def test_servers_then_the_ported_devices(self):
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
                product = "dropbear" if p.ssh_profile == "dropbear" else "OpenSSH"
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
