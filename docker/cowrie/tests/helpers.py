"""Shared helpers for the tpot_personas tests (stdlib only)."""
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
    tree.ensure_file(root, "usr/lib/systemd/system/ssh.service", b"[Unit]\n", ctime=OLD)
    tree.ensure_file(root, "usr/lib/systemd/system/ssh.socket", b"[Unit]\n", ctime=OLD)
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
    # Debian's own packages (adduser, debianutils) and the process of the container
    tree.ensure_file(root, "etc/adduser.conf", b"DSHELL=/bin/bash\n", ctime=OLD)
    tree.ensure_file(root, "etc/rcS.d/README", b"", ctime=OLD)
    tree.ensure_file(root, "usr/bin/which.debianutils", b"#! /bin/sh\n", mode=0o755, ctime=OLD)
    _link(root, "usr/bin/which", "usr/bin/which.debianutils")
    tree.ensure_file(root, "proc/969/status", b"Name:\tpython3\n", ctime=OLD)
    _link(root, "proc/self", "proc/969")
    _link(root, "proc/969/exe", "usr/bin/python3.11")
    _link(root, "dev/stdout", "proc/969/fd/pipe:[7685]")
    _link(root, "usr/lib64/ld-linux-x86-64.so.2", "usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2")
    tree.ensure_file(root, "usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2", b"", mode=0o755, ctime=OLD)
    for rel, size in (("usr/bin/apt", 18824), ("usr/bin/apt-get", 51592), ("usr/bin/dpkg", 318816),
                      ("usr/bin/top", 134736), ("usr/bin/df", 105824), ("usr/bin/uname", 43888),
                      ("usr/bin/perl", 3804432), ("usr/bin/python3.11", 6831736), ("usr/sbin/sshd", 1265432),
                      ("usr/bin/bash", 1265648), ("usr/sbin/useradd", 159536), ("usr/sbin/adduser", 48382)):
        _program(root, rel, size)
    _link(root, "usr/bin/python3", "usr/bin/python3.11")
    return root


def tmpdir(test):
    handle = tempfile.TemporaryDirectory()
    test.addCleanup(handle.cleanup)
    return Path(handle.name)
