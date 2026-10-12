"""Shared helpers for the tpot_personas tests (stdlib only)."""
import io
import pickle
import pickletools
import sys
import tempfile
from pathlib import Path

DIST = Path(__file__).resolve().parents[1] / "dist"
if str(DIST) not in sys.path:
    sys.path.insert(0, str(DIST))

from tpot_personas import tree  # noqa: E402

OLD = 1_300_000_000


def _link(root, rel, target):
    parent = tree.ensure_dir(root, str(Path(rel).parent), ctime=OLD)
    tree.node_children(parent).append([Path(rel).name, tree.T_LINK, 0, 0, len(target), 0o120777, OLD, [],
                                       target, None])


def _data_file(root, rel, size):
    node = tree.ensure_file(root, rel, b"", ctime=OLD)
    tree.node_set(node, tree.A_SIZE, size)
    tree.node_set(node, tree.A_CONTENTS, [])
    return node


def _program(root, rel, size):
    node = tree.ensure_file(root, rel, b"", mode=0o755, ctime=OLD)
    # Cowrie's pickle: programs have their size and no contents (an empty list)
    tree.node_set(node, tree.A_SIZE, size)
    tree.node_set(node, tree.A_CONTENTS, [])
    return node


def base_tree():
    """A small stand-in for Cowrie's fs.pickle (Debian 12, merged /usr): the
    links bin, sbin, lib and lib64, programs with their sizes, the parts of
    another system the renderer removes or renames (home/phil, Debian package
    files, x86_64 libraries, systemd, sshd_config, /.dockerenv, Debian's
    /proc/modules, Python 3.11, Perl, adduser and which.debianutils, the
    /proc/self and stdout of the container's Python) and an old ctime."""
    root = ["", tree.T_DIR, 0, 0, 4096, 0o40755, OLD, [], None, None]
    for rel in ("etc", "home/phil", "usr/bin", "usr/sbin", "usr/lib", "usr/lib64", "var/log", "proc", "root",
                "tmp"):
        tree.ensure_dir(root, rel, ctime=OLD)
    for name in ("bin", "sbin", "lib", "lib64"):
        _link(root, name, f"usr/{name}")
    tree.ensure_file(root, "etc/motd", b"phil was here\n", ctime=OLD)
    tree.ensure_file(root, "etc/debian_version", b"12.5\n", ctime=OLD)
    tree.ensure_file(root, "etc/apt/sources.list.d/debian.sources", b"Types: deb\n", ctime=OLD)
    tree.ensure_file(root, "etc/dpkg/dpkg.cfg", b"no-debsig\n", ctime=OLD)
    tree.ensure_file(root, "var/lib/dpkg/status", b"Package: base-files\n", ctime=OLD)
    # unit files as Cowrie's pickle has them: their size and no contents
    for rel, size in (("ssh.service", 538), ("ssh.socket", 196), ("e2scrub_reap.service", 553)):
        _data_file(root, f"usr/lib/systemd/system/{rel}", size)
    _link(root, "etc/systemd/system/sshd.service", "usr/lib/systemd/system/ssh.service")
    tree.ensure_file(root, "etc/ssh/sshd_config", b"Include /etc/ssh/sshd_config.d/*.conf\n", ctime=OLD)
    tree.ensure_file(root, "proc/modules", b"nft_chain_nat 12288 0 - Live 0x0000000000000000\n", ctime=OLD)
    tree.ensure_file(root, ".dockerenv", b"", mode=0o755, ctime=OLD)
    tree.ensure_file(root, "usr/lib/x86_64-linux-gnu/libc.so.6", b"", ctime=OLD)
    tree.ensure_file(root, "usr/lib/systemd/systemd", b"", mode=0o755, ctime=OLD)
    tree.ensure_file(root, "usr/lib/os-release", b"ID=debian\n", ctime=OLD)
    tree.ensure_file(root, "usr/lib/python3.11/os.py", b"", ctime=OLD)
    tree.ensure_file(root, "usr/lib/python3.11/__pycache__/os.cpython-311.pyc", b"", ctime=OLD)
    tree.ensure_file(root, "usr/share/perl5/strict.pm", b"", ctime=OLD)
    _link(root, "etc/os-release", "usr/lib/os-release")
    tree.ensure_file(root, "usr/share/zoneinfo/Etc/UTC", b"TZif2", ctime=OLD)
    _link(root, "etc/localtime", "usr/share/zoneinfo/Etc/UTC")
    tree.ensure_file(root, "etc/dpkg/origins/debian", b"Vendor: Debian\n", ctime=OLD)
    _link(root, "etc/dpkg/origins/default", "etc/dpkg/origins/debian")
    # tar's rmt as Cowrie's tree has it: through dpkg's alternatives
    _link(root, "etc/rmt", "usr/sbin/rmt-tar")
    _link(root, "usr/sbin/rmt", "usr/sbin/rmt-tar")
    _link(root, "etc/alternatives/rmt", "usr/sbin/rmt-tar")
    tree.ensure_file(root, "usr/share/man/man8/rmt-tar.8.gz", b"\x1f\x8b", ctime=OLD)
    _link(root, "usr/share/man/man8/rmt.8.gz", "usr/share/man/man8/rmt-tar.8.gz")
    _link(root, "etc/alternatives/rmt.8.gz", "usr/share/man/man8/rmt-tar.8.gz")
    # Debian's own packages (adduser, debianutils) and the process of the container
    tree.ensure_file(root, "etc/adduser.conf", b"DSHELL=/bin/bash\n", ctime=OLD)
    tree.ensure_file(root, "etc/rcS.d/README", b"", ctime=OLD)
    for name in ("hwclock.sh", "procps", "ssh"):
        tree.ensure_file(root, f"etc/init.d/{name}", b"#!/bin/sh\n", mode=0o755, ctime=OLD)
    # their run level links as Cowrie's pickle has them (targets from the root)
    for level, name in (("S", "S01hwclock.sh"), ("0", "K01hwclock.sh"), ("6", "K01hwclock.sh"), ("S", "S01procps"),
                        ("2", "S01ssh"), ("3", "S01ssh"), ("4", "S01ssh"), ("5", "S01ssh")):
        _link(root, f"etc/rc{level}.d/{name}", f"etc/init.d/{name[3:]}")
    tree.ensure_file(root, "usr/bin/which.debianutils", b"#! /bin/sh\n", mode=0o755, ctime=OLD)
    _link(root, "usr/bin/which", "usr/bin/which.debianutils")
    tree.ensure_file(root, "proc/969/status", b"Name:\tpython3\n", ctime=OLD)
    _link(root, "proc/self", "proc/969")
    _link(root, "proc/969/exe", "usr/bin/python3.11")
    _link(root, "dev/stdout", "proc/969/fd/pipe:[7685]")
    _link(root, "proc/969/cwd", "")
    tree.ensure_dir(root, "proc/1", ctime=OLD)
    _link(root, "proc/1/root", "")
    _link(root, "usr/lib64/ld-linux-x86-64.so.2", "usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2")
    tree.ensure_file(root, "usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2", b"", mode=0o755, ctime=OLD)
    for rel, size in (("usr/bin/apt", 18824), ("usr/bin/apt-get", 51592), ("usr/bin/dpkg", 318816),
                      ("usr/bin/top", 134736), ("usr/bin/df", 105824), ("usr/bin/uname", 43888),
                      ("usr/bin/perl", 3804432), ("usr/bin/python3.11", 6831736), ("usr/sbin/sshd", 1265432),
                      ("usr/bin/bash", 1265648), ("usr/sbin/useradd", 159536), ("usr/sbin/adduser", 48382),
                      ("usr/bin/wget", 470384), ("usr/sbin/installkernel", 2659),
                      ("usr/sbin/rmt-tar", 60432), ("usr/sbin/e2scrub_all", 5442),
                      # the programs of the packages every server has installed, as Cowrie's tree has them
                      ("usr/bin/curl", 280800), ("usr/bin/gzip", 98136), ("usr/bin/tar", 531984),
                      ("usr/bin/ssh", 1125408), ("usr/bin/vim.basic", 3800000)):
        _program(root, rel, size)
    _link(root, "usr/bin/python3", "usr/bin/python3.11")
    _link(root, "usr/bin/vim", "usr/bin/vim.basic")
    return root


