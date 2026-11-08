"""T-Pot JSON output: whole records, safe alongside the check's external appends."""

import json
import os

from core import output
from core.config import CONFIG
from core.logfile import HoneypotDailyLogFile
from core.tools import mkdir


class AppendDailyLogFile(HoneypotDailyLogFile):
    def _openFile(self):
        super()._openFile()
        # Set append mode when opening, including on shared filesystems.
        self._file.close()
        self._file = open(self.path, "ab", buffering=0)


class Output(output.Output):
    def start(self):
        self.epoch_timestamp = CONFIG.getboolean("output_jsonlog", "epoch_timestamp", fallback=False)
        filename = CONFIG.get("output_jsonlog", "logfile")
        mkdir(os.path.dirname(filename))
        self.outfile = AppendDailyLogFile(os.path.basename(filename), os.path.dirname(filename),
                                         defaultMode=0o664)

    def stop(self):
        self.outfile.flush()

    def write(self, event):
        record = dict(event)
        if not self.epoch_timestamp:
            record.pop("unixtime", None)
        self.outfile.write(json.dumps(record, separators=(",", ":")) + os.linesep)
        self.outfile.flush()
