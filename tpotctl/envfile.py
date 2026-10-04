"""Read and change ~/tpotce/.env without touching anything else in it.

Both forms of env.example are kept as they are: KEY=value and the KEY: "value" of
the LLM blocks. A changed key keeps its form and its quotes, comments, order, the
"NEVER MAKE CHANGES" ruler, the owner and the mode of the file stay. Standard
library only.
"""

import os
import re
import tempfile
from dataclasses import dataclass
from typing import Dict, List, Optional

_LINE_RE = re.compile(r"^(?P<lead>\s*)(?P<key>[A-Za-z_][A-Za-z0-9_]*)(?P<sep>\s*=\s*|:\s+)(?P<raw>.*)$")
_RULER = "# NEVER MAKE CHANGES TO THIS SECTION"


class EnvError(Exception):
    """A value or a file that cannot be written."""


@dataclass
class Entry:
    key: str
    value: str
    index: int       # line number in the file
    colon: bool      # KEY: "value" instead of KEY=value
    quote: str       # '', '"' or "'"


def _unquote(raw: str):
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        inner = raw[1:-1]
        if raw[0] == '"':
            inner = inner.replace('\\"', '"').replace("\\\\", "\\")
        return inner, raw[0]
    # an unquoted value ends where a comment starts, as for docker compose
    return re.split(r"\s+#", raw, 1)[0].strip(), ""


def _quote(value: str, quote: str) -> str:
    if quote == "'" and "'" not in value:
        return f"'{value}'"
    if quote or re.search(r"\s#|^['\"]|['\"]$", value):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def check_value(value: str) -> None:
    if any(c in value for c in "\n\r\0"):
        raise EnvError("a value cannot contain line breaks")


class EnvFile:

    def __init__(self, path: str):
        self.path = path
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.trailing_newline = text.endswith("\n")
        self.lines: List[str] = text.split("\n")
        if self.trailing_newline:
            self.lines.pop()

    def entries(self) -> Dict[str, Entry]:
        found: Dict[str, Entry] = {}
        for index, line in enumerate(self.lines):
            if line.lstrip().startswith("#"):
                continue
            match = _LINE_RE.match(line)
            if not match:
                continue
            value, quote = _unquote(match.group("raw"))
            found[match.group("key")] = Entry(match.group("key"), value, index,
                                              not match.group("sep").strip().startswith("="), quote)
        return found

    def values(self) -> Dict[str, str]:
        return {key: entry.value for key, entry in self.entries().items()}

    def get(self, key: str) -> Optional[str]:
        entry = self.entries().get(key)
        return entry.value if entry else None

    def set(self, key: str, value: str, system: bool = False) -> None:
        """Change a key in place, or add it: user keys above the ruler, system keys at the end."""
        check_value(value)
        entry = self.entries().get(key)
        if entry:
            lead = re.match(r"^\s*", self.lines[entry.index]).group(0)
            if entry.colon:
                self.lines[entry.index] = f'{lead}{key}: {_quote(value, entry.quote or chr(34))}'
            else:
                self.lines[entry.index] = f"{lead}{key}={_quote(value, entry.quote)}"
            return
        line = f"{key}={_quote(value, '')}"
        ruler = next((i for i, text in enumerate(self.lines) if text.startswith(_RULER)), None)
        if system or ruler is None:
            self.lines.append(line)
            return
        # in front of the block of '#' lines around the ruler, after a blank line
        at = ruler
        while at > 0 and self.lines[at - 1].startswith("#"):
            at -= 1
        while at > 0 and not self.lines[at - 1].strip():
            at -= 1
        self.lines[at:at] = ["", line]

    def text(self) -> str:
        return "\n".join(self.lines) + ("\n" if self.trailing_newline else "")

    def save(self) -> None:
        """Atomically, with the owner and mode of the old file (it holds credentials)."""
        path = os.path.realpath(self.path)   # a linked .env is written where it really is
        try:
            stat = os.stat(path)
        except OSError as err:
            raise EnvError(f"cannot read {self.path}: {err}")
        directory = os.path.dirname(path)
        handle, temp = tempfile.mkstemp(prefix=".env-", dir=directory)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                out.write(self.text())
            os.chmod(temp, stat.st_mode & 0o7777)
            if hasattr(os, "chown") and (stat.st_uid, stat.st_gid) != (os.getuid(), os.getgid()):
                try:
                    os.chown(temp, stat.st_uid, stat.st_gid)
                except PermissionError:
                    raise EnvError(f"{self.path} belongs to another user, change it as that user")
            os.replace(temp, path)
        except OSError as err:
            raise EnvError(f"cannot write {self.path}: {err}")
        finally:
            if os.path.exists(temp):
                os.unlink(temp)


def read_values(path: str) -> Dict[str, str]:
    try:
        return EnvFile(path).values()
    except OSError:
        return {}
