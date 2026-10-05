"""Run a script or a tpot command as a child and hand its output on line by line.

stdin is closed (a script that asks would hang otherwise) and stderr goes with
stdout, so the log shows both in order. The tests replace it with a fake.
"""

import subprocess
from typing import Callable, Dict, List, Optional


class Engine:

    def __init__(self, command: List[str], env: Optional[Dict[str, str]] = None, cwd: Optional[str] = None):
        self.command = command
        self.env = env
        self.cwd = cwd

    def run(self, line: Callable[[str], None]) -> int:
        try:
            proc = subprocess.Popen(self.command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, universal_newlines=True, bufsize=1,
                                    env=self.env, cwd=self.cwd)
        except OSError as err:
            line(f"{self.command[0]}: {err.strerror or err}\n")
            return 127
        for text in proc.stdout:
            line(text)
        return proc.wait()
