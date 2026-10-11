import unittest

from tests.helpers import base_tree, tmpdir
from tpot_personas import tree


class TreeTest(unittest.TestCase):
    def test_ensure_file_creates_parents_and_sets_fields(self):
        root = base_tree()
        node = tree.ensure_file(root, "etc/ssh/ssh_config.d/tpot.conf", b"Port 22\n", mode=0o600, ctime=42)
        self.assertEqual(tree.node_get(node, tree.A_SIZE), 8)
        self.assertEqual(tree.node_get(node, tree.A_CONTENTS), b"Port 22\n")
        self.assertEqual(tree.node_get(node, tree.A_CTIME), 42)
        self.assertEqual(tree.node_get(node, tree.A_MODE) & 0o777, 0o600)
        self.assertIsNotNone(tree.find_node(root, "etc/ssh/ssh_config.d"))

    def test_remove_node_and_find(self):
        root = base_tree()
        tree.remove_node(root, "home/phil")
        self.assertIsNone(tree.find_node(root, "home/phil"))
        tree.remove_node(root, "does/not/exist")  # no error

    def test_iter_nodes_yields_absolute_paths(self):
        paths = [p for p, _ in tree.iter_nodes(base_tree())]
        self.assertIn("/etc/motd", paths)
        self.assertIn("/usr/bin", paths)

    def test_paths_follow_the_links_of_their_directories(self):
        # Cowrie's Debian tree: bin -> usr/bin; the last part is not followed
        root = base_tree()
        self.assertIs(tree.find_node(root, "bin/top"), tree.find_node(root, "usr/bin/top"))
        self.assertEqual(tree.node_get(tree.find_node(root, "bin"), tree.A_TYPE), tree.T_LINK)
        self.assertEqual(tree.node_get(tree.find_node(root, "usr/bin/python3"), tree.A_TYPE), tree.T_LINK)
        self.assertEqual(tree.real_path(root, "bin/df"), "usr/bin/df")
        self.assertEqual(tree.real_path(root, "sbin/qpkg_cli"), "usr/sbin/qpkg_cli")
        self.assertEqual(tree.real_path(root, "etc/motd"), "etc/motd")
        self.assertIsNone(tree.real_path(root, "nowhere/x"))

    def test_ensure_dir_keeps_a_link_to_a_directory(self):
        root = base_tree()
        self.assertIs(tree.ensure_dir(root, "bin"), tree.find_node(root, "usr/bin"))
        self.assertEqual(tree.node_get(tree.find_node(root, "bin"), tree.A_TYPE), tree.T_LINK)
        tree.ensure_file(root, "bin/new", b"x")
        self.assertIsNotNone(tree.find_node(root, "usr/bin/new"))

    def test_remove_node_through_a_link_removes_the_target(self):
        root = base_tree()
        tree.remove_node(root, "bin/apt")
        self.assertIsNone(tree.find_node(root, "usr/bin/apt"))
        self.assertIsNotNone(tree.find_node(root, "bin"))

    def test_a_link_loop_ends(self):
        root = base_tree()
        for name, target in (("a", "b"), ("b", "a")):
            tree.node_children(root).append([name, tree.T_LINK, 0, 0, 1, 0o120777, 0, [], target, None])
        self.assertIsNone(tree.find_node(root, "a/x"))
        self.assertIsNone(tree.real_path(root, "a/x"))

    def test_ensure_executable_keeps_a_size_and_drops_contents(self):
        root = base_tree()
        top = tree.ensure_executable(root, "usr/bin/top", 999, ctime=42)
        self.assertEqual(tree.node_get(top, tree.A_SIZE), 134736)
        self.assertEqual(tree.node_get(top, tree.A_CTIME), 1_300_000_000)
        new = tree.ensure_executable(root, "bin/busybox", 500_000, ctime=42)
        self.assertIs(new, tree.find_node(root, "usr/bin/busybox"))
        self.assertEqual(tree.node_get(new, tree.A_SIZE), 500_000)
        self.assertEqual(tree.node_get(new, tree.A_MODE) & 0o777, 0o755)
        self.assertNotIsInstance(tree.node_get(new, tree.A_CONTENTS), bytes)
        empty = tree.ensure_file(root, "usr/bin/nproc", b"", mode=0o755)
        tree.ensure_executable(root, "usr/bin/nproc", 31_000)
        self.assertEqual((tree.node_get(empty, tree.A_SIZE), tree.node_get(empty, tree.A_CONTENTS)), (31_000, []))
        link = tree.ensure_executable(root, "usr/bin/python3", 7_000)
        self.assertEqual((tree.node_get(link, tree.A_TYPE), tree.node_get(link, tree.A_TARGET)), (tree.T_FILE, None))

    def test_pickle_round_trip(self):
        path = tmpdir(self) / "fs.pickle"
        tree.dump_pickle(base_tree(), path)
        self.assertIsNotNone(tree.find_node(tree.load_pickle(path), "etc/motd"))
