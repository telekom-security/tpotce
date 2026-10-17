"""The honeypot logs a check reads and appends to, below the data folder, never through a link.

The folder is writable for the group tpot, so every name below the data folder is opened with
O_NOFOLLOW through the descriptor of its folder, and only a regular file with one link is
written to. Nothing is created: a log that is not there stays missing (a file root made would
lock the honeypot out of its own log).

The module has no imports of tpotctl, so it also runs as a script, as root through sudo when a
log is not open to the user (`python3 -I logfile.py append|read|size|list DATA REL ...`); the
text of `append` is an argument there (with --become-file the stdin of sudo is the password); the
check uses the same code both ways.
"""

import fnmatch
import os
import stat
import sys
from typing import List, Optional

READ_LIMIT = 4 * 2 ** 20        # bytes of new log text a check reads per file at most


class LogFileError(Exception):
    """A log that cannot be reached safely (a link, no regular file, a name that leaves the folder)."""


def _parts(rel: str) -> List[str]:
    parts = rel.split("/")
    if not rel or rel.startswith("/") or any(part in ("", ".", "..") for part in parts):
        raise LogFileError(f"{rel!r} is no path below the data folder")
    return parts


def _folder(base: str, parts: List[str]) -> int:
    """The descriptor of base/parts..., each name opened without following a link."""
    fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts:
            info = os.lstat(part, dir_fd=fd)
            if not stat.S_ISDIR(info.st_mode):
                raise LogFileError(f"{part} below {base} is no folder (a link?)")
            sub = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = sub
        return fd
    except BaseException:
        os.close(fd)
        raise


def _open(base: str, rel: str, flags: int, write: bool = False) -> int:
    parts = _parts(rel)
    folder = _folder(base, parts[:-1])
    try:
        info = os.lstat(parts[-1], dir_fd=folder)
        if stat.S_ISLNK(info.st_mode):
            raise LogFileError(f"{os.path.join(base, rel)} is a link")
        fd = os.open(parts[-1], flags | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_NOCTTY", 0), dir_fd=folder)
    finally:
        os.close(folder)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or (write and info.st_nlink != 1):
        os.close(fd)
        raise LogFileError(f"{os.path.join(base, rel)} is no regular file of its own")
    return fd


def size(base: str, rel: str) -> Optional[int]:
    """The size of a log, None when it is not there."""
    try:
        fd = _open(base, rel, os.O_RDONLY)
    except FileNotFoundError:
        return None
    try:
        return os.fstat(fd).st_size
    finally:
        os.close(fd)


def read_from(base: str, rel: str, offset: int, limit: int = READ_LIMIT) -> bytes:
    """What a log holds from offset on (from its start when it is shorter now: rotated)."""
    fd = _open(base, rel, os.O_RDONLY)
    try:
        if os.fstat(fd).st_size < offset:
            offset = 0
        os.lseek(fd, offset, os.SEEK_SET)
        chunks, left = [], limit
        while left > 0:
            chunk = os.read(fd, min(left, 65536))
            if not chunk:
                break
            chunks.append(chunk)
            left -= len(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def append(base: str, rel: str, data: bytes) -> None:
    """data at the end of an existing log (one write, O_APPEND)."""
    fd = _open(base, rel, os.O_WRONLY | os.O_APPEND, write=True)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
    finally:
        os.close(fd)


def listing(base: str, rel_dir: str, pattern: str) -> List[str]:
    """The regular files of a folder below base whose names match pattern, sorted ([] without it)."""
    try:
        folder = _folder(base, _parts(rel_dir))
    except FileNotFoundError:
        return []
    try:
        names = []
        for name in sorted(os.listdir(folder)):
            if not fnmatch.fnmatchcase(name, pattern):
                continue
            info = os.lstat(name, dir_fd=folder)
            if stat.S_ISREG(info.st_mode):
                names.append(f"{rel_dir}/{name}")
        return names
    finally:
        os.close(folder)


def main(argv: List[str]) -> int:
    """append BASE REL [TEXT] (else stdin) | read BASE REL OFFSET [LIMIT] | size BASE REL |
    list BASE DIR PATTERN; exit 1 with the reason on stderr."""
    try:
        command, base, rel = argv[0], argv[1], argv[2]
        if command == "append":
            # the text as an argument: with --become-file the stdin of sudo is the password
            append(base, rel, argv[3].encode("utf-8") if len(argv) > 3 else sys.stdin.buffer.read())
        elif command == "read":
            limit = int(argv[4]) if len(argv) > 4 else READ_LIMIT
            sys.stdout.buffer.write(read_from(base, rel, int(argv[3]), limit))
        elif command == "size":
            found = size(base, rel)
            sys.stdout.write("-\n" if found is None else f"{found}\n")
        elif command == "list":
            sys.stdout.write("".join(f"{name}\n" for name in listing(base, rel, argv[3])))
        else:
            raise IndexError(command)
    except (IndexError, ValueError):
        sys.stderr.write("usage: logfile.py append|read|size|list DATA REL ...\n")
        return 2
    except (LogFileError, OSError) as error:
        sys.stderr.write(f"{error}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
