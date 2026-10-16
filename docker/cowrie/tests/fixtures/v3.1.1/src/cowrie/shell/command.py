# SPDX-FileCopyrightText: 2009-2014 Upi Tamminen <desaster@gmail.com>
# SPDX-FileCopyrightText: 2018-2026 Michel Oosterhof <michel@oosterhof.net>
#
# SPDX-License-Identifier: BSD-3-Clause

"""
This module contains code to run a command
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Callable

from twisted.internet import error
from twisted.logger import Logger
from twisted.python import failure


def process_status(code: int) -> failure.Failure:
    """A process-end reason carrying an exit status.

    ``ProcessDone`` for 0 (a clean exit) and ``ProcessTerminated`` otherwise, so
    the SSH session relays the right ``exit-status`` to the client.
    """
    if code == 0:
        return failure.Failure(error.ProcessDone(status=""))
    return failure.Failure(error.ProcessTerminated(exitCode=code))


class HoneyPotCommand:
    """
    This is the super class for all commands in cowrie/commands
    """

    _log = Logger()

    # True once exit() has run. An async callback firing after the command
    # already exited (a download completing after an abort) checks this so a
    # late exit() is a no-op. Class-level so it holds even for instances
    # created without __init__ (tests).
    exited: bool = False

    # This command's stdio wiring, set at spawn (see __init__). Class-level so
    # it holds even for instances created without __init__ (tests).
    pp: Any = None

    # Whether the command reads its stdin: set True by exactly the commands
    # that use self.input_data. A pipe feeding a shell is drained by the first
    # such command and is then empty for the rest, as in bash; a command that
    # does not read stdin (cd, echo, sleep ...) leaves the data for the next.
    consumes_stdin: bool = False

    def __init__(self, protocol, *args):
        self.protocol = protocol
        self.args = list(args)
        # Exit status, propagated to the owning shell on exit() for $? and
        # && / || . Commands set this (default 0 = success).
        self.exit_code: int = 0
        self.environ = self.protocol.cmdstack[-1].environ
        self.exported = self.protocol.cmdstack[-1].exported
        # The shell this command runs in (the nearest shell on the cmdstack at
        # spawn -- wrapper commands like busybox may sit in between). cwd and
        # user identity are snapshot at spawn, as a spawned process inherits
        # its parent's; the cd and su builtins mutate shell state, not their
        # own.
        self.shell = next(
            item
            for item in reversed(self.protocol.cmdstack)
            if hasattr(item, "queue_line")
        )
        self.cwd: str = self.shell.cwd
        self.user: dict[str, Any] = dict(self.shell.user)
        self.fs = self.protocol.fs
        self.data: bytes = b""  # output data
        # used to store STDIN data passed via PIPE
        self.input_data: bytes | None = None
        # This command's own stdio: the fd table and its redirections, handed
        # over at spawn. Held per command because a command that finishes late
        # (an async download) must still write to and clean up the fds it was
        # started with, not whichever command is running by the time it ends.
        pp: Any = getattr(self.protocol, "pp", None)
        self.pp: Any = pp
        self.writefn: Callable[[bytes], None]
        self.errorWritefn: Callable[[bytes], None]
        if pp and hasattr(pp, "write_stdout") and hasattr(pp, "write_stderr"):
            self.writefn = cast("Callable[[bytes], None]", pp.write_stdout)
            self.errorWritefn = cast("Callable[[bytes], None]", pp.write_stderr)
        else:
            self.writefn = cast("Callable[[bytes], None]", pp.outReceived)
            self.errorWritefn = cast("Callable[[bytes], None]", pp.errReceived)

    def write(self, data: str) -> None:
        """
        Write a string to the user on stdout
        """
        self.writefn(data.encode("utf8"))

    def writeBytes(self, data: bytes) -> None:
        """
        Like write() but input is bytes
        """
        self.writefn(data)

    def errorWrite(self, data: str) -> None:
        """
        Write errors to the user on stderr
        """
        self.errorWritefn(data.encode("utf8"))

    def check_arguments(self, application, args):
        files = []
        for arg in args:
            path = self.fs.resolve_path(arg, self.cwd)
            if self.fs.isdir(path):
                self.errorWrite(
                    f"{application}: error reading `{arg}': Is a directory\n"
                )
                continue
            files.append(path)
        return files

    def set_input_data(self, data: bytes) -> None:
        self.input_data = data

    def start(self) -> None:
        self.call()
        self.exit()

    def call(self) -> None:
        self.write(f"Hello World! [{self.args!r}]\n")

    def exit(self, code: int | None = None) -> None:
        """
        Sometimes client is disconnected and command exits after. So cmdstack is gone

        ``code`` sets this command's exit status (``$?`` for the shell that ran
        it). When omitted, the existing ``exit_code`` is kept, so a command that
        set it earlier (e.g. in an error callback) can just call ``exit()``.

        Exiting twice is safe: a second call (a download callback firing after
        the command already exited) returns without touching the cmdstack, so
        the shell is not resumed again.
        """
        if self.exited:
            return
        self.exited = True
        if code is not None:
            self.exit_code = code
        # Register this command's own redirection backing files for hashing at
        # session close. Read from self.pp, not the protocol's current pipe: a
        # command that finishes after a later one started would otherwise
        # register that command's files and orphan its own.
        if (
            self.protocol
            and self.protocol.terminal
            and getattr(self.pp, "redirect_real_files", None)
        ):
            for real_path, virtual_path in self.pp.redirect_real_files:
                self.protocol.terminal.redirFiles.add((real_path, virtual_path))

        if len(self.protocol.cmdstack):
            self.protocol.cmdstack.remove(self)

        if len(self.protocol.cmdstack):
            # Hand the exit status to the shell that ran us, for $? and the
            # && / || logic in runCommand.
            self.protocol.cmdstack[-1].last_exit_code = self.exit_code
            self.protocol.cmdstack[-1].resume()
        else:
            # No shell left to return to: either an `exit` builtin removed the
            # shell before this command finished, or the session is being torn
            # down. End the process with this command's status.
            self.protocol.end_process(self.exit_code)

    def exec_command(self, pp: Any, cmdclass: Any, *args: str) -> None:
        """Replace this command with another, as exec(2) does: leave the
        cmdstack and start the new command in this one's place, so its exit
        status goes to the shell that ran this command. The wrappers that
        dispatch to a real command (busybox, sudo) use this."""
        self.exited = True
        if self in self.protocol.cmdstack:
            self.protocol.cmdstack.remove(self)
        self.protocol.call_command(pp, cmdclass, *args)

    def handle_CTRL_C(self) -> None:
        self._log.info("Received CTRL-C, exiting..")
        self.write("^C\n")
        self.exit(130)  # 128 + SIGINT, like a real shell

    def lineReceived(self, line: str) -> None:
        self._log.info("QUEUED INPUT: {line}", line=line)
        # Queue on the innermost shell, the next stdin reader once this
        # command exits: an outer shell only resumes after the shells above
        # it unwind, so a line queued there would wait on the whole stack.
        # A transient child shell (a "$(...)" substitution, a "(...)" group or
        # a pipeline stage -- any shell with a ``done`` Deferred) is skipped:
        # its program is fixed source text, and typed input is stdin data for
        # the next real reader, never a command to run inside the child.
        for item in reversed(self.protocol.cmdstack):
            if hasattr(item, "queue_line") and getattr(item, "done", None) is None:
                item.queue_line(line)
                return

    def resume(self) -> None:
        pass

    def handle_TAB(self) -> None:
        pass

    def eofReceived(self) -> None:
        """
        EOF on stdin. Commands that read stdin override this to terminate; the
        default ignores it (the command does not read stdin).
        """
        pass

    def __repr__(self) -> str:
        return str(self.__class__.__name__)
