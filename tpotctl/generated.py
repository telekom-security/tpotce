"""Source fingerprints for runtime configuration generated from this checkout."""

import hashlib
import os
import re
from typing import Iterable

STAMP_PREFIX = "# T-Pot sources sha256: "
_STAMP = re.compile(r"^# T-Pot sources sha256: ([0-9a-f]{64})\r?$")


class GeneratedError(Exception):
    """The sources or stamp of generated runtime configuration are invalid."""


def source_fingerprint(root: str, sources: Iterable[str], selection: str = "") -> str:
    """Hash named source files and a configuration selection, independent of input order."""
    digest = hashlib.sha256()
    root = os.path.realpath(root)
    paths = sorted(set(sources))
    if not paths:
        raise GeneratedError("at least one source file is required")
    for relative in paths:
        if os.path.isabs(relative) or relative in ("", ".", "..") or relative.startswith(".." + os.sep):
            raise GeneratedError(f"source path must be relative to the checkout: {relative}")
        path = os.path.realpath(os.path.join(root, relative))
        if os.path.commonpath((root, path)) != root:
            raise GeneratedError(f"source path leaves the checkout: {relative}")
        try:
            with open(path, "rb") as source:
                content = source.read()
        except OSError as err:
            raise GeneratedError(f"cannot read source {relative}: {err}") from err
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    digest.update(b"selection\0")
    digest.update(selection.encode("utf-8"))
    return digest.hexdigest()


def stamp_text(text: str, fingerprint: str) -> str:
    """Add or replace the source marker in the leading comment block."""
    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise GeneratedError("source fingerprint must be a SHA-256 hex digest")
    lines = text.splitlines(keepends=True)
    lines = [line for line in lines if not _STAMP.match(line.rstrip("\n"))]
    newline = "\r\n" if "\r\n" in text else "\n"
    marker = f"{STAMP_PREFIX}{fingerprint}{newline}"
    index = 0
    while index < len(lines) and (not lines[index].strip() or lines[index].lstrip().startswith("#")):
        index += 1
    lines.insert(index, marker)
    if not lines:
        return marker
    return "".join(lines)


def has_fingerprint(text: str, fingerprint: str) -> bool:
    """Whether exactly one source marker names the expected fingerprint."""
    found = [match.group(1) for line in text.splitlines() if (match := _STAMP.match(line))]
    return found == [fingerprint]
