# SPDX-FileCopyrightText: 2009-2014 Upi Tamminen <desaster@gmail.com>
# SPDX-FileCopyrightText: 2014-2026 Michel Oosterhof <michel@oosterhof.net>
#
# SPDX-License-Identifier: BSD-3-Clause

# Excerpt of Cowrie v3.1.1 (c17c9b73): the imports, the class line, the shell name of __init__,
# error_prefix, showPrompt and command_not_found_message, the text
# patch_cowrie_persona_support.py touches.

from __future__ import annotations

import copy
import enum
import fnmatch
import posixpath
import re

from cowrie.core.config import CowrieConfig


class HoneyPotShell:

    def __init__(self, protocol, interactive=True):
        launcher = None
        # How this shell names itself in the errors it reports, as bash does
        # from $0: "-bash" for the login shell, the name as typed for bash -c
        # or a script (the launching command sets it). A non-interactive shell
        # adds the line it is running (current_line, counted from line_base),
        # and under -c its syntax errors also say "-c". A child shell -- a
        # subshell, pipeline stage or substitution -- keeps its parent's,
        # counting from the parent's current line.
        if isinstance(launcher, HoneyPotShell):
            self.name: str = launcher.name
            self.line_numbers: bool = launcher.line_numbers
            self.syntax_label: str | None = launcher.syntax_label
            self.line_base: int = launcher.current_line - 1
        else:
            self.name = "-bash" if interactive else "bash"
            self.line_numbers = not interactive
            self.syntax_label = None if interactive else "-c"
            self.line_base = 0

    def error_prefix(self) -> str:
        """The prefix bash puts on an error it reports: its name and, for a
        non-interactive shell, the line being run."""
        if self.line_numbers:
            return f"{self.name}: line {self.current_line}: "
        return f"{self.name}: "

    def command_not_found_message(self, cmd: str) -> str:
        """
        Build the error a real shell prints when a command cannot be run.
        For a path-like command (one starting with "." or "/") match bash's
        errno-based messages: an existing directory yields "Is a directory"
        (EISDIR) and a path that does not exist yields "No such file or
        directory" (ENOENT). Anything else yields "command not found".
        """
        if cmd[:1] in (".", "/"):
            path = self.protocol.fs.resolve_path(cmd, self.cwd)
            if self.protocol.fs.isdir(path):
                return f"{self.error_prefix()}{cmd}: Is a directory\n"
            if not self.protocol.fs.exists(path):
                return f"{self.error_prefix()}{cmd}: No such file or directory\n"
        return f"{self.error_prefix()}{cmd}: command not found\n"

    def showPrompt(self) -> None:
        if not self.interactive:
            return

        prompt = ""
        if CowrieConfig.has_option("honeypot", "prompt"):
            prompt = CowrieConfig.get("honeypot", "prompt")
            prompt += " "
        else:
            username = self.user["username"]
            uid = self.user["uid"]
            home = self.user["home"]

            cwd = self.cwd
            homelen = len(home)
            if cwd == home:
                cwd = "~"
            elif len(cwd) > (homelen + 1) and cwd[: (homelen + 1)] == home + "/":
                cwd = "~" + cwd[homelen:]

            # Example: [root@svr03 ~]#   (More of a "CentOS" feel)
            # Example: root@svr03:~#     (More of a "Debian" feel)
            prompt = f"{username}@{self.protocol.hostname}:{cwd}"
            if not uid:
                prompt += "# "  # "Root" user
            else:
                prompt += "$ "  # "Non-Root" user

        # The username is attacker input from SSH/Telnet auth and is decoded
        # with errors="replace" rather than restricted to ASCII, so the prompt
        # goes to the terminal as UTF-8, the way a real shell writes it.
        self.protocol.terminal.write(prompt.encode("utf-8"))
        self.protocol.ps = (prompt.encode("utf-8"), b"> ")
