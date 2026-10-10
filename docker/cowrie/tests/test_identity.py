import unittest

from tests.helpers import DIST  # noqa: F401
from tpot_personas import catalog
from tpot_personas.identity import DAY, draw, to_json

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
