import unittest

from tests.helpers import DIST  # noqa: F401
from tpot_personas import catalog
from tpot_personas.identity import DAY, draw, first_install, kernel_time, ready_at, to_json

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
