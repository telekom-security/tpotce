import json
import unittest

from tests.helpers import base_tree, tmpdir
from tpot_personas import catalog, tree, validate
from tpot_personas.identity import draw
from tpot_personas.render import render

NOW = 1_790_000_000


def render_one(test, persona, seed):
    out = tmpdir(test) / persona.id
    keys = tmpdir(test) / "etc" / persona.id
    ident = draw(persona, seed, NOW)
    render(persona, ident, base_tree(), out, runtime_dir=out, keys_dir=keys)
    return ident, out, keys


class RenderTest(unittest.TestCase):
    def test_every_persona_renders_and_validates(self):
        for persona in catalog.PERSONAS:
            for seed in range(20):
                ident, out, keys = render_one(self, persona, seed)
                self.assertEqual(
                    validate.problems(persona, ident, out, out, keys, min_pickle=0), [],
                    (persona.id, seed))

    def test_identity_json_round_trips(self):
        persona = catalog.PERSONAS[0]
        ident, out, _ = render_one(self, persona, 3)
        data = json.loads((out / "identity.json").read_text())
        self.assertEqual(data["persona"], persona.id)
        self.assertEqual(data["hostname"], ident.hostname)
        self.assertEqual(data["boot_offset"], ident.boot_offset)

    def test_phil_is_gone(self):
        persona = catalog.PERSONAS[0]
        _, out, _ = render_one(self, persona, 1)
        self.assertNotIn(b"phil", (out / "fs.pickle").read_bytes().lower())

    def test_same_identity_same_output(self):
        persona = catalog.by_id("debian-bookworm-vuln")
        _, first, _ = render_one(self, persona, 9)
        _, second, _ = render_one(self, persona, 9)
        for rel in ("cmdoutput.json", "honeyfs/etc/shadow", "honeyfs/proc/meminfo", "txtcmds/usr/bin/top"):
            self.assertEqual((first / rel).read_text(), (second / rel).read_text(), rel)

    def test_restricted_directories_keep_owner_and_mode(self):
        # tree.ensure_file resets parent directories to 0:0 0755; the renderer
        # must put /root, /proc, /tmp and the home of the persona user back.
        for persona in catalog.PERSONAS:
            _, out, _ = render_one(self, persona, 0)
            fs = tree.load_pickle(out / "fs.pickle")
            mode = lambda rel: tree.node_get(tree.find_node(fs, rel), tree.A_MODE) & 0o7777  # noqa: E731
            self.assertEqual(mode("root"), 0o700, persona.id)
            self.assertEqual(mode("proc"), 0o555, persona.id)
            self.assertEqual(mode("tmp"), 0o1777, persona.id)
            home = "root" if persona.user == "root" else f"home/{persona.user}"
            node = tree.find_node(fs, home)
            self.assertEqual((tree.node_get(node, tree.A_UID), tree.node_get(node, tree.A_GID)),
                             (persona.uid, persona.gid), persona.id)
            for path, child in tree.iter_nodes(node, "/" + home):
                self.assertEqual(tree.node_get(child, tree.A_UID), persona.uid, (persona.id, path))
