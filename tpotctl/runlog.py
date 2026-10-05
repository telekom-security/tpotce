"""The @@tpot marks the T-Pot scripts print for tpot (fuMARK of installer/lib/ui.sh).

`@@tpot phase <key> [title ...]` starts a phase, `@@tpot warn <key> <text ...>` is a
warning to show at the end, `@@tpot fail <key>` says that phase failed (the run goes
on) and `@@tpot changed checkout` that ~/tpotce changed, so tpot should start anew
even if the run fails later (`@@tpot changed back`: it is the one before the run again); installer.Progress reads its own marks (tasks, images)
on top. Every other line is output of the script and kept for the log view.
"""

from collections import deque
from typing import List, Optional, Set, Tuple

PREFIX = "@@tpot "


def parse_mark(line: str) -> Optional[Tuple[str, List[str]]]:
    if not line.startswith(PREFIX):
        return None
    words = line[len(PREFIX):].split()
    if not words:
        return None
    return words[0], words[1:]


class Run:
    """What a script said so far: its phases in order, the failed ones, warnings and the last lines."""

    def __init__(self, keep: int = 2000):
        self.phases: List[Tuple[str, str]] = []
        self.phase = ""
        self.warnings: List[str] = []
        self.failed: Set[str] = set()
        self.checkout_changed = False
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
        elif what == "fail" and words:
            self.failed.add(words[0])
        elif what == "changed" and words == ["checkout"]:
            self.checkout_changed = True
        elif what == "changed" and words == ["back"]:
            self.checkout_changed = False
