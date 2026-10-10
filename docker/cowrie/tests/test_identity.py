import re
import unittest

from tests.helpers import DIST  # noqa: F401
from tpot_personas import catalog
from tpot_personas.identity import (DAY, draw, first_install, host_patterns, hostname_regex, is_medium, kernel_time,
                                    log_window, ready_at, superseded_at, to_json)

NOW = 1_790_000_000  # fixed "now" for reproducible tests


class IdentityTest(unittest.TestCase):
    def test_same_seed_same_identity(self):
        p = catalog.PERSONAS[0]
        self.assertEqual(draw(p, 7, NOW), draw(p, 7, NOW))

    def test_dates_are_ordered_and_never_in_the_future(self):
        for p in catalog.PERSONAS:
            for seed in range(200):
                i = draw(p, seed, NOW)
                self.assertLessEqual(i.installed_at, i.booted_at, (p.id, seed))
                self.assertLess(i.booted_at, NOW, (p.id, seed))
                self.assertGreaterEqual(i.boot_offset, DAY, (p.id, seed))
                limit = 400 if p.kind == "device" else 120
                self.assertLessEqual(i.boot_offset, (limit + 1) * DAY, (p.id, seed))
                self.assertGreaterEqual(NOW - i.installed_at, 90 * DAY, (p.id, seed))
                self.assertLessEqual(NOW - i.installed_at, 900 * DAY, (p.id, seed))
                # a system is not installed before its release or image existed
                self.assertGreaterEqual(i.installed_at, first_install(i.release), (p.id, seed))
                # it runs the kernel it booted, so that kernel was built (and the update made) before the boot
                self.assertLessEqual((kernel_time(i.release) or 0) + DAY, i.booted_at, (p.id, seed))
                self.assertLessEqual(ready_at(i.release) + DAY, i.booted_at, (p.id, seed))

    def test_ordering_holds_for_any_clock(self):
        # a clock long before the releases (or far after them) must still give ordered dates
        for now in (1_500_000_000, NOW, 2_200_000_000):
            for p in catalog.PERSONAS:
                for seed in range(30):
                    i = draw(p, seed, now)
                    self.assertLessEqual(i.installed_at, i.booted_at, (p.id, now, seed))
                    self.assertGreaterEqual(i.boot_offset, DAY, (p.id, now, seed))
                    self.assertGreaterEqual(now - i.installed_at, 90 * DAY, (p.id, now, seed))
                    self.assertLessEqual(now - i.installed_at, 900 * DAY, (p.id, now, seed))

    def test_hardware_tokens_in_host_names_match_the_hardware(self):
        p = catalog.by_id("ubuntu-noble")
        for seed in range(200):
            i = draw(p, seed, NOW)
            if i.hostname.startswith("ubuntu-s-"):
                gib = max(1, round(i.hardware.mem_kb / 1024 / 1024))
                self.assertTrue(i.hostname.startswith(f"ubuntu-s-{i.hardware.cpus}vcpu-{gib}gb-"), i.hostname)
                break
        else:
            self.fail("no droplet host name in 200 seeds")

    def test_raspberry_pi_kernel_fits_the_board(self):
        p = catalog.by_id("raspios")
        for seed in range(50):
            i = draw(p, seed, NOW)
            flavor = "rpi-2712" if "Pi 5" in i.hardware.board else "rpi-v8"
            self.assertTrue(i.release.kernel_version.endswith(flavor), (seed, i.release.kernel_version))
            self.assertNotIn("{", i.release.kernel_version)

    def test_hostname_tokens_are_expanded(self):
        for p in catalog.PERSONAS:
            for seed in range(50):
                host = draw(p, seed, NOW).hostname
                self.assertRegex(host, r"^[A-Za-z0-9][A-Za-z0-9.-]{0,62}$", (p.id, host))
                self.assertNotIn("{", host)

    def test_seeds_vary_the_identity(self):
        p = catalog.by_id("ubuntu-jammy")
        seen = {(draw(p, s, NOW).boot_offset, draw(p, s, NOW).installed_at) for s in range(20)}
        self.assertGreater(len(seen), 15)

    def test_to_json_names_persona_and_boot(self):
        p = catalog.PERSONAS[0]
        ident = draw(p, 3, NOW)
        data = to_json(ident)
        self.assertEqual(data["persona"], p.id)
        self.assertEqual(data["booted_at"], NOW - ident.boot_offset)
        self.assertIsInstance(data["release"], dict)
        self.assertIsInstance(data["hardware"], dict)


