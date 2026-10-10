import random
import unittest

from tests.helpers import DIST  # noqa: F401
from tpot_personas import PersonaError
from tpot_personas.select import choose, parse_selection

IDS = ["ubuntu-noble", "debian-trixie", "ipcam"]


class SelectTest(unittest.TestCase):
    def test_empty_means_all(self):
        for value in (None, "", "   ", "random"):
            self.assertEqual(parse_selection(value, IDS), IDS)

    def test_list_with_any_whitespace(self):
        self.assertEqual(parse_selection("  ipcam\tubuntu-noble \n", IDS), ["ipcam", "ubuntu-noble"])

    def test_unknown_names_allowed_ids(self):
        with self.assertRaises(PersonaError) as ctx:
            parse_selection("ipcam ../etc", IDS)
        self.assertIn("../etc", str(ctx.exception))
        self.assertIn("ubuntu-noble", str(ctx.exception))

    def test_duplicate_is_an_error(self):
        with self.assertRaises(PersonaError):
            parse_selection("ipcam ipcam", IDS)

    def test_choose_stays_in_selection(self):
        rng = random.Random(1)
        for _ in range(50):
            self.assertIn(choose("ipcam debian-trixie", IDS, rng), {"ipcam", "debian-trixie"})
