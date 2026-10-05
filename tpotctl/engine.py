"""Run a script or a tpot command as a child and hand its output on line by line.

stdin is closed (a script that asks would hang otherwise) and stderr goes with
stdout, so the log shows both in order. The child starts anew: the venv guard of
the menu (bootstrap.GUARD) stays out, a tpot it runs goes to the venv on its own. The
tests replace it with a fake.
"""

import os
import subprocess
from typing import Callable, Dict, List, Optional

from tpotctl.bootstrap import GUARD


class Engine:

    def __init__(self, command: List[str], env: Optional[Dict[str, str]] = None, cwd: Optional[str] = None):
        self.command = command
        self.env = dict(os.environ if env is None else env)
        self.env.pop(GUARD, None)
        self.cwd = cwd

    def run(self, line: Callable[[str], None]) -> int:
        try:
            # a log is text, a stray byte (i.e. a banner nmap prints) must not end the run; no
            # controlling terminal: a sudo that needs a password fails at once instead of asking
            # into the menu
            proc = subprocess.Popen(self.command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, universal_newlines=True, bufsize=1,
                                    encoding="utf-8", errors="replace", env=self.env, cwd=self.cwd,
                                    start_new_session=True)
        except OSError as err:
            line(f"{self.command[0]}: {err.strerror or err}\n")
            return 127
        for text in proc.stdout:
            line(text)
        return proc.wait()
