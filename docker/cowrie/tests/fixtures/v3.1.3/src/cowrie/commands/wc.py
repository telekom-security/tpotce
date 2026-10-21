# SPDX-FileCopyrightText: 2019 NunoNovais <nuno@novais.me>
# SPDX-FileCopyrightText: 2018 Free Software Foundation, Inc.\n")
# SPDX-FileCopyrightText: 2019 Nuno Novais <nuno@noais.me>
# SPDX-FileCopyrightText: 2020-2025 Michel Oosterhof <michel@oosterhof.net>
#
# SPDX-License-Identifier: BSD-3-Clause

"""
This module contains the wc commnad
"""

from __future__ import annotations

import getopt
import re

from cowrie.shell.command import HoneyPotCommand

commands = {}


class Command_wc(HoneyPotCommand):
    """
    wc command
    """

    consumes_stdin = True

    def version(self) -> None:
        self.writeBytes(b"wc (GNU coreutils) 8.30\n")
        self.writeBytes(b"Copyright (C) 2018 Free Software Foundation, Inc.\n")
        self.writeBytes(
            b"License GPLv3+: GNU GPL version 3 or later <https://gnu.org/licenses/gpl.html>.\n"
        )
        self.writeBytes(
            b"This is free software: you are free to change and redistribute it.\n"
        )
        self.writeBytes(b"There is NO WARRANTY, to the extent permitted by law.\n")
        self.writeBytes(b"\n")
        self.writeBytes(b"Written by Paul Rubin and David MacKenzie.\n")

    def help(self) -> None:
        self.writeBytes(b"Usage: wc [OPTION]... [FILE]...\n")
        self.writeBytes(
            b"Print newline, word, and byte counts for each FILE, and a total line if\n"
        )
        self.writeBytes(
            b"more than one FILE is specified.  A word is a non-zero-length sequence of\n"
        )
        self.writeBytes(b"characters delimited by white space.\n")
        self.writeBytes(b"\n")
        self.writeBytes(b"With no FILE, or when FILE is -, read standard input.\n")
        self.writeBytes(b"\n")
        self.writeBytes(
            b"The options below may be used to select which counts are printed, always in\n"
        )
        self.writeBytes(
            b"the following order: newline, word, character, byte, maximum line length.\n"
        )
        self.writeBytes(b"\t-c\tprint the byte counts\n")
        self.writeBytes(b"\t-m\tprint the character counts\n")
        self.writeBytes(b"\t-l\tprint the newline counts\n")
        self.writeBytes(b"\t-w\tprint the word counts\n")
        self.writeBytes(b"\t-h\tdisplay this help and exit\n")
        self.writeBytes(b"\t-v\toutput version information and exit\n")

    def wc_get_contents(self, filename: str, optlist: list[tuple[str, str]]) -> None:
        try:
            contents = self.fs.file_contents(filename)
            self.wc_application(contents, optlist)
        except Exception:
            self.errorWrite(f"wc: {filename}: No such file or directory\n")

    def wc_application(self, contents: bytes, optlist: list[tuple[str, str]]) -> None:
        for opt, _arg in optlist:
            if opt == "-l":
                contentsplit = contents.split(b"\n")
                self.write(f"{len(contentsplit) - 1}\n")
            elif opt == "-w":
                contentsplit = re.sub(b" +", b" ", contents.strip(b"\n").strip()).split(
                    b" "
                )
                self.write(f"{len(contentsplit)}\n")
            elif opt == "-m" or opt == "-c":
                self.write(f"{len(contents)}\n")
            elif opt == "-v":
                self.version()
            else:
                self.help()

    def start(self) -> None:
        if not self.args:
            self.exit()
            return

        optlist: list[tuple[str, str]] = []
        args = self.args
        if self.args[0] == ">":
            pass
        else:
            try:
                optlist, args = getopt.getopt(self.args, "cmlLwhv")
            except getopt.GetoptError as err:
                self.errorWrite(f"wc: invalid option -- {err.opt}\n")
                self.help()
                self.exit()
                return
            for opt in optlist:
                if opt[0] == "-v":
                    self.version()
                    self.exit()
                    return
                if opt[0] == "-h":
                    self.help()
                    self.exit()
                    return

        if not self.input_data:
            files = self.check_arguments("wc", args[1:])
            for pname in files:
                self.wc_get_contents(pname, optlist)
        else:
            self.wc_application(self.input_data, optlist)

        self.exit()

    def lineReceived(self, line: str) -> None:
        self.protocol.events.dispatch(
            "cowrie.command.input",
            "INPUT (%(realm)s): %(input)s",
            realm="wc",
            input=line,
        )

    def eofReceived(self) -> None:
        self.exit()


commands["/usr/bin/wc"] = Command_wc
commands["/bin/wc"] = Command_wc
commands["wc"] = Command_wc
