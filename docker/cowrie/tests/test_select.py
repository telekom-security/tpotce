import random
import unittest

from tests.helpers import DIST  # noqa: F401
from tpot_personas import PersonaError
from tpot_personas.select import choose, parse_selection

IDS = ["ubuntu-noble", "debian-trixie", "ipcam"]


class SelectTest(unittest.TestCase):
    def test_empty_means_all(self):
        for value in (None, "", "   "):
            self.assertEqual(parse_selection(value, IDS), IDS)

    def test_random_is_no_alias(self):
        # tpotinit's choice_list rejects it, so the container does too
        with self.assertRaises(PersonaError) as ctx:
            parse_selection("random", IDS)
        self.assertEqual(str(ctx.exception), 'unknown COWRIE_PERSONA "random", allowed: ubuntu-noble debian-trixie ipcam')

    def test_list_with_any_whitespace(self):
        self.assertEqual(parse_selection("  ipcam\tubuntu-noble \n", IDS), ["ipcam", "ubuntu-noble"])

    def test_unknown_names_allowed_ids(self):
        with self.assertRaises(PersonaError) as ctx:
            parse_selection("ipcam ../etc", IDS)
        self.assertIn("../etc", str(ctx.exception))
        self.assertIn("ubuntu-noble", str(ctx.exception))

    def test_duplicate_is_an_error_that_names_the_allowed_ids(self):
        with self.assertRaises(PersonaError) as ctx:
            parse_selection("ipcam debian-trixie ipcam", IDS)
        message = str(ctx.exception)
        self.assertEqual(message, 'COWRIE_PERSONA names "ipcam" twice, allowed: ubuntu-noble debian-trixie ipcam')
        self.assertNotIn("\n", message)

    def test_choose_stays_in_selection(self):
        rng = random.Random(1)
        for _ in range(50):
            self.assertIn(choose("ipcam debian-trixie", IDS, rng), {"ipcam", "debian-trixie"})
