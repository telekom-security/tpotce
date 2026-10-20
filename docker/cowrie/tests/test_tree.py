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

    def test_ensure_file_keeps_the_ctime_of_an_existing_file(self):
        root = base_tree()
        motd = tree.ensure_file(root, "etc/motd", b"new\n", ctime=42)
        self.assertEqual(tree.node_get(motd, tree.A_CTIME), 1_300_000_000)
        self.assertEqual(tree.node_get(motd, tree.A_CONTENTS), b"new\n")
        # a node without a ctime (0) takes the one given
        tree.node_set(motd, tree.A_CTIME, 0)
        self.assertEqual(tree.node_get(tree.ensure_file(root, "etc/motd", b"x", ctime=42), tree.A_CTIME), 42)
        # ctime=0 gives none
        self.assertEqual(tree.node_get(tree.ensure_file(root, "etc/new.conf", b"x"), tree.A_CTIME), 0)

    def test_ensure_dir_gives_the_ctime_to_new_folders_only(self):
        root = base_tree()
        tree.ensure_file(root, "etc/tpot/a/b.conf", b"x", ctime=42)
        self.assertEqual([tree.node_get(tree.find_node(root, rel), tree.A_CTIME)
                          for rel in ("etc", "etc/tpot", "etc/tpot/a")], [1_300_000_000, 42, 42])
        tree.ensure_dir(root, "etc/tpot/a", mode=0o700, ctime=99)
        node = tree.find_node(root, "etc/tpot/a")
        self.assertEqual((tree.node_get(node, tree.A_CTIME), tree.node_get(node, tree.A_MODE) & 0o777), (42, 0o700))

    def test_ensure_link_and_device_take_the_ctime(self):
        root = base_tree()
        link = tree.ensure_link(root, "etc/motd", "/run/motd", ctime=42)
        self.assertEqual((tree.node_get(link, tree.A_TYPE), tree.node_get(link, tree.A_CTIME)), (tree.T_LINK, 42))
        device = tree.ensure_device(root, "dev/pts/0", ctime=43)
        self.assertEqual(tree.node_get(device, tree.A_CTIME), 43)
        self.assertEqual(tree.node_get(tree.find_node(root, "dev/pts"), tree.A_CTIME), 43)

    def test_iter_nodes_goes_into_every_folder_and_no_link(self):
        root = base_tree()
        tree.ensure_file(root, "etc/tpot/a/b/c.conf", b"x")
        paths = [p for p, _ in tree.iter_nodes(root)]
        self.assertEqual([p for p in paths if p.startswith("/etc/tpot")],
                         ["/etc/tpot", "/etc/tpot/a", "/etc/tpot/a/b", "/etc/tpot/a/b/c.conf"])
        # bin -> usr/bin: its programs are listed once, under usr/bin
        self.assertIn("/bin", paths)
        self.assertEqual([p for p in paths if p.startswith("/bin/")], [])
        self.assertIn("/usr/bin/top", paths)
        self.assertEqual(len(paths), len(set(paths)))
        self.assertEqual([p for p, _ in tree.iter_nodes(tree.find_node(root, "etc/tpot"), "/etc/tpot")],
                         ["/etc/tpot/a", "/etc/tpot/a/b", "/etc/tpot/a/b/c.conf"])

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
