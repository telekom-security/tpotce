# SPDX-FileCopyrightText: 2009-2014 Upi Tamminen <desaster@gmail.com>
# SPDX-FileCopyrightText: 2014-2026 Michel Oosterhof <michel@oosterhof.net>
#
# SPDX-License-Identifier: BSD-3-Clause


from __future__ import annotations

import copy
import enum
import fnmatch
import posixpath
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from twisted.python.failure import Failure

    from cowrie.shell.prescan import HereDoc

from twisted.internet.defer import Deferred, succeed
from twisted.logger import Logger
from twisted.python.compat import iterbytes

from cowrie.core.config import CowrieConfig
from cowrie.shell import fs
from cowrie.shell.bashparse import (
    REDIRECTABLE,
    BashParser,
    BraceGroup,
    CaseClause,
    Command,
    ForClause,
    FunctionDef,
    IfClause,
    Pipeline,
    Statement,
    Subshell,
    SyntaxError_,
    WhileClause,
    max_input_size,
)
from cowrie.shell.parser import CommandParser
from cowrie.shell.pipe import FD_CAPTURE, FD_TERMINAL, PipeProtocol

# Honeypot safety caps. A loop in an uploaded script must never hang or exhaust
# the process: bound the number of iterations a single loop runs. Real malware
# downloaders loop a handful of times (over a few URLs or architectures); these
# ceilings are far above that yet keep a `while true` from running forever.
MAX_WHILE_ITERATIONS = 1000
MAX_FOR_ITEMS = 10000

# A word is a variable assignment only when a valid shell identifier precedes
# the =; bash treats words like "=", "=foo" or "1x=5" as command names.
ASSIGNMENT_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")


class LoopSignal(enum.Enum):
    """A pending ``break`` / ``continue`` for the innermost running loop.

    Set on the shell by the ``break`` / ``continue`` builtins and consumed by
    that shell's innermost loop continuation.
    """

    BREAK = "break"
    CONTINUE = "continue"


@dataclass
class _Continuation:
    """An internal queue entry that runs a Python callback when reached.

    Flow control schedules these between the statements it splices into
    ``cmdpending`` so it can react to a condition's exit status or step a loop.
    ``is_loop_cont`` marks the continuation that closes one loop-body iteration,
    which is where a pending ``break`` / ``continue`` is consumed.
    """

    fn: Callable[[], None]
    is_loop_cont: bool = False
    op: str | None = None


