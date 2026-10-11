"""Cowrie fs.pickle tree helpers.

A node is a list [name, type, uid, gid, size, mode, ctime, contents, target,
realfile]; directories keep their children in contents. The layout is the one
Cowrie's pickle loader expects.

Paths follow the links of their directories as Cowrie does: in Cowrie's Debian
tree /bin, /sbin and /lib are links to usr/bin, usr/sbin and usr/lib, so
bin/df is the node usr/bin/df. The last part of a path is not followed
(remove_node removes a link, ensure_file replaces one), except by ensure_dir.
"""
import pickle
import posixpath
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
T_CHR = 4   # Cowrie's fs.py: T_LINK, T_DIR, T_FILE, T_BLK, T_CHR, T_SOCK, T_FIFO

MAX_LINKS = 40


def node_get(node, index, default=None):
    return node[index] if len(node) > index else default


def node_set(node, index, value):
    while len(node) <= index:
        node.append(None)
    node[index] = value


def node_children(node):
    return node_get(node, A_CONTENTS, [])


def _child(node, name):
    children = node_children(node)
    if not isinstance(children, list):
        return None
    return next((child for child in children if node_get(child, A_NAME) == name), None)


def _parts(relative_path):
    return [part for part in Path(relative_path).parts if part not in ("", "/", ".")]


def _link_path(link):
    """The parts a link points to. Cowrie's pickle stores link targets from the root
    ("usr/bin/python3.11" for usr/bin/python3) and fs.getfile resolves them so."""
    target = node_get(link, A_TARGET) or ""
    return [part for part in posixpath.normpath("/" + target).split("/") if part]


def _walk(root, parts, depth=0):
    """(directory node, its real parts) of parts, following links; (None, None) if one is missing."""
    current, real = root, []
    for part in parts:
        if part == "..":
            current, real = _walk(root, real[:-1], depth + 1)
        else:
            child = _child(current, part)
            if child is not None and node_get(child, A_TYPE) == T_LINK:
                if depth >= MAX_LINKS or not node_get(child, A_TARGET):
                    return None, None
                current, real = _walk(root, _link_path(child), depth + 1)
            else:
                current, real = child, real + [part]
        if current is None:
            return None, None
    return current, real


def find_node(root, relative_path):
    """The node of the path; the links of its directories are followed, the last part is not."""
    parts = _parts(relative_path)
    if not parts:
        return root
    parent, _ = _walk(root, parts[:-1])
    return None if parent is None else _child(parent, parts[-1])


def find_dir(root, relative_path):
    """The directory node of the path, links followed (also the last part); None if there is none."""
    node, _ = _walk(root, _parts(relative_path))
    return node if node is not None and node_get(node, A_TYPE) == T_DIR else None


def real_path(root, relative_path):
    """The path with the links of its directories resolved ("bin/df" -> "usr/bin/df"),
    None if a directory of it is missing."""
    parts = _parts(relative_path)
    if not parts:
        return ""
    _, real = _walk(root, parts[:-1])
    return None if real is None else "/".join(real + [parts[-1]])


def ensure_dir(root, relative_path, uid=0, gid=0, mode=0o755, ctime=0):
    """The directory of the path, created where missing; a link to a directory is
    followed (also as the last part), any other link becomes a directory."""
    current, real = root, []
    for part in _parts(relative_path):
        match = _child(current, part)
        if match is not None and node_get(match, A_TYPE) == T_LINK and node_get(match, A_TARGET):
            target, target_real = _walk(root, _link_path(match), 1)
            if target is not None and node_get(target, A_TYPE) == T_DIR:
                current, real = target, target_real
                continue
        if match is None:
            match = [part, T_DIR, uid, gid, 4096, stat.S_IFDIR | mode, ctime, [], None, None]
            node_children(current).append(match)
        node_set(match, A_TYPE, T_DIR)
        if not isinstance(node_get(match, A_CONTENTS), list):
            node_set(match, A_CONTENTS, [])
        node_set(match, A_TARGET, None)
        current, real = match, real + [part]
    node_set(current, A_TYPE, T_DIR)
    node_set(current, A_UID, uid)
    node_set(current, A_GID, gid)
    node_set(current, A_MODE, stat.S_IFDIR | mode)
    return current


def ensure_file(root, relative_path, content, uid=0, gid=0, mode=0o644, ctime=0):
    parent = ensure_dir(root, str(Path(relative_path).parent), ctime=ctime)
    name = Path(relative_path).name
    node = _child(parent, name)
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


def ensure_executable(root, relative_path, size, uid=0, gid=0, mode=0o755, ctime=0):
    """A program Cowrie runs as a txtcmd or a Python command: a file node without
    contents, so `cat` / `dd` get the ELF header of [shell] arch (fs.file_contents
    serves it for an executable of non-zero size without contents). An existing
    program keeps its size and ctime; an empty one, a link or a missing one gets `size`."""
    parent = ensure_dir(root, str(Path(relative_path).parent), ctime=ctime)
    name = Path(relative_path).name
    node = _child(parent, name)
    if node is None:
        node = [name, T_FILE, uid, gid, size, stat.S_IFREG | mode, ctime, [], None, None]
        node_children(parent).append(node)
    if node_get(node, A_TYPE) != T_FILE or not node_get(node, A_SIZE):
        node_set(node, A_SIZE, size)
    node_set(node, A_TYPE, T_FILE)
    node_set(node, A_UID, uid)
    node_set(node, A_GID, gid)
    node_set(node, A_MODE, stat.S_IFREG | mode)
    if not node_get(node, A_CTIME):
        node_set(node, A_CTIME, ctime)
    node_set(node, A_CONTENTS, [])
    node_set(node, A_TARGET, None)
    node_set(node, A_REALFILE, None)
    return node


def ensure_device(root, relative_path, uid=0, gid=0, mode=0o600, ctime=0):
    """A character device (a terminal of /dev/pts)."""
    parent = find_dir(root, str(Path(relative_path).parent)) or ensure_dir(root, str(Path(relative_path).parent),
                                                                          ctime=ctime)
    name = Path(relative_path).name
    children = node_children(parent)
    children[:] = [child for child in children if node_get(child, A_NAME) != name]
    node = [name, T_CHR, uid, gid, 0, stat.S_IFCHR | mode, ctime, [], None, None]
    children.append(node)
    return node


def ensure_link(root, relative_path, target, ctime=0):
    """A link; target is from the root ("usr/bin/python2.7"), as in Cowrie's pickle."""
    parent = ensure_dir(root, str(Path(relative_path).parent), ctime=ctime)
    name = Path(relative_path).name
    children = node_children(parent)
    children[:] = [child for child in children if node_get(child, A_NAME) != name]
    node = [name, T_LINK, 0, 0, len(target), stat.S_IFLNK | 0o777, ctime, [], target, None]
    children.append(node)
    return node


def remove_node(root, relative_path):
    parts = _parts(relative_path)
    if not parts:
        return
    parent, _ = _walk(root, parts[:-1])
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
