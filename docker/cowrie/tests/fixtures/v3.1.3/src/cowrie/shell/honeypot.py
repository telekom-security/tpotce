# SPDX-FileCopyrightText: 2009-2014 Upi Tamminen <desaster@gmail.com>
# SPDX-FileCopyrightText: 2014-2026 Michel Oosterhof <michel@oosterhof.net>
#
# SPDX-License-Identifier: BSD-3-Clause

# Excerpt of Cowrie v3.1.3 (c7773f49): the imports, the class line, the environment and the
# shell name of __init__, error_prefix, showPrompt, command_not_found_message,
# _queue_statements, _write_shell_error, _statement_failed, _advance, runCommand, _run_pipeline and
# _run_expanded, the text patch_cowrie_persona_support.py touches.
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

    def _queue_statements(self, statements: list[Statement], source: str = "") -> bool:
        """Append parsed statements to ``cmdpending`` for sequential execution.

        A subshell is queued as one unit so its join operator (e.g. the || in
        `x || (a; b)`) gates the whole group; runCommand splices the inner
        statements in only when the group actually runs. Cowrie does not give
        a ``(...)`` group its own isolated shell, so the inner statements run
        in the parent shell and their ``cd`` / variable effects persist.

        A syntax error is queued in place of the statement holding it and
        ends the queue: as in bash, earlier lines run, then the error is
        reported and nothing after it runs. Returns False at a syntax error.
        """
        for statement in statements:
            error = self._find_syntax_error([statement])
            if error is not None:
                lines = source.split("\n")
                if not error.source and 0 < error.lineno <= len(lines):
                    error.source = lines[error.lineno - 1]
                self.cmdpending.append(error)
                return False
            self.cmdpending.append(statement)
        return True

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
            # Idle, not done: the channel will deliver more lines or EOF.
            pass
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

    def lineReceived(self, line: str) -> None:
        """Parse a command line with the Lark grammar and run the result."""
        self.protocol.events.dispatch(
            "cowrie.command.input", "CMD: %(input)s", input=line
        )
        if self._reject_oversized(line):
            self._advance()
            return
        self._queue_statements(self.bashparser.parse(line), line)
        self._advance()

    def eofReceived(self) -> None:
        """
        EOF with the shell as the active reader (no command running) logs out,
        exiting with the last command's status ($?) as bash does.
        """
        self._log.info("received eof, logging out")
        self.protocol.end_process(self.last_exit_code)

    def handle_CTRL_C(self) -> None:
        self.protocol.lineBuffer = []
        self.protocol.lineBufferIndex = 0
        self.protocol.terminal.write(b"\n")
        self.showPrompt()

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
        pp.stdin_from_pipe = stdin is not None
        # The name the command was invoked by, its argv[0].
        pp.argv0 = cmd
        if pp.has_redirection_error:
            # bash reports the failure (already written) and skips the command.
            self.last_exit_code = 1
            self._advance()
            return

        self._exec_replaced = exec_replace
        self.protocol.call_command(pp, cmdclass, *args)
