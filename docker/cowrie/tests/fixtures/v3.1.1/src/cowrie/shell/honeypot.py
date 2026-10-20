# SPDX-FileCopyrightText: 2009-2014 Upi Tamminen <desaster@gmail.com>
# SPDX-FileCopyrightText: 2014-2026 Michel Oosterhof <michel@oosterhof.net>
#
# SPDX-License-Identifier: BSD-3-Clause

# Excerpt of Cowrie v3.1.1 (c17c9b73): the imports, the class line, the environment and the
# shell name of __init__, error_prefix, showPrompt, command_not_found_message,
# _write_shell_error and _run_pipeline, the text patch_cowrie_persona_support.py touches.
# The first two lines of __init__ (its def line and "launcher = None") stand in for the
# start of __init__; the rest is verbatim.

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
        if protocol.cmdstack:
            parent = protocol.cmdstack[-1]
            self.environ: dict[str, str] = copy.copy(parent.environ)
            self.exported: set[str] = copy.copy(parent.exported)
        else:
            self.environ = copy.copy(protocol.environ)
            self.exported = set(protocol.environ.keys())
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

    def _write_shell_error(
        self, message: bytes, ops: list[dict[str, Any]] | None = None
    ) -> None:
        """Write an error the shell itself reports to its stderr (fd 2), with
        ``ops`` -- the failing command's own redirections -- applied, so
        `x 2>&1 | cat` and `( x ) 2>/dev/null` reroute it."""
        pp = self._make_pipe(None, [], None, ops)
        pp.errReceived(message)
        for real_path, virtual_path in pp.redirect_real_files:
            self.protocol.terminal.redirFiles.add((real_path, virtual_path))

    def _run_pipeline(self, node: Pipeline) -> None:
        """Run the stages left to right, each in its own child shell, the
        stdout of one buffered as the stdin of the next, as a shell forks one
        process per stage. The first stage reads what this shell reads and
        the last writes where this shell writes; the pipeline's status is the
        last stage's, as in bash without pipefail.

        Stages that complete synchronously are driven by the loop here rather
        than by nested callbacks, so a long pipeline does not grow the Python
        stack by one frame per stage (issue #40352).
        """
        stages = node.stages
        output: bytes | None = None

        def stage_finished(child: HoneyPotShell) -> None:
            nonlocal output
            output = child.captured
            self.last_exit_code = child.last_exit_code

        def continue_from(_child: None, index: int) -> None:
            run_from(index)

        def run_from(index: int) -> None:
            while index < len(stages):
                last = index == len(stages) - 1
                if last:
                    # The last stage writes where this shell writes (terminal,
                    # a file, or the enclosing capture sink).
                    fds, buf = dict(self.fds), self._capture
                else:
                    # An intermediate stage's stdout is captured to feed the
                    # next; its stderr still reaches this shell.
                    fds, buf = self._capture_fds()
                d = self.run_child(
                    [stages[index]],
                    fds=fds,
                    capture_buf=buf,
                    stdin=output,
                    inherits_stdin=index == 0,
                )
                index += 1
                finished = d.addCallback(stage_finished)
                if not d.called:
                    # The stage paused on a Deferred; carry on when it ends.
                    finished.addCallback(continue_from, index)
                    return
            self._advance()

        run_from(0)
