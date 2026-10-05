"""The @@tpot marks the T-Pot scripts print for tpot (fuMARK of installer/lib/ui.sh).

`@@tpot phase <key> [title ...]` starts a phase, `@@tpot warn <key> <text ...>` is a
warning to show at the end; installer.Progress reads its own marks (tasks, images)
on top. Every other line is output of the script and kept for the log view.
"""

from collections import deque
from typing import List, Optional, Tuple

PREFIX = "@@tpot "


def parse_mark(line: str) -> Optional[Tuple[str, List[str]]]:
    if not line.startswith(PREFIX):
        return None
    words = line[len(PREFIX):].split()
    if not words:
        return None
    return words[0], words[1:]


class Run:
    """What a script said so far: its phases in order, warnings and the last lines."""

    def __init__(self, keep: int = 2000):
        self.phases: List[Tuple[str, str]] = []
        self.phase = ""
        self.warnings: List[str] = []
        self.lines: deque = deque(maxlen=keep)

    def feed(self, line: str) -> None:
        line = line.rstrip("\n")
        mark = parse_mark(line)
        if mark is None:
            self.lines.append(line)
            return
        what, words = mark
        if what == "phase" and words:
            self.phase = words[0]
            if words[0] not in (key for key, _title in self.phases):
                self.phases.append((words[0], " ".join(words[1:]) or words[0]))
        elif what == "warn" and len(words) > 1:
            self.warnings.append(" ".join(words[1:]))