class ProviderTest(unittest.TestCase):
    """Host name, kernel and hardware tell the same story about where the system runs."""

    def test_host_names_fit_the_provider_of_the_hardware(self):
        for p in catalog.PERSONAS:
            for seed in range(2000 if p.kind == "server" else 50):
                i = draw(p, seed, NOW)
                patterns = host_patterns(p, i.hardware)
                self.assertTrue(any(re.fullmatch(hostname_regex(x, i.hardware), i.hostname) for x in patterns),
                                (p.id, seed, i.hostname, i.hardware.provider))

    def test_noble_on_aws_runs_the_aws_kernel(self):
        p = catalog.by_id("ubuntu-noble")
        seen = set()
        for seed in range(2000):
            i = draw(p, seed, NOW)
            aws = i.hardware.provider == "aws"
            seen.add(i.hardware.provider)
            self.assertEqual(i.release.kernel_version.endswith("-aws"), aws, (seed, i.release.label))
            self.assertEqual(i.hostname.startswith("ip-172-31-"), aws, (seed, i.hostname))
            if i.hostname.startswith("ubuntu-s-"):
                self.assertEqual(i.hardware.provider, "do", seed)
        self.assertEqual(seen, {"aws", "kvm", "do"})

    def test_dgx_names_only_on_the_dgx(self):
        p = catalog.by_id("gpu-ubuntu")
        for seed in range(2000):
            i = draw(p, seed, NOW)
            if i.hostname.startswith(("dgx", "a100-")):
                self.assertEqual(i.hardware.gpus, ("NVIDIA A100-SXM4-80GB",) * 8, (seed, i.hostname))


class PatchLevelTest(unittest.TestCase):
    """Logged package versions are the current ones on the logged day."""

    def test_installed_before_the_next_patch_level_unless_from_its_image(self):
        for p in catalog.PERSONAS:
            for seed in range(500 if p.kind == "server" else 20):
                i = draw(p, seed, NOW)
                if superseded_at(i.release) and not is_medium(i.release):
                    self.assertLessEqual(i.installed_at, superseded_at(i.release), (p.id, seed, i.release.label))

    def test_log_window_is_the_time_the_versions_were_current(self):
        for p in catalog.PERSONAS:
            for seed in range(500 if p.kind == "server" else 20):
                i = draw(p, seed, NOW)
                window = log_window(i)
                if window is None:
                    # only an image install after the next patch level logs nothing
                    self.assertTrue(is_medium(i.release), (p.id, seed))
                    self.assertGreater(i.installed_at, superseded_at(i.release), (p.id, seed))
                    continue
                first, last = window
                self.assertGreaterEqual(first, i.installed_at)
                if superseded_at(i.release):
                    self.assertLessEqual(last, superseded_at(i.release), (p.id, seed))

    def test_the_regresshion_bait_stays_reachable(self):
        p = catalog.by_id("debian-bookworm")
        banners = {draw(p, seed, NOW).release.ssh_banner for seed in range(500)}
        self.assertIn("SSH-2.0-OpenSSH_9.2p1 Debian-2+deb12u1", banners)
        self.assertIn("SSH-2.0-OpenSSH_9.2p1 Debian-2+deb12u2", banners)

    def test_every_server_draws_at_least_three_patch_levels(self):
        for p in catalog.PERSONAS:
            if p.kind == "server":
                labels = {draw(p, seed, NOW).release.label for seed in range(500)}
                self.assertGreaterEqual(len(labels), 3, (p.id, labels))