def tmpdir(test):
    handle = tempfile.TemporaryDirectory()
    test.addCleanup(handle.cleanup)
    return Path(handle.name)


# opcodes a pickle of plain lists, strings, numbers and bytes needs; anything that names a class or
# calls one is refused before loading
_DATA_OPCODES = frozenset({"PROTO", "FRAME", "STOP", "MARK", "POP", "POP_MARK", "DUP", "EMPTY_LIST", "APPEND",
                           "APPENDS", "LIST", "EMPTY_TUPLE", "TUPLE", "TUPLE1", "TUPLE2", "TUPLE3", "EMPTY_DICT",
                           "DICT", "SETITEM", "SETITEMS", "NONE", "NEWTRUE", "NEWFALSE", "INT", "BININT",
                           "BININT1", "BININT2", "LONG", "LONG1", "LONG4", "FLOAT", "BINFLOAT", "STRING",
                           "BINSTRING", "SHORT_BINSTRING", "UNICODE", "BINUNICODE", "SHORT_BINUNICODE",
                           "BINUNICODE8", "BINBYTES", "SHORT_BINBYTES", "BINBYTES8", "PUT", "BINPUT",
                           "LONG_BINPUT", "GET", "BINGET", "LONG_BINGET", "MEMOIZE"})


class _DataUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        raise pickle.UnpicklingError(f"refused: {module}.{name}")


def load_data_pickle(path):
    """A pickle of data only (Cowrie's upstream fs.pickle): the opcodes are checked first, then it
    is loaded with an Unpickler that names no class."""
    data = Path(path).read_bytes()
    foreign = {op.name for op, _, _ in pickletools.genops(data)} - _DATA_OPCODES
    if foreign:
        raise pickle.UnpicklingError(f"refused opcodes: {sorted(foreign)}")
    try:
        return _DataUnpickler(io.BytesIO(data)).load()
    except UnicodeDecodeError:
        return _DataUnpickler(io.BytesIO(data), encoding="utf-8").load()
