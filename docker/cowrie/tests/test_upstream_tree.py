"""Every persona on Cowrie's own fs.pickle (the tree the image renders from), before any image build.

Set TPOT_COWRIE_FS_PICKLE to the fs.pickle of the pinned Cowrie release, e.g.
    git -C <cowrie clone> show v3.1.3:src/cowrie/data/fs.pickle > /tmp/fs.pickle
    TPOT_COWRIE_FS_PICKLE=/tmp/fs.pickle python3 -m unittest tests.test_upstream_tree
The pickle is read as data only (tests.helpers.load_data_pickle); without the variable the test skips."""
import os
import unittest

from tests.helpers import load_data_pickle, tmpdir
from tpot_personas import catalog, tree, validate
from tpot_personas.identity import draw
from tpot_personas.render import COMMON_REMOVE_PATHS, render

SEEDS = 3


@unittest.skipUnless(os.environ.get("TPOT_COWRIE_FS_PICKLE"), "TPOT_COWRIE_FS_PICKLE is not set")
class UpstreamTreeTest(unittest.TestCase):
    def test_every_persona_validates_on_cowries_tree(self):
        base = load_data_pickle(os.environ["TPOT_COWRIE_FS_PICKLE"])
        for rel in COMMON_REMOVE_PATHS:   # as generate_cowrie_fs.py cleans it into base.pickle
            tree.remove_node(base, rel)
        for persona in catalog.PERSONAS:
            for seed in range(SEEDS):
                with self.subTest(persona=persona.id, seed=seed):
                    out = tmpdir(self) / persona.id
                    ident = draw(persona, seed)
                    render(persona, ident, base, out, runtime_dir=out, keys_dir=out / "keys")
                    self.assertEqual(validate.problems(persona, ident, out, out, out / "keys"), [])


if __name__ == "__main__":
    unittest.main()
