"""Shared helpers for the tpot_personas tests (stdlib only)."""
import sys
import tempfile
from pathlib import Path

DIST = Path(__file__).resolve().parents[1] / "dist"
if str(DIST) not in sys.path:
    sys.path.insert(0, str(DIST))

from tpot_personas import tree  # noqa: E402


def base_tree():
    """A tiny stand-in for Cowrie's fs.pickle: root dir with the parts the
    renderer touches, a home/phil to be removed and an old ctime."""
    root = ["", tree.T_DIR, 0, 0, 4096, 0o40755, 1_300_000_000, [], None, None]
    for rel in ("bin", "etc", "home/phil", "usr/bin", "usr/sbin", "var/log", "proc"):
        tree.ensure_dir(root, rel, ctime=1_300_000_000)
    tree.ensure_file(root, "etc/motd", b"phil was here\n", ctime=1_300_000_000)
    tree.ensure_file(root, "bin/apt", b"", mode=0o755, ctime=1_300_000_000)
    return root


def tmpdir(test):
    handle = tempfile.TemporaryDirectory()
    test.addCleanup(handle.cleanup)
    return Path(handle.name)
