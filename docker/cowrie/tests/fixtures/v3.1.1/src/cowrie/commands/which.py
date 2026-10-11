# SPDX-FileCopyrightText: 2013 Bas Stottelaar <basstottelaar [AT] gmail [DOT] com>
# SPDX-FileCopyrightText: 2015-2022 Michel Oosterhof <michel@oosterhof.net>
#
# SPDX-License-Identifier: BSD-3-Clause

from __future__ import annotations

from cowrie.shell.command import HoneyPotCommand

commands = {}


class Command_which(HoneyPotCommand):
    # Do not resolve args
    resolve_args = False

    def call(self) -> None:
        """
        Look up all the arguments on PATH and print each (first) result
        """

        # No arguments, just exit
        if not len(self.args) or "PATH" not in self.environ:
            return

        # Look up each file
        for f in self.args:
            for path in self.environ["PATH"].split(":"):
                resolved = self.fs.resolve_path(f, path)

                if self.fs.exists(resolved):
                    self.write(f"{path}/{f}\n")


commands["which"] = Command_which
