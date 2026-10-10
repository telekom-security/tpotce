"""Cowrie fs.pickle tree helpers.

A node is a list [name, type, uid, gid, size, mode, ctime, contents, target,
realfile]; directories keep their children in contents. The layout is the one
Cowrie's pickle loader expects.
"""
import pickle
import stat
from pathlib import Path

A_NAME = 0
A_TYPE = 1
A_UID = 2
A_GID = 3
A_SIZE = 4
A_MODE = 5
A_CTIME = 6
A_CONTENTS = 7
A_TARGET = 8
A_REALFILE = 9

T_LINK = 0
T_DIR = 1
T_FILE = 2


def node_get(node, index, default=None):
    return node[index] if len(node) > index else default


def node_set(node, index, value):
    while len(node) <= index:
        node.append(None)
    node[index] = value


def node_children(node):
    return node_get(node, A_CONTENTS, [])


def find_node(root, relative_path):
    if relative_path in ("", "."):
        return root
    current = root
    for part in Path(relative_path).parts:
        if part in ("", "/"):
            continue
        current = next(
            (child for child in node_children(current) if node_get(child, A_NAME) == part),
            None,
        )
        if current is None:
            return None
    return current


def ensure_dir(root, relative_path, uid=0, gid=0, mode=0o755, ctime=0):
    current = root
    for part in Path(relative_path).parts:
        if part in ("", "/"):
            continue
        match = next(
            (child for child in node_children(current) if node_get(child, A_NAME) == part),
            None,
        )
        if match is None:
            match = [part, T_DIR, uid, gid, 4096, stat.S_IFDIR | mode, ctime, [], None, None]
            node_children(current).append(match)
        current = match
    node_set(current, A_TYPE, T_DIR)
    node_set(current, A_UID, uid)
    node_set(current, A_GID, gid)
    node_set(current, A_MODE, stat.S_IFDIR | mode)
    return current


def ensure_file(root, relative_path, content, uid=0, gid=0, mode=0o644, ctime=0):
    parent = ensure_dir(root, str(Path(relative_path).parent), ctime=ctime)
    name = Path(relative_path).name
    node = next(
        (child for child in node_children(parent) if node_get(child, A_NAME) == name),
        None,
    )
    if node is None:
        node = [name, T_FILE, uid, gid, len(content), stat.S_IFREG | mode, ctime, [], None, None]
        node_children(parent).append(node)

    node_set(node, A_NAME, name)
    node_set(node, A_TYPE, T_FILE)
    node_set(node, A_UID, uid)
    node_set(node, A_GID, gid)
    node_set(node, A_SIZE, len(content))
    node_set(node, A_MODE, stat.S_IFREG | mode)
    if not node_get(node, A_CTIME):
        node_set(node, A_CTIME, ctime)
    node_set(node, A_CONTENTS, content)
    node_set(node, A_TARGET, None)
    node_set(node, A_REALFILE, None)
    return node


def remove_node(root, relative_path):
    parts = [part for part in Path(relative_path).parts if part not in ("", "/")]
    if not parts:
        return
    parent = root
    for part in parts[:-1]:
        parent = next(
            (child for child in node_children(parent) if node_get(child, A_NAME) == part),
            None,
        )
        if parent is None:
            return
    children = node_children(parent)
    children[:] = [child for child in children if node_get(child, A_NAME) != parts[-1]]


def iter_nodes(root, prefix=""):
    for child in node_children(root):
        path = f"{prefix}/{node_get(child, A_NAME)}"
        yield path, child
        if node_get(child, A_TYPE) == T_DIR:
            yield from iter_nodes(child, path)


def load_pickle(path):
    with Path(path).open("rb") as handle:
        try:
            return pickle.load(handle)
        except UnicodeDecodeError:
            handle.seek(0)
            return pickle.load(handle, encoding="utf-8")


def dump_pickle(tree, path):
    with Path(path).open("wb") as handle:
        pickle.dump(tree, handle)
