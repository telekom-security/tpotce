# SPDX-FileCopyrightText: 2017-2023 Michel Oosterhof <michel@oosterhof.net>
#
# SPDX-License-Identifier: BSD-3-Clause

"""
dd commands
"""

from __future__ import annotations

import re

from cowrie.shell.command import HoneyPotCommand
from cowrie.shell.fs import FileNotFound

commands = {}


class Command_dd(HoneyPotCommand):
    """
    dd command
    """

    consumes_stdin = True

    ddargs: dict[str, str]

    def start(self) -> None:
        if not self.args or self.args[0] == ">":
            return

        self.ddargs = {}

        for arg in self.args:
            if arg.find("=") == -1:
                self.write(f"unknown operand: {arg}")
                self._finish(success=False)
            operand, value = arg.split("=")
            if operand not in ("if", "bs", "of", "count"):
                self.write(f"unknown operand: {operand}")
                self._finish(success=False)
            self.ddargs[operand] = value

        if self.input_data:
            self.writeBytes(self.input_data)
        else:
            bSuccess = True
            c = -1
            block = 512
            if "if" in self.ddargs:
                iname = self.ddargs["if"]
                pname = self.fs.resolve_path(iname, self.cwd)
                if self.fs.isdir(pname):
                    self.errorWrite(f"dd: {iname}: Is a directory\n")
                    bSuccess = False

                if bSuccess:
                    if "bs" in self.ddargs:
                        block = parse_size(self.ddargs["bs"])
                        if block <= 0:
                            self.errorWrite(f"dd: invalid number '{block}'\n")
                            bSuccess = False

                if bSuccess:
                    if "count" in self.ddargs:
                        c = int(self.ddargs["count"])
                        if c < 0:
                            self.errorWrite(f"dd: invalid number '{c}'\n")
                            bSuccess = False

                if bSuccess:
                    try:
                        contents = self.fs.file_contents(pname)
                        if c == -1:
                            self.writeBytes(contents)
                        else:
                            tsize = block * c
                            data = contents
                            if len(data) > tsize:
                                self.writeBytes(data[:tsize])
                            else:
                                self.writeBytes(data)
                    except FileNotFound:
                        self.errorWrite(f"dd: {iname}: No such file or directory\n")
                        bSuccess = False

                self._finish(success=bSuccess)

    def _finish(self, success: bool = True) -> None:
        if success:
            # Real dd writes its transfer statistics to stderr, not stdout, so
            # they do not pollute a command substitution that captures stdout.
            self.errorWrite("0+0 records in\n")
            self.errorWrite("0+0 records out\n")
            self.errorWrite("0 bytes transferred in 0.695821 secs (0 bytes/sec)\n")
            self.exit(0)
        else:
            self.exit(1)

    def lineReceived(self, line: str) -> None:
        self.protocol.events.dispatch(
            "cowrie.session.input",
            "INPUT (%(realm)s): %(input)s",
            realm="dd",
            input=line,
        )

    def eofReceived(self) -> None:
        self._finish()


def parse_size(param: str) -> int:
    """
    Parse dd arguments that indicate block sizes
    Return 0 in case of illegal input
    """
    pattern = r"^(\d+)(c|w|b|kB|K|MB|M|xM|GB|G|T|TB|P|PB|E|EB|Z|ZB|Y|YB)?$"
    z = re.search(pattern, param)
    if not z:
        return 0
    digits = int(z.group(1))
    letters = z.group(2)

    if not letters:
        multiplier = 1
    elif letters == "c":
        multiplier = 1
    elif letters == "w":
        multiplier = 2
    elif letters == "b":
        multiplier = 512
    elif letters == "kB":
        multiplier = 1000
    elif letters == "K":
        multiplier = 1024
    elif letters == "MB":
        multiplier = 1000 * 1000
    elif letters == "M" or letters == "xM":
        multiplier = 1024 * 1024
    elif letters == "GB":
        multiplier = 1000 * 1000 * 1000
    elif letters == "G":
        multiplier = 1024 * 1024 * 1024
    else:
        multiplier = 1

    return digits * multiplier


commands["/bin/dd"] = Command_dd
commands["dd"] = Command_dd