class HoneyPotShell:
    _log = Logger()

    def __init__(
        self,
        protocol: Any,
        interactive: bool = True,
        reads_stdin: bool = False,
    ) -> None:
        self.protocol = protocol
        self.interactive: bool = interactive
        # The shell's commands arrive over a live stdin (an SSH exec channel
        # running `bash`): a drained queue means idle, not done, until EOF.
        self.reads_stdin: bool = reads_stdin
        # Parsed-but-not-yet-evaluated statements; each is expanded against the
        # live environment only when it is about to run (see runCommand). A
        # subshell stays a single unit here so its &&/|| gate covers the whole
        # group; runCommand splices its statements in only when it runs.
        self.cmdpending: list[Statement | _Continuation] = []
        # A nested shell (e.g. a command substitution) inherits the live
        # environment, working directory and user identity of whichever shell
        # or command is currently running, as a forked shell process inherits
        # its parent's; the very first shell of a session falls back to the
        # login environment (all of which is exported), the login user, and
        # that user's home. cwd and user are this shell's own from here on: a
        # cd or su inside a substitution or nested script shell never changes
        # the parent's.
        if protocol.cmdstack:
            parent = protocol.cmdstack[-1]
            self.environ: dict[str, str] = copy.copy(parent.environ)
            self.exported: set[str] = copy.copy(parent.exported)
            self.cwd: str = parent.cwd
            self.user: dict[str, Any] = dict(parent.user)
            # Shell functions are inherited like the environment; a launching
            # command (bash -c, su -c) has none to pass on.
            self.functions: dict[str, list[Statement]] = dict(
                getattr(parent, "functions", {})
            )
        else:
            self.environ = copy.copy(protocol.environ)
            self.exported = set(protocol.environ.keys())
            self.functions = {}
            self.user = {
                "uid": protocol.user.uid,
                "gid": protocol.user.gid,
                "username": protocol.user.username,
                "home": protocol.user.avatar.home,
            }
            if protocol.fs.exists(protocol.user.avatar.home):
                self.cwd = protocol.user.avatar.home
            else:
                self.cwd = "/"
        if hasattr(protocol.user, "windowSize"):
            self.environ["COLUMNS"] = str(protocol.user.windowSize[1])
            self.environ["LINES"] = str(protocol.user.windowSize[0])
        self.parser = CommandParser()
        self.bashparser = BashParser(self)
        # The shell's fd table for the commands it runs (stdout=1, stderr=2),
        # inherited by each command as a forked process inherits its parent's
        # open files. A command applies its own redirections on top, and a
        # redirection on a compound command (`{ ...; } > f`) swaps the shell's
        # own entry for the group's duration. A top-level shell writes to the
        # terminal; a capture child (see run_child) points fd 1 at a sink.
        self.fds: dict[int, tuple[str, Any]] = {
            1: (FD_TERMINAL, None),
            2: (FD_TERMINAL, None),
        }
        # A shell a command starts (bash -c, a script, su -c) writes where that
        # command writes, so `$(bash -c ...)` or `sh x.sh > f` captures it.
        launcher = protocol.cmdstack[-1] if protocol.cmdstack else None
        launcher_pp = getattr(launcher, "pp", None)
        if launcher_pp is not None and getattr(launcher_pp, "targets", None):
            self.fds = {1: launcher_pp.targets[1], 2: launcher_pp.targets[2]}
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
        self.current_line: int = self.line_base + 1
        # A shell fed one line at a time (interactive, or reading live stdin)
        # keeps the lines of a construct still being written -- an open `if`,
        # quote or here-document -- until the input completes, as bash does
        # at its `> ` prompt. ``_lines_fed`` counts the
        # lines consumed so far, so errors name their line in the whole
        # input; ``stdin_eof`` is set once the live stdin feeding a
        # ``reads_stdin`` shell has closed.
        self._pending: list[str] = []
        self._lines_fed: int = 0
        self.stdin_eof: bool = False
        # Child-shell state (see run_child): a pipeline stage, a "(...)" group
        # or a $(...) substitution runs in its own shell, as a forked child.
        # ``stdin`` is the pipe buffer feeding it, handed to the first command
        # that reads; with none, an ``inherits_stdin`` child reads whatever its
        # parent reads. ``_capture`` is the bytearray fd 1 points at when the
        # child captures its stdout. ``done`` fires with the finished child
        # when it leaves the cmdstack -- its queue drained, or an inner `exit`
        # or `exec` ended it: the parent's read on the pipe seeing EOF.
        self.stdin: bytes | None = None
        self.inherits_stdin: bool = False
        self._capture: bytearray | None = None
        self.done: Deferred[HoneyPotShell] | None = None
        # Final status of the most recent command substitution expanded for
        # the current statement: bash makes it the statement's own status
        # when the statement is only assignments.
        self._subst_status: int | None = None
        # Exit status of the most recent command in this shell, for $? and the
        # && / || short-circuit logic.
        self.last_exit_code: int = 0
        # Flow-control state: how many loops are currently running (so break /
        # continue only act inside a loop) and a pending loop signal consumed by
        # the innermost loop continuation.
        self._loop_depth: int = 0
        self._loop_signal: LoopSignal | None = None
        # Trampoline state for _advance (see its docstring).
        self._advancing: bool = False
        self._advance_pending: bool = False
        # True once `exec` has replaced this shell with the command now
        # running: when that command finishes, the shell is gone (see resume).
        self._exec_replaced: bool = False

    # -- bashparse.ShellContext interface -----------------------------------

    def get_variable(self, name: str) -> str | None:
        """Look up a shell variable for the Lark word evaluator."""
        return self.environ.get(name)

    def error_prefix(self) -> str:
        """The prefix bash puts on an error it reports: its name and, for a
        non-interactive shell, the line being run."""
        if self.line_numbers:
            return f"{self.name}: line {self.current_line}: "
        return f"{self.name}: "

    def _make_pipe(
        self,
        cmdclass: Any,
        args: list[str],
        stdin: bytes | None,
        ops: list[dict[str, Any]] | None,
    ) -> PipeProtocol:
        """The stdio wiring for a command this shell starts: its fd table with
        ``ops`` applied, reporting redirection errors with this shell's
        prefix."""
        return PipeProtocol(
            self.protocol,
            cmdclass,
            args,
            stdin,
            dict(self.fds),
            ops,
            cwd=self.cwd,
            user=self.user,
            error_prefix=self.error_prefix(),
        )

    def get_status(self) -> str:
        """Return $? -- the last command's exit status as a string."""
        return str(self.last_exit_code)

    @property
    def captured(self) -> bytes:
        """The stdout captured by this child, when fd 1 points at a sink."""
        return bytes(self._capture) if self._capture is not None else b""

    def command_substitution(self, source: str) -> Deferred[str]:
        """Run ``source`` as a command substitution: the returned Deferred
        fires with its captured stdout, trailing newlines stripped, once the
        subshell finishes -- the evaluator awaits it, as bash blocks on the
        substitution pipe.

        The inner source runs in a capture subshell with the same sequencing
        as a top-level line: a same-line assignment is visible to later
        statements, ``$?`` carries across them, and ``&&`` / ``||``
        short-circuit. Output is captured instead of reaching the terminal.
        """

        def finished(subshell: HoneyPotShell) -> str:
            # The subshell's status becomes $? for the rest of the expansion
            # and, via _subst_status, for a statement of only assignments
            # (`x=$(false)` leaves $? = 1).
            self.last_exit_code = subshell.last_exit_code
            self._subst_status = subshell.last_exit_code
            # The captured output is attacker bytes and need not be valid UTF-8.
            return subshell.captured.decode(errors="replace").rstrip("\n")

        statements = self._resolve_eof(self.bashparser.parse(source), source, 1)
        fds, buf = self._capture_fds()
        return self.run_child(statements, fds=fds, capture_buf=buf).addCallback(
            finished
        )

    def _capture_fds(self) -> tuple[dict[int, tuple[str, Any]], bytearray]:
        """An fd table whose stdout is a fresh capture sink; stderr is
        inherited from this shell, so a substitution's or stage's errors reach
        the terminal (or the enclosing capture) as bash does."""
        buf = bytearray()
        return {1: (FD_CAPTURE, buf), 2: self.fds[2]}, buf

    def run_child(
        self,
        statements: Sequence[Statement | _Continuation],
        *,
        fds: dict[int, tuple[str, Any]],
        capture_buf: bytearray | None = None,
        stdin: bytes | None = None,
        inherits_stdin: bool = False,
    ) -> Deferred[HoneyPotShell]:
        """Run ``statements`` in a child shell, as a forked process would, and
        return a Deferred that fires with the finished child -- its status in
        ``last_exit_code`` and, when ``capture_buf`` is its stdout sink, its
        output in ``captured``.

        ``fds`` is the child's stdout/stderr table. ``stdin`` is the pipe
        buffer the child reads; without one, an ``inherits_stdin`` child reads
        whatever this shell reads (a pipeline's first stage, a "(...)" group)
        and any other child sees EOF. The child starts with copies of this
        shell's environment, working directory and identity, so nothing it
        changes persists here.

        The child runs its statements through the normal cmdpending /
        _advance machinery, so a command that pauses on a Deferred (wget)
        keeps the child alive until it completes -- the returned Deferred is
        the parent's blocking read on the pipe. When every statement completes
        synchronously the Deferred has already fired by the time this returns.
        """
        shell = HoneyPotShell(self.protocol, interactive=False)
        shell.fds = fds
        shell._capture = capture_buf
        shell.stdin = stdin
        shell.inherits_stdin = inherits_stdin
        done: Deferred[HoneyPotShell] = Deferred()
        shell.done = done
        self.protocol.cmdstack.append(shell)
        shell._queue_statements(statements)
        shell._advance()
        return done

    def _complete_child(self) -> None:
        """Fire ``done`` with this finished child shell: it is gone and the
        parent's read on its pipe sees EOF."""
        if self.done is None:
            return
        done, self.done = self.done, None
        done.callback(self)

    def exit_shell(self, code: int) -> None:
        """End this shell from the ``exit`` builtin: record the final status
        for whoever launched it, leave the cmdstack, and complete a capture
        subshell's substitution."""
        self.last_exit_code = code
        if self in self.protocol.cmdstack:
            self.protocol.cmdstack.remove(self)
        self._complete_child()

    def lineReceived(self, line: str) -> None:
        """Parse a command line with the Lark grammar and run the result."""
        self._feed_line(line, run=True)

    def queue_line(self, line: str) -> None:
        """Queue a line that arrived while a command holds the terminal.

        The statements are parsed now but run only when this shell resumes
        after the command exits, like tty input read by the next reader.
        """
        self._feed_line(line, run=False)

    def _feed_line(self, line: str, *, run: bool) -> None:
        """Take one unit of input: a physical line for a shell fed line by
        line, or the whole text for one given it at once (`bash -c`, a
        script, an exec command).

        A line-fed shell joins the line onto the lines of a construct still
        open and parses them together; while the result is incomplete it
        waits for more (showing the `> ` prompt when interactive). A whole
        text that ends inside a construct is reported as bash reports an
        unexpected end of file. ``run`` starts the queue; a line that arrives
        while a command holds the terminal is only queued.
        """
        self.protocol.events.dispatch(
            "cowrie.command.input", "CMD: %(input)s", input=line
        )
        if self.interactive or self.reads_stdin:
            self._pending.append(line)
            text = self._pending_text()
            first_line = self._lines_fed + 1
            # The newline _pending_text adds is not part of what was sent.
            if self._reject_oversized(text[:-1]):
                self._discard_pending()
                if run:
                    self._advance()
                return
            statements = self.bashparser.parse(text, first_line=first_line)
            if self._incomplete(statements):
                if run:
                    self.showPrompt()
                return
            self._discard_pending()
            queued: Sequence[Statement | _Continuation] = statements
        else:
            if self._reject_oversized(line):
                if run:
                    self._advance()
                return
            text, first_line = line, 1
            queued = self._resolve_eof(self.bashparser.parse(line), line, 1)
        self._queue_statements(queued, text, first_line)
        if run:
            self._advance()

    @staticmethod
    def _incomplete(statements: list[Statement]) -> SyntaxError_ | None:
        """The trailing incomplete-input marker of a parse, if any."""
        if statements and isinstance(statements[-1], SyntaxError_):
            if statements[-1].incomplete:
                return statements[-1]
        return None

    def _pending_text(self) -> str:
        """The buffered lines as the input they came from: each one ends in
        a newline, so an empty line typed into a here-document is a body
        line and a trailing backslash still awaits the next line."""
        return "\n".join(self._pending) + "\n"

    def _discard_pending(self) -> None:
        """Forget the buffered lines, counting them as consumed."""
        self._lines_fed += len(self._pending)
        self._pending.clear()

    def _flush_pending(self) -> None:
        """Stdin ended inside a construct: run the buffered lines as they
        stand, reporting the unexpected end of file as bash does."""
        text = self._pending_text()
        first_line = self._lines_fed + 1
        statements = self.bashparser.parse(text, first_line=first_line)
        self._discard_pending()
        self._queue_statements(
            self._resolve_eof(statements, text, first_line), text, first_line
        )

    def _resolve_eof(
        self, statements: list[Statement], text: str, first_line: int
    ) -> list[Statement | _Continuation]:
        """Settle a parse of ``text`` that ended inside a construct, now that
        no more input is coming: an unfinished here-document body is used as
        it is after bash's warning, a backslash-newline at the very end is
        dropped, and the other markers become the error bash prints for an
        unexpected end of file. ``first_line`` is the line number of the
        text's first line."""
        error = self._incomplete(statements)
        if error is None:
            return list(statements)
        rest: list[Statement | _Continuation] = list(statements[:-1])
        if error.opener == "heredoc":
            # bash warns once it has parsed the command holding the
            # here-document, after running the lines before it.
            last_line = first_line - 1 + len(text.rstrip("\n").split("\n"))
            heredocs = error.heredocs

            def warn() -> None:
                for heredoc in heredocs:
                    self._warn_unterminated_heredoc(heredoc, last_line)
                self._advance()

            rest.insert(max(len(rest) - 1, 0), _Continuation(warn))
            return rest
        if error.opener == "\\":
            # A backslash-newline at the very end: nothing follows it.
            return rest
        error.incomplete = False
        return [*rest, error]

    def _warn_unterminated_heredoc(self, heredoc: HereDoc, last_line: int) -> None:
        """bash's warning for a here-document that ran to the end of the
        input: named by the shell and the last line read, without the "-c"
        label its syntax errors carry."""
        label = (
            f"{self.name}: line {last_line}: "
            if self.line_numbers
            else f"{self.name}: "
        )
        self._write_shell_error(
            f"{label}warning: here-document at line {heredoc.lineno} delimited "
            f"by end-of-file (wanted `{heredoc.delimiter}')\n".encode()
        )

    def _reject_oversized(self, line: str) -> bool:
        """Refuse a line longer than max_input_size before it reaches the
        parser, reporting it like a syntax error. The input is still
        dispatched as a cowrie.command.input event: what the attacker sent
        is worth logging even when it is not worth parsing."""
        if len(line) <= max_input_size():
            return False
        self._report_syntax_error(SyntaxError_(token=""))
        return True

    def _queue_statements(
        self,
        statements: Sequence[Statement | _Continuation],
        source: str = "",
        first_line: int = 1,
    ) -> bool:
        """Append parsed statements to ``cmdpending`` for sequential execution.

        A subshell is queued as one unit so its join operator (e.g. the || in
        `x || (a; b)`) gates the whole group; runCommand splices the inner
        statements in only when the group actually runs. Cowrie does not give
        a ``(...)`` group its own isolated shell, so the inner statements run
        in the parent shell and their ``cd`` / variable effects persist.

        A syntax error is queued in place of the statement holding it and
        ends the queue: as in bash, earlier lines run, then the error is
        reported and nothing after it runs. Returns False at a syntax error.
        ``first_line`` is the line number of the first line of ``source``.
        """
        for statement in statements:
            if isinstance(statement, _Continuation):
                self.cmdpending.append(statement)
                continue
            error = self._find_syntax_error([statement])
            if error is not None:
                lines = source.split("\n")
                index = error.lineno - first_line
                if not error.source and 0 <= index < len(lines):
                    error.source = lines[index]
                self.cmdpending.append(error)
                return False
            self.cmdpending.append(statement)
        return True

    def _find_syntax_error(self, statements: list[Statement]) -> SyntaxError_ | None:
        """The first syntax error at the top level or nested anywhere inside a
        subshell or pipeline, since bash rejects the whole line holding it."""
        for statement in statements:
            if isinstance(statement, SyntaxError_):
                return statement
            if isinstance(statement, Subshell):
                inner = statement.statements
            elif isinstance(statement, Pipeline):
                inner = statement.stages
            else:
                continue
            error = self._find_syntax_error(inner)
            if error is not None:
                return error
        return None

    def _report_syntax_error(self, statement: SyntaxError_) -> None:
        """Write the message bash prints for a syntax error and set $? to 2.

        For input that ended inside a quote, backtick, ``$(`` or ``${``,
        that is the "unexpected EOF while looking for matching" line; an
        interactive bash follows it with the plain unexpected end of file.
        """
        self.current_line = self.line_base + (statement.lineno or 1)
        if statement.token:
            text = f"syntax error near unexpected token `{statement.token}'"
        else:
            text = "syntax error: unexpected end of file"
        if statement.opener:
            matching = f"unexpected EOF while looking for matching `{statement.opener}'"
            text = matching if self.line_numbers else f"{matching}\n{self.name}: {text}"
        if self.line_numbers:
            # A non-interactive bash also quotes the offending line.
            label = f"{self.name}: "
            if self.syntax_label:
                label += f"{self.syntax_label}: "
            label += f"line {self.current_line}: "
            message = f"{label}{text}\n"
            if statement.token:
                message += f"{label}`{statement.source}'\n"
        else:
            message = f"{self.name}: {text}\n"
        self._write_shell_error(message.encode())
        self.last_exit_code = 2  # bash uses 2 for a syntax error

    def _write_shell_error(
        self, message: bytes, ops: list[dict[str, Any]] | None = None
    ) -> bool:
        """Write an error the shell itself reports to its stderr (fd 2), with
        ``ops`` -- the failing command's own redirections -- applied, so
        `x 2>&1 | cat` and `( x ) 2>/dev/null` reroute it. Returns False,
        without writing it, when one of those redirections failed: bash
        applies them first, so their error is the only one reported."""
        pp = self._make_pipe(None, [], None, ops)
        for real_path, virtual_path in pp.redirect_real_files:
            self.protocol.terminal.redirFiles.add((real_path, virtual_path))
        if pp.has_redirection_error:
            return False
        pp.errReceived(message)
        return True

    def _finish(self) -> None:
        """The command queue is drained: do the shell's idle action.

        An interactive shell shows the next prompt. A shell reading commands
        from live stdin stays resident awaiting the next line or EOF. A
        top-level non-interactive shell (an exec session) ends the process. A
        nested non-interactive shell that runs a script or ``-c`` commands
        (sh/bash/su) removes itself from the cmdstack and resumes the command
        that launched it -- this is what hands control back once the script's
        async commands (wget/curl) have all finished. A command-substitution
        capture subshell removes itself and completes its substitution.
        """
        if self.interactive:
            self.showPrompt()
        elif self.reads_stdin:
            if not self.stdin_eof:
                # Idle, not done: the channel will deliver more lines or EOF.
                pass
            elif self._pending:
                # EOF arrived while a command held the terminal, with the
                # lines typed after it still open: run them as they stand.
                self._flush_pending()
                self._advance()
            else:
                # Stdin has closed: end with the last command's status, as
                # bash does when it runs out of input.
                self.protocol.end_process(self.last_exit_code)
        elif self.done is not None:
            # Child shell: its program has run to completion, so the parent's
            # read on its pipe sees EOF. The parent carries on from the done
            # callback, so no resume() of the shell below is needed here.
            if self in self.protocol.cmdstack:
                self.protocol.cmdstack.remove(self)
            self._complete_child()
        elif len(self.protocol.cmdstack) == 1:
            # Top-level non-interactive shell (an exec session): end the process
            # with the last command's status so the SSH channel reports a real
            # exit-status to the client.
            self.protocol.end_process(self.last_exit_code)
        elif self.protocol.cmdstack[-1] is self:
            # Nested script / `-c` shell whose queue is drained: unwind it and
            # let the launching command carry on. Done here rather than with an
            # unconditional pop() at the call site because an async command
            # (wget/curl) leaves this shell mid-stack until it later resumes and
            # drains us.
            self.protocol.cmdstack.remove(self)
            if self.protocol.cmdstack:
                self.protocol.cmdstack[-1].resume()

    def _statement_failed(self, failure: Failure) -> None:
        """An error escaped a statement's expansion or setup: log it, give the
        statement bash's generic failure status, and carry on with the queue --
        a shell that stops responding would fingerprint the honeypot."""
        self._log.failure("shell statement failed", failure=failure)
        self.last_exit_code = 1
        self._advance()

    def _short_circuit(self, op: str | None) -> bool:
        """Whether a statement joined by ``op`` should be skipped given the last
        command's exit status: ``&&`` after a failure, ``||`` after a success.
        """
        return (op == "&&" and self.last_exit_code != 0) or (
            op == "||" and self.last_exit_code == 0
        )

    def _advance(self) -> None:
        """Run the next queued command, or finish when the queue is drained.

        This is a trampoline. A command that completes synchronously calls
        ``exit() -> resume() -> _advance()`` again before the original call
        returns, so a naive recursive design would grow the Python stack by one
        frame per command -- a long ``;`` list, or any loop, would overflow it.
        Instead a re-entrant ``_advance`` just flags that more work is pending
        and unwinds; the outermost call drives a flat loop. A command that
        instead pauses on a Deferred (e.g. wget) unwinds normally and its later
        ``resume`` starts a fresh drive.
        """
        if self._advancing:
            self._advance_pending = True
            return
        self._advancing = True
        try:
            running = True
            while running:
                self._advance_pending = False
                if self.cmdpending:
                    self.runCommand()
                elif self._busy():
                    # The queue is drained but something this shell started is
                    # still running (an async download, or a pipeline stage
                    # waiting on one). The statement is not over: whatever is
                    # above resumes this shell when it finishes.
                    return
                else:
                    self._finish()
                running = self._advance_pending
        finally:
            self._advancing = False

    def _busy(self) -> bool:
        """Whether something this shell started is still on the cmdstack above
        it -- a command that has not exited, or a shell it launched."""
        stack = self.protocol.cmdstack
        return bool(stack) and self in stack and stack[-1] is not self

    # -- flow control -------------------------------------------------------
    #
    # Compound commands are driven through the same cmdpending queue as simple
    # commands. A handler splices its body statements to the front and, where it
    # must observe a result (a loop test, the next iteration), appends a
    # _Continuation callback that runs once the spliced statements have finished
    # -- which works whether those statements complete synchronously or pause on
    # a Deferred (e.g. wget) and resume later.

    def _end_loop(self) -> None:
        self._loop_depth -= 1
        self._advance()

    def _loop_body_end(self, step: Callable[[], None]) -> _Continuation:
        """The continuation appended after a loop body. It consumes a pending
        break / continue (break ends the loop; continue and a normal pass both
        run ``step`` again to take the next iteration)."""

        def loop_cont() -> None:
            signal = self._loop_signal
            self._loop_signal = None
            if signal is LoopSignal.BREAK:
                self._end_loop()
            else:
                step()

        return _Continuation(loop_cont, is_loop_cont=True)

    def _run_for(self, node: ForClause) -> None:
        """``for VAR in WORDS; do BODY; done`` over the expanded word list."""
        if node.items:
            d = Deferred.fromCoroutine(self.bashparser.evaluate(node.items))
        else:
            d = succeed([])
        d.addCallback(self._run_for_expanded, node)
        d.addErrback(self._statement_failed)

    def _run_for_expanded(self, values: list[str], node: ForClause) -> None:
        """Loop over the expanded ``in`` list, one body pass per word."""
        values = values[:MAX_FOR_ITEMS]
        if not values:
            # A loop over an empty list runs the body zero times and succeeds.
            self.last_exit_code = 0
            self._advance()
            return

        self._loop_depth += 1
        index = 0

        def step() -> None:
            nonlocal index
            if index >= len(values):
                self._end_loop()
                return
            self.environ[node.var] = values[index]
            index += 1
            self.cmdpending[0:0] = [*node.body, self._loop_body_end(step)]
            self._advance()

        step()

    def _run_while(self, node: WhileClause) -> None:
        """``while COND; do BODY; done`` (``until`` inverts the test)."""
        self._loop_depth += 1
        iterations = 0
        # A loop's exit status is its body's last command, or 0 if the body
        # never ran -- never the condition's. Each condition test overwrites $?,
        # so snapshot the body's status before re-testing and restore it on exit.
        body_status = 0

        def test() -> None:
            nonlocal iterations, body_status
            if iterations:
                body_status = self.last_exit_code
            if iterations >= MAX_WHILE_ITERATIONS:
                self.last_exit_code = body_status
                self._end_loop()
                return
            iterations += 1
            self.cmdpending[0:0] = [*node.condition, _Continuation(decide)]
            self._advance()

        def decide() -> None:
            succeeded = self.last_exit_code == 0
            run_body = (not succeeded) if node.until else succeeded
            if not run_body:
                self.last_exit_code = body_status
                self._end_loop()
                return
            self.cmdpending[0:0] = [*node.body, self._loop_body_end(test)]
            self._advance()

        test()

    def _run_if(self, node: IfClause) -> None:
        """``if COND; then BODY; [elif ...] [else ...] fi``."""

        def try_branch(index: int) -> None:
            if index >= len(node.branches):
                if node.else_body is not None:
                    self.cmdpending[0:0] = list(node.else_body)
                else:
                    # No branch ran: an if with no matching arm succeeds.
                    self.last_exit_code = 0
                self._advance()
                return
            condition, body = node.branches[index]
            self.cmdpending[0:0] = [
                *condition,
                _Continuation(lambda: decide(index, body)),
            ]
            self._advance()

        def decide(index: int, body: list[Statement]) -> None:
            if self.last_exit_code == 0:
                self.cmdpending[0:0] = list(body)
                self._advance()
            else:
                try_branch(index + 1)

        try_branch(0)

    def _run_case(self, node: CaseClause) -> None:
        """``case WORD in PATTERN) BODY ;; ... esac`` -- first match wins."""
        if node.word:
            # bash does not split the case word.
            d = Deferred.fromCoroutine(self.bashparser.evaluate(node.word, split=False))
        else:
            d = succeed([])
        d.addCallback(self._run_case_expanded, node)
        d.addErrback(self._statement_failed)

    def _run_case_expanded(self, tokens: list[str], node: CaseClause) -> None:
        """Match the expanded case word against each pattern arm."""
        word = " ".join(tokens)
        for patterns, body in node.items:
            for pattern in patterns:
                if fnmatch.fnmatchcase(word, self._strip_quotes(pattern)):
                    # A matched body's status is its last command, or 0 when the
                    # body is empty; the prior command's status must not leak.
                    if not body:
                        self.last_exit_code = 0
                    self.cmdpending[0:0] = list(body)
                    self._advance()
                    return
        # No pattern matched: the case succeeds.
        self.last_exit_code = 0
        self._advance()

    @staticmethod
    def _strip_quotes(pattern: str) -> str:
        """Drop a single pair of surrounding quotes from a case pattern so a
        quoted literal like ``'x86_64'`` matches; glob metacharacters in an
        unquoted pattern are left untouched for fnmatch."""
        if len(pattern) >= 2 and pattern[0] == pattern[-1] and pattern[0] in "\"'":
            return pattern[1:-1]
        return pattern

    def set_positional_params(self, args: list[str]) -> None:
        """Bind $1.. , $#, $@ and $* to ``args``."""
        for i, value in enumerate(args, start=1):
            self.environ[str(i)] = value
        self.environ["#"] = str(len(args))
        self.environ["@"] = " ".join(args)
        self.environ["*"] = " ".join(args)

    def _call_function(self, name: str, args: list[str]) -> None:
        """Run a function body with $1.. , $#, $@ and $* bound to ``args``.

        Only the positional parameters for this call ($1..$len(args)) are saved
        and restored. bash also unsets any higher-numbered parameters on entry,
        so a function called with fewer arguments than its caller would still
        see the caller's $2.. here; emulating that needs per-call param scoping.
        """
        body = self.functions[name]
        params = [str(i) for i in range(1, len(args) + 1)] + ["#", "@", "*"]
        saved = {key: self.environ.get(key) for key in params}
        self.set_positional_params(args)

        def restore() -> None:
            for key, value in saved.items():
                if value is None:
                    self.environ.pop(key, None)
                else:
                    self.environ[key] = value
            self._advance()

        self.cmdpending[0:0] = [*body, _Continuation(restore)]
        self._advance()

    def _strip_exec(self, tokens: list[str]) -> tuple[bool, bool]:
        """Consume a leading ``exec`` and its options from ``tokens`` in place.

        Returns ``(exec_seen, replaces_shell)``. ``exec cmd`` runs the remaining
        command through the normal machinery and, when it replaces the shell,
        the shell terminates once the command finishes. exec's own options do
        not change what runs (``-a NAME`` supplies argv[0], ``-c`` cleans the
        environment, ``-l`` makes it a login shell), so they are dropped. A
        backgrounded (``&``) command runs in a subshell, so ``exec`` there
        never replaces this shell; a bare ``exec`` (possibly with only
        redirections) runs no command and the shell survives it.
        """
        if not tokens or tokens[0] != "exec":
            return False, False
        tokens.pop(0)
        while tokens and tokens[0].startswith("-"):
            opt = tokens.pop(0)
            if opt == "--":
                break
            if "a" in opt and tokens:
                tokens.pop(0)
        replaces = bool(tokens) and tokens[-1] != "&"
        return True, replaces

    def runCommand(self):
        # A pending break / continue: drop the rest of the current loop body up
        # to the innermost loop continuation, which consumes the signal.
        if self._loop_signal is not None:
            while self.cmdpending:
                node = self.cmdpending[0]
                if isinstance(node, _Continuation) and node.is_loop_cont:
                    break
                self.cmdpending.pop(0)
            if not self.cmdpending:
                # break / continue with no enclosing loop body left: ignore it.
                self._loop_signal = None

        if not self.cmdpending:
            # The queue is drained.
            self._finish()
            return

        command = self.cmdpending.pop(0)

        # Internal flow-control continuations run their callback and return; they
        # carry no exit status and are never short-circuited.
        if isinstance(command, _Continuation):
            command.fn()
            return

        # A syntax error surfaces here when execution reaches its line.
        if isinstance(command, SyntaxError_):
            self._report_syntax_error(command)
            self._advance()
            return

        # && / || short-circuit: skip this statement (or whole group) based on
        # the previous command's exit status, leaving $? unchanged.
        if self._short_circuit(command.op):
            self._advance()
            return

        # A redirection on a compound command ("(...) > f", "done 2>&1")
        # applies to the whole group: evaluate its target words, then run the
        # group with the redirection applied to the shell's fds.
        if isinstance(command, REDIRECTABLE) and command.redirections is not None:
            self._run_redirected(command, command.redirections)
            return

        if not isinstance(command, Command):
            # A compound command (subshell, pipeline, group, loop, ...).
            self._run_compound(command)
            return

        # Expand the statement's words against the *current* environment, just
        # before it runs, so a same-line `x=hi; echo $x` sees the value. A
        # command substitution in a word suspends the expansion until its
        # subshell finishes; the statement then runs from the callback. With
        # only synchronous substitutions the Deferred has already fired and
        # the statement runs before this returns.
        if command.lineno:
            self.current_line = self.line_base + command.lineno
        self._subst_status = None
        d = Deferred.fromCoroutine(self.bashparser.evaluate(command))
        d.addCallback(self._run_expanded)
        d.addErrback(self._statement_failed)

    def _run_compound(self, command: Statement) -> None:
        """Dispatch a compound command (or a function definition), past the
        short-circuit and redirection checks in :meth:`runCommand`."""
        if isinstance(command, Subshell):
            self._run_subshell(command)
        elif isinstance(command, Pipeline):
            self._run_pipeline(command)
        elif isinstance(command, BraceGroup):
            # A { ...; } group runs its statements in the current shell.
            self.cmdpending[0:0] = command.statements
            self._advance()
        elif isinstance(command, ForClause):
            self._run_for(command)
        elif isinstance(command, IfClause):
            self._run_if(command)
        elif isinstance(command, WhileClause):
            self._run_while(command)
        elif isinstance(command, CaseClause):
            self._run_case(command)
        elif isinstance(command, FunctionDef):
            # Defining a function records its body and succeeds.
            self.functions[command.name] = command.body
            self.last_exit_code = 0
            self._advance()

    def _run_subshell(self, command: Subshell) -> None:
        """Run a "(...)" group in a child shell, so a cd, an assignment or an
        exit inside it does not reach this shell. It inherits this shell's fd
        table, so it writes where this shell writes (terminal, file, or the
        shared capture sink)."""
        stdin, self.stdin = self.stdin, None
        self.run_child(
            command.statements,
            fds=dict(self.fds),
            capture_buf=self._capture,
            stdin=stdin,
            inherits_stdin=True,
        ).addCallback(self._child_finished)

    def _child_finished(self, child: HoneyPotShell) -> None:
        """A "(...)" group's child shell is done: take its status and carry on.
        Its output went straight to this shell's fds (terminal, file or the
        shared capture sink), so there is nothing to fold in here."""
        self.last_exit_code = child.last_exit_code
        self._advance()

    def _run_redirected(self, command: Statement, redirections: Command) -> None:
        """Run a compound command with a trailing redirection ("(...) > f").

        Its target words are evaluated, then the redirection is applied to the
        shell's fd table for the group's duration and restored afterwards. A
        subshell copies the table into its child; an in-place group ({ }, a
        loop, if, case) reads the swapped table directly and the restore
        continuation, queued before the body, runs once the body drains. An
        input redirection (`{ cat; } < f`, `done <<EOF`) likewise becomes
        the shell's own stdin for the group, read by the first command in it
        that consumes stdin, as a shared pipe would be.
        """

        def apply(tokens: list[str]) -> None:
            _, ops = self.parser.parse_redirections(tokens)
            fds, stdin, error = self._open_redirection_fds(ops)
            if error:
                # bash reports the failure (already written) and skips the group.
                self.last_exit_code = 1
                self._advance()
                return
            saved_fds, self.fds = self.fds, fds
            saved_stdin = None
            if stdin is not None:
                saved_stdin = (self.stdin,)
                self.stdin = stdin
            self.cmdpending.insert(
                0, _Continuation(lambda: self._restore_fds(saved_fds, saved_stdin))
            )
            self._run_compound(command)

        d = Deferred.fromCoroutine(self.bashparser.evaluate(redirections))
        d.addCallback(apply)
        d.addErrback(self._statement_failed)

    def _restore_fds(
        self,
        saved: dict[int, tuple[str, Any]],
        saved_stdin: tuple[bytes | None] | None = None,
    ) -> None:
        """Restore the shell's fd table, and its stdin when the group
        redirected that too, after a redirected group finishes."""
        self.fds = saved
        if saved_stdin is not None:
            (self.stdin,) = saved_stdin
        self._advance()

    def _open_redirection_fds(
        self, ops: list[dict[str, Any]]
    ) -> tuple[dict[int, tuple[str, Any]], bytes | None, bool]:
        """Apply redirection ops over this shell's fd table, opening any files
        once for the whole group, and register their backing files. Returns the
        new table, the stdin an input redirection among them supplies (or
        None), and whether a redirection failed."""
        pp = self._make_pipe(None, [], None, ops)
        for real_path, virtual_path in pp.redirect_real_files:
            self.protocol.terminal.redirFiles.add((real_path, virtual_path))
        return (
            {1: pp.targets[1], 2: pp.targets[2]},
            pp.input_data,
            pp.has_redirection_error,
        )

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

    def _run_expanded(self, cmdAndArgs: list[str]) -> None:
        """Run one expanded simple command: split off leading assignments,
        resolve it to a command class, wire its stdin and redirections, and
        start it."""
        pp = None

        # Probably no reason to be this comprehensive for just PATH...
        environ = copy.copy(self.environ)
        cmd_tokens: list[str] = []
        while cmdAndArgs:
            piece = cmdAndArgs.pop(0)
            if ASSIGNMENT_WORD.match(piece):
                key, val = piece.split("=", 1)
                environ[key] = val
                continue
            cmd_tokens = [piece, *cmdAndArgs]
            break

        exec_seen, exec_replace = self._strip_exec(cmd_tokens)

        if not cmd_tokens:
            # A statement of only assignments (no command) persists those
            # variables for the rest of the session. They are shell variables,
            # not exported, so self.exported is left untouched. Its status is
            # that of the last command substitution its words ran, as in bash
            # (`x=$(false)` leaves $? = 1); with no substitution a bare
            # assignment succeeds.
            self.environ = environ
            self.last_exit_code = (
                self._subst_status if self._subst_status is not None else 0
            )
            self._advance()
            return

        # A call to a shell function defined earlier runs its body with the
        # positional parameters bound to the call arguments. `exec` never
        # sees functions: it only runs files.
        if not exec_replace and cmd_tokens[0] in self.functions:
            self._call_function(cmd_tokens[0], cmd_tokens[1:])
            return

        args, ops = self.parser.parse_redirections(cmd_tokens)
        if not args:
            if ops:
                # Handle redirection without command (e.g. > file). This
                # creates the backing files via _setup_redirections; register
                # them so they are hashed/renamed or removed at session close
                # instead of being orphaned in the download directory.
                pp = self._make_pipe(None, [], None, ops)
                for real_path, virtual_path in pp.redirect_real_files:
                    self.protocol.terminal.redirFiles.add((real_path, virtual_path))
                self.last_exit_code = 1 if pp.has_redirection_error else 0
            self._advance()
            return

        cmd = args.pop(0)
        cmdclass = self.protocol.getCommand(
            cmd, environ.get("PATH", "").split(":"), self.cwd
        )
        if not cmdclass:
            self.protocol.events.dispatch(
                "cowrie.command.failed",
                "Command not found: %(input)s",
                input=cmd + " " + " ".join(args),
            )
            if exec_seen:
                # exec reports a failed lookup as its own error.
                message = f"{self.error_prefix()}exec: {cmd}: not found\n".encode()
            else:
                message = self.command_not_found_message(cmd).encode("utf8")
            if not self._write_shell_error(message, ops):
                # Even exec carries on after a failed redirection, with status 1.
                self.last_exit_code = 1
                self._advance()
                return

            self.last_exit_code = 127  # command not found
            if exec_replace and not self.interactive:
                # A failed exec ends a non-interactive shell with 127; an
                # interactive one survives it (bash without execfail).
                self._terminate(127)
                return
            self._advance()
            return

        self._log.info("Command found: {input}", input=cmd + " " + " ".join(args))
        # The pipe buffer feeding this shell is offered to every command and
        # drained by the first one that consumes stdin, as a shared pipe is;
        # a stdin redirection on the command replaces it.
        stdin = self.stdin
        if cmdclass.consumes_stdin:
            self.stdin = None
        pp = self._make_pipe(cmdclass, args, stdin, ops)
        # Buffered input -- a pipe, a `< file` or a here-document -- ends in
        # EOF once read; only a terminal or the live channel stays open.
        pp.stdin_from_pipe = stdin is not None or any(
            op["type"] in ("stdin", "heredoc") for op in ops
        )
        # The name the command was invoked by, its argv[0].
        pp.argv0 = cmd
        if pp.has_redirection_error:
            # bash reports the failure (already written) and skips the command.
            self.last_exit_code = 1
            self._advance()
            return

        self._exec_replaced = exec_replace
        self.protocol.call_command(pp, cmdclass, *args)

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

    def resume(self) -> None:
        if self._exec_replaced:
            # The command that replaced this shell via `exec` has finished;
            # there is no shell to come back to.
            self._terminate(self.last_exit_code)
            return
        if self.interactive:
            self.protocol.setInsertMode()
        # Go through the _advance trampoline so a command that resumes us
        # synchronously does not deepen the Python stack (see _advance).
        self._advance()

    def _terminate(self, code: int) -> None:
        """End this shell, as when `exec` replaces it: unwind to whatever ran
        it, or end the process with ``code`` when nothing else is running.

        This mirrors the `exit` builtin's teardown: the shell leaves the
        cmdstack and either the launching command carries on (nested shell) or,
        with the cmdstack empty, the process ends and the SSH channel reports
        ``code`` to the client.
        """
        self.last_exit_code = code
        if self in self.protocol.cmdstack:
            self.protocol.cmdstack.remove(self)
        if self.done is not None:
            # The parent carries on from the done callback; resuming the shell
            # below as well would drive it twice.
            self._complete_child()
            return
        if self.protocol.cmdstack:
            self.protocol.cmdstack[-1].resume()
        else:
            self.protocol.end_process(code)

    def showPrompt(self) -> None:
        """Write the prompt: PS1, or the `> ` continuation prompt (PS2) while
        lines of an unfinished construct are buffered."""
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
        # ``ps`` / ``pn`` are what the line editor redraws (CTRL-L).
        self.protocol.ps = (prompt.encode("utf-8"), b"> ")
        self.protocol.pn = 1 if self._pending else 0
        self.protocol.terminal.write(self.protocol.ps[self.protocol.pn])

    def eofReceived(self) -> None:
        """
        EOF with the shell as the active reader (no command running) logs out,
        exiting with the last command's status ($?) as bash does. At the `> `
        prompt it instead ends the construct being written: bash reports the
        unexpected end of file (or runs an unfinished here-document after a
        warning) and, when interactive, shows the next prompt rather than
        logging out.
        """
        self.stdin_eof = True
        if self._pending:
            self._flush_pending()
            self._advance()
            return
        self._log.info("received eof, logging out")
        self.protocol.end_process(self.last_exit_code)

    def handle_CTRL_C(self) -> None:
        self.protocol.lineBuffer = []
        self.protocol.lineBufferIndex = 0
        # At the `> ` prompt CTRL-C abandons the construct being written.
        self._discard_pending()
        self.protocol.terminal.write(b"\n")
        self.showPrompt()

    def handle_TAB(self) -> None:
        """
        lineBuffer is an array of bytes

        These are the raw keystrokes typed so far, so they need not be valid
        UTF-8: an ordinary multi-byte character is an incomplete sequence
        until its last byte arrives. Decoding is lossy for the same reason
        lineReceived's is -- completing on a line must not raise out of the
        protocol and kill the session.
        """
        if not self.protocol.lineBuffer:
            return

        line: bytes = b"".join(self.protocol.lineBuffer)
        if line[-1:] == b" ":
            clue = ""
        else:
            clue = line.split()[-1].decode("utf8", errors="replace")

        # clue now contains the string to complete or is empty.
        # line contains the buffer as bytes
        basedir = posixpath.dirname(clue)
        if basedir and basedir[-1] != "/":
            basedir += "/"

        if not basedir:
            tmppath = self.cwd
        else:
            tmppath = basedir

        try:
            r = self.protocol.fs.resolve_path(tmppath, self.cwd)
        except Exception:
            return

        if not self.protocol.fs.exists(r):
            return

        files = []
        for x in self.protocol.fs.get_path(r):
            if clue == "":
                files.append(x)
                continue
            if not x[fs.A_NAME].startswith(posixpath.basename(clue)):
                continue
            files.append(x)

        if not files:
            return

        # Clear early so we can call showPrompt if needed
        for _i in range(self.protocol.lineBufferIndex):
            self.protocol.terminal.cursorBackward()
            self.protocol.terminal.deleteCharacter()

        newbuf = ""
        if len(files) == 1:
            newbuf = " ".join(
                [
                    *line.decode("utf8", errors="replace").split()[:-1],
                    f"{basedir}{files[0][fs.A_NAME]}",
                ]
            )
            if files[0][fs.A_TYPE] == fs.T_DIR:
                newbuf += "/"
            else:
                newbuf += " "
            newbyt = newbuf.encode("utf8")
        else:
            if posixpath.basename(clue):
                prefix = posixpath.commonprefix([x[fs.A_NAME] for x in files])
            else:
                prefix = ""
            first = line.decode("utf8", errors="replace").split(" ")[:-1]
            newbuf = " ".join([*first, f"{basedir}{prefix}"])
            newbyt = newbuf.encode("utf8")
            if newbyt == b"".join(self.protocol.lineBuffer):
                self.protocol.terminal.write(b"\n")
                maxlen = max(len(x[fs.A_NAME]) for x in files) + 1
                # windowSize is only set once the client sent a window-change
                # request; fall back to 80 columns like __init__ does above.
                if hasattr(self.protocol.user, "windowSize"):
                    columns = self.protocol.user.windowSize[1]
                else:
                    columns = 80
                perline = max(1, int(columns / (maxlen + 1)))
                count = 0
                for file in files:
                    if count == perline:
                        count = 0
                        self.protocol.terminal.write(b"\n")
                    self.protocol.terminal.write(
                        file[fs.A_NAME].ljust(maxlen).encode("utf8")
                    )
                    count += 1
                self.protocol.terminal.write(b"\n")
                self.showPrompt()

        self.protocol.lineBuffer = list(iterbytes(newbyt))
        self.protocol.lineBufferIndex = len(self.protocol.lineBuffer)
        self.protocol.terminal.write(newbyt)
