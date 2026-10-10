import unittest

from tests.helpers import base_tree, tmpdir
from tpot_personas import tree


class TreeTest(unittest.TestCase):
    def test_ensure_file_creates_parents_and_sets_fields(self):
        root = base_tree()
        node = tree.ensure_file(root, "etc/ssh/sshd_config", b"Port 22\n", mode=0o600, ctime=42)
        self.assertEqual(tree.node_get(node, tree.A_SIZE), 8)
        self.assertEqual(tree.node_get(node, tree.A_CONTENTS), b"Port 22\n")
        self.assertEqual(tree.node_get(node, tree.A_CTIME), 42)
        self.assertEqual(tree.node_get(node, tree.A_MODE) & 0o777, 0o600)
        self.assertIsNotNone(tree.find_node(root, "etc/ssh"))

    def test_remove_node_and_find(self):
        root = base_tree()
        tree.remove_node(root, "home/phil")
        self.assertIsNone(tree.find_node(root, "home/phil"))
        tree.remove_node(root, "does/not/exist")  # no error

    def test_iter_nodes_yields_absolute_paths(self):
        paths = [p for p, _ in tree.iter_nodes(base_tree())]
        self.assertIn("/etc/motd", paths)
        self.assertIn("/usr/bin", paths)

    def test_pickle_round_trip(self):
        path = tmpdir(self) / "fs.pickle"
        tree.dump_pickle(base_tree(), path)
        self.assertIsNotNone(tree.find_node(tree.load_pickle(path), "etc/motd"))
