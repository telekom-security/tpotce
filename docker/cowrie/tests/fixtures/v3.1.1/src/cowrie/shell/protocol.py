# SPDX-FileCopyrightText: 2009-2014 Upi Tamminen <desaster@gmail.com>
# SPDX-FileCopyrightText: 2014-2026 Michel Oosterhof <michel@oosterhof.net>
#
# SPDX-License-Identifier: BSD-3-Clause

from __future__ import annotations

import functools
import random
import socket
import time
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from twisted.conch import recvline
from twisted.conch.insults import insults
from twisted.internet.protocol import connectionDone
from twisted.logger import Logger
from twisted.protocols.policies import TimeoutMixin

import cowrie.commands
from cowrie.core.config import CowrieConfig
from cowrie.core.resources import read_data_bytes
from cowrie.shell import command, fs, honeypot

# Excerpt of Cowrie v3.1.1 (c17c9b73), the text patch_cowrie_persona_support.py touches.

    def getCommand(self, cmd, paths, cwd):
        if not cmd.strip():
            return None
        path = None
        if cmd in self.commands:
            return self.commands[cmd]
        if cmd[0] in (".", "/"):
            path = self.fs.resolve_path(cmd, cwd)
            if not self.fs.exists(path):
                return None
        else:
            for i in [f"{self.fs.resolve_path(x, cwd)}/{cmd}" for x in paths if x]:
                if self.fs.exists(i):
                    path = i
                    break

        if path is None:
            return None

        relpath = path.lstrip("/")
        txtcmds_path = CowrieConfig.get("honeypot", "txtcmds_path", fallback="")
        if txtcmds_path:
            operator_path = Path(txtcmds_path) / relpath
            if operator_path.is_file():
                return self.txtcmd(operator_path.read_bytes())

        try:
            binary_data = read_data_bytes("txtcmds", *relpath.split("/"))
            return self.txtcmd(binary_data)
        except FileNotFoundError:
            # No txtcmd file at this path -- including when the command resolves
            # to a directory (e.g. `/` -> empty relpath -> the txtcmds directory
            # itself), which read_data_bytes reports as FileNotFoundError on
            # every platform. Fall through so the caller reports "Is a
            # directory" instead of crashing the session.
            pass

        if path in self.commands:
            return self.commands[path]

        if self.fs.isfile(path):
            # bash names a command typed with a slash as typed, and one found
            # through PATH by its full path.
            return self.scriptcmd(path, cmd if "/" in cmd else path)

        self._log.info("Can't find command {cmd}", cmd=cmd)
        return None
