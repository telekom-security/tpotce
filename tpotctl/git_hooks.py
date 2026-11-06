"""Install source-drift warnings into this checkout's Git hooks."""

import argparse
import os
import shlex
import subprocess
import sys
import tempfile

BEGIN = "# >>> tpot config freshness >>>"
END = "# <<< tpot config freshness <<<"


class GitHookError(Exception):
    """A Git hook could not be updated without replacing user-owned content."""


def _hooks_dir(root: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", root, "rev-parse", "--git-path", "hooks"],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except (OSError, subprocess.CalledProcessError) as err:
        raise GitHookError(f"cannot find Git hooks for {root}: {err}") from err
    path = result.stdout.strip()
    if not path:
        raise GitHookError(f"Git returned no hooks directory for {root}")
    path = os.path.realpath(path if os.path.isabs(path) else os.path.join(root, path))
    if os.path.commonpath((root, path)) != root:
        raise GitHookError(f"Git hooks are shared outside this checkout: {path}; they were left unchanged")
    return path


def _block(root: str) -> str:
    generated = os.path.join(root, "tpotctl", "generated.py")
    return (
        f"{BEGIN}\n"
        'if [ "${TPOT_UPDATE_ACTIVE:-}" != "1" ]; then\n'
        f"  python3 {shlex.quote(generated)} check --root {shlex.quote(root)} || true\n"
        "fi\n"
        f"{END}\n"
    )


def _write_hook(path: str, root: str) -> None:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        mode, owner = 0o755, None
    else:
        if not os.path.isfile(path) or os.path.islink(path):
            raise GitHookError(f"{path} is not a regular Git hook; it was left unchanged")
        mode = info.st_mode & 0o777
        if not mode & 0o111:
            raise GitHookError(f"{path} is not executable; it was left unchanged")
        owner = (info.st_uid, info.st_gid)
        try:
            with open(path, encoding="utf-8", newline="") as handle:
                old_text = handle.read()
        except (OSError, UnicodeDecodeError) as err:
            raise GitHookError(f"cannot read {path}: {err}") from err
        if not (old_text.startswith(f"#!/bin/sh\n{BEGIN}\n") and old_text.endswith(f"{END}\n")
                and old_text.count(BEGIN) == old_text.count(END) == 1):
            raise GitHookError(f"{path} is a hook of your own; it was left unchanged")
    text = "#!/bin/sh\n" + _block(root)
    directory = os.path.dirname(path)
    fd, temporary = tempfile.mkstemp(prefix=".tpot-hook-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        if owner and owner != (os.getuid(), os.getgid()):
            os.chown(temporary, *owner)
        os.replace(temporary, path)
    except OSError as err:
        raise GitHookError(f"cannot update {path}: {err}") from err
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def install(root: str, hooks_dir: str = "") -> None:
    root = os.path.realpath(root)
    hooks_dir = hooks_dir or _hooks_dir(root)
    if not os.path.isdir(hooks_dir):
        raise GitHookError(f"Git hooks directory does not exist: {hooks_dir}")
    for name in ("post-merge", "post-checkout"):
        _write_hook(os.path.join(hooks_dir, name), root)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Install T-Pot Git source-drift warnings.")
    commands = parser.add_subparsers(dest="command", required=True)
    install_parser = commands.add_parser("install", help="install post-merge and post-checkout warnings")
    install_parser.add_argument("--root", required=True)
    args = parser.parse_args(argv)
    try:
        install(args.root)
    except GitHookError as err:
        print(f"git hooks: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
