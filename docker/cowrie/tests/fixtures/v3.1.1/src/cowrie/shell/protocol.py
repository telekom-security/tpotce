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

if TYPE_CHECKING:
    from twisted.python import failure

    from cowrie.core.events import EventLog


@functools.cache
def boot_offset() -> float:
    """Seconds between the emulated machine's boot and cowrie's start, from
    [honeypot] boot_offset. Unset, it is chosen once per process between one
    and ninety days, so a restarted honeypot does not report seconds of
    uptime and installs do not share one value."""
    configured = CowrieConfig.getint("honeypot", "boot_offset", fallback=-1)
    if configured >= 0:
        return float(configured)
    return float(random.randint(86400, 90 * 86400))


class HoneyPotBaseProtocol(insults.TerminalProtocol, TimeoutMixin):
    """
    Base protocol for interactive and non-interactive use
    """

    _log = Logger()

    # The session's event emitter, set from the transport in connectionMade.
    events: EventLog

    # True once end_process() has run. Class-level so it holds even for
    # instances created without __init__ (tests).
    _process_ended: bool = False

    commands: ClassVar[dict] = {}
    for c in cowrie.commands.command_modules:
        try:
            module = import_module(f"cowrie.commands.{c}")
            commands.update(module.commands)
        except ImportError:
            _log.failure("Failed to import command {cmd}", cmd=c)

    def __init__(self, avatar):
        self.user = avatar
        self.environ = avatar.environ
        self.hostname: str = self.user.server.hostname
        self.fs = self.user.server.fs
        # The stdio wiring being handed to the command now starting, which the
        # command keeps as its own (HoneyPotCommand.pp). It stays set to the
        # most recently started command's afterwards, which is what a capture
        # shell reads to collect a command's captured output. Only ever one at
        # a time: commands run strictly in sequence.
        self.pp = None
        self.logintime: float
        self.realClientIP: str
        self.realClientPort: int
        self.kippoIP: str
        self.kippoIPv6: str = ""
        self.clientIP: str
        self.sessionno: int
        self.factory = None

        self.data = None
        self.password_input = False
        self.cmdstack = []

    def getProtoTransport(self):
        """
        Due to protocol nesting differences, we need provide how we grab
        the proper transport to access underlying SSH information. Meant to be
        overridden for other protocols.
        """
        return self.terminal.transport.session.conn.transport

    def connectionMade(self) -> None:
        pt = self.getProtoTransport()

        self.factory = pt.factory
        # The session's event emitter, owned by the transport. Kept across
        # connectionLost so a command's deferred callback that outlives the
        # session (a download completing after disconnect) can still emit an
        # attributed, late-flagged event.
        self.events = pt.events
        self.sessionno = pt.transport.sessionno
        self.realClientIP = pt.transport.getPeer().host
        self.realClientPort = pt.transport.getPeer().port
        self.logintime = time.time()

        self.events.dispatch("cowrie.session.params", "", arch=self.user.server.arch)
        self.fs.generated_files["/proc/uptime"] = self.proc_uptime

        idle_timeout = CowrieConfig.getint("honeypot", "idle_timeout", fallback=180)
        self.setTimeout(idle_timeout)

        # Source IP of client in user visible reports (can be fake or real)
        self.clientIP = CowrieConfig.get(
            "honeypot", "fake_addr", fallback=self.realClientIP
        )

        # Source IP of server in user visible reports (can be fake or real)
        if CowrieConfig.has_option("honeypot", "internet_facing_ip"):
            self.kippoIP = CowrieConfig.get("honeypot", "internet_facing_ip")
        else:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    s.connect(("8.8.8.8", 80))
                    self.kippoIP = s.getsockname()[0]
            except Exception:
                self.kippoIP = "192.168.0.1"

        # IPv6 GUA of server in user visible reports (can be fake or real)
        if CowrieConfig.has_option("honeypot", "internet_facing_ipv6"):
            self.kippoIPv6 = CowrieConfig.get("honeypot", "internet_facing_ipv6")
        else:
            try:
                with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as s:
                    s.connect(
                        ("2001:4860:4860::8888", 80)
                    )  # NOSONAR - probe target to detect host GUA, not a secret
                    addr = s.getsockname()[0]
                    # Only use GUA, not link-local
                    self.kippoIPv6 = addr if not addr.lower().startswith("fe80") else ""
            except Exception:
                self.kippoIPv6 = ""

    def end_process(self, code: int) -> None:
        """
        End the session's process with exit status ``code``, once.

        Every path that ends the process comes through here: the exec queue
        draining, ``exit``, ``exec``, EOF, a command finishing with no shell
        left, and the idle timeout. Only the first call reaches the channel.
        A later one would report a second, possibly different, exit status:
        a channel EOF that arrives after an exec command already finished
        reaches the finished shell still on the cmdstack, and input queued
        behind ``exit`` is still read before a telnet connection closes.
        """
        if self._process_ended:
            return
        self._process_ended = True
        terminal = self.terminal
        if terminal is None or terminal.transport is None:
            # The client already disconnected: there is no channel to tell.
            return
        terminal.transport.processEnded(command.process_status(code))

    def timeoutConnection(self) -> None:
        """
        this logs out when connection times out
        """
        self.end_process(1)

    def connectionLost(self, reason: failure.Failure = connectionDone) -> None:
        """
        Called when the connection is shut down.
        Clear any circular references here, and any external references to
        this Protocol. The connection has been closed.
        """
        self.setTimeout(None)
        insults.TerminalProtocol.connectionLost(self, reason)
        self.terminal = None  # (this should be done by super above)
        self.cmdstack = []
        self.fs = None
        self.pp = None
        self.user = None
        self.environ = None

    def txtcmd(self, txt: bytes) -> object:
        class Command_txtcmd(command.HoneyPotCommand):
            def call(self):
                self.writeBytes(txt)

        return Command_txtcmd

    def scriptcmd(self, path: str, name: str) -> object:
        """Return a command class that executes a shell script from the virtual filesystem."""
        from cowrie.shell.script import run_script_file

        class Command_scriptcmd(command.HoneyPotCommand):
            def call(self):
                # Running ./file or /path/file: the kernel rejects a binary with
                # ENOEXEC ("cannot execute binary file"); a shell script is run
                # through the parser (the shebang is parsed as a comment).
                prefix = self.shell.error_prefix()
                run_script_file(
                    self,
                    path,
                    name=name,
                    not_found_message=f"{prefix}{name}: No such file or directory\n",
                    binary_message=(
                        f"{prefix}{name}: cannot execute binary file: "
                        "Exec format error\n"
                    ),
                )

        return Command_scriptcmd

    def isCommand(self, cmd):
        """
        Check if cmd (the argument of a command) is a command, too.
        """
        return True if cmd in self.commands else False

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

    def lineReceived(self, line: bytes) -> None:
        """
        IMPORTANT
        Before this, all data is 'bytes'. Here it converts to 'string' and
        commands work with string rather than bytes.

        Invalid UTF-8 (binary piped or pasted into the shell) becomes
        replacement characters: the line must not raise out of the protocol
        and kill the session.
        """
        string = line.decode("utf8", errors="replace")

        if self.cmdstack:
            self.cmdstack[-1].lineReceived(string)
        else:
            self._log.info("discarding input {input}", input=string)
            self.end_process(0)

    def call_command(self, pp, cmd, *args):
        """Run a command with the stdio wiring ``pp``. A command that runs
        another synchronously during ``start()`` (``su -c``, ``sh -c``, a
        nested shell, busybox) reaches back here and executes it inline."""
        self.pp = pp
        obj = cmd(self, *args)
        obj.set_input_data(pp.input_data)
        self.cmdstack.append(obj)
        obj.start()

        # Mirror ProcessProtocol.transport.closeStdin(): if the command parked
        # waiting for stdin but nothing will ever write to it, signal EOF so it
        # terminates instead of leaking on the cmdstack. Stdin is left open when
        # a live source will deliver its own EOF later: an interactive terminal,
        # or the SSH exec channel feeding the top-level command (e.g. `scp -t`,
        # whose pushed bytes arrive after the command has started). A piped
        # command reads buffered output, so it gets EOF here.
        if self.cmdstack and self.cmdstack[-1] is obj:
            parent = self.cmdstack[-2] if len(self.cmdstack) >= 2 else None
            # A child shell that inherits its parent's stdin (a pipeline's
            # first stage, a "(...)" group) is looked through to the shell
            # that owns the stdin.
            while parent is not None and getattr(parent, "inherits_stdin", False):
                index = self.cmdstack.index(parent)
                parent = self.cmdstack[index - 1] if index > 0 else None
            live_stdin = (
                not getattr(pp, "stdin_from_pipe", False)
                and parent is not None
                and (
                    getattr(parent, "interactive", False)
                    or (
                        isinstance(self, HoneyPotExecProtocol)
                        and parent is self.cmdstack[0]
                    )
                )
            )
            if not live_stdin:
                obj.eofReceived()

    def boot_time(self) -> float:
        """When the emulated machine booted: boot_offset before cowrie
        started."""
        return float(self.getProtoTransport().factory.starttime) - boot_offset()

    def uptime(self):
        """
        Uptime
        """
        return time.time() - self.boot_time()

    def proc_uptime(self) -> bytes:
        """/proc/uptime: seconds since boot, and idle seconds summed over the
        CPUs listed in /proc/cpuinfo, as a lightly loaded machine reports."""
        uptime = self.uptime()
        try:
            cpuinfo = self.fs.file_contents("/proc/cpuinfo")
        except (fs.FileNotFound, IsADirectoryError):
            cpuinfo = b""
        processors = [x for x in cpuinfo.splitlines() if x.startswith(b"processor")]
        cpus = max(1, len(processors))
        return f"{uptime:.2f} {uptime * cpus * 0.97:.2f}\n".encode()

    def eofReceived(self) -> None:
        """
        EOF on stdin, from a terminal CTRL-D or a closed SSH channel. Deliver it
        to the command currently reading stdin; with no command running the
        shell handles it (logout). If the cmdstack is gone, end the session.
        """
        if self.cmdstack:
            self.cmdstack[-1].eofReceived()
        else:
            self.end_process(0)


class HoneyPotExecProtocol(HoneyPotBaseProtocol):
    _log = Logger()

    # input_data is static buffer for stdin received from remote client
    input_data = b""

    # Longest accepted stdin line in line mode, bash's per-argument limit
    # (MAX_ARG_STRLEN). Bytes beyond it are dropped so an endless
    # unterminated stream cannot grow memory without bound.
    STDIN_LINE_MAX = 131072

    def __init__(self, avatar, execcmd):
        """
        IMPORTANT
        Before this, execcmd is 'bytes'. Here it converts to 'string' and
        commands work with string rather than bytes.
        """
        # The exec command is attacker input and need not be valid UTF-8.
        # Every caller reads execcmd right after construction, so it must
        # always be set.
        self.execcmd = execcmd.decode("utf8", errors="replace")

        # When the exec'd command is a shell reading commands from the channel
        # (`ssh host bash`), stdin is delivered to the cmdstack line by line
        # instead of being buffered raw in input_data.
        self.stdin_line_mode: bool = False
        self._stdin_line = bytearray()
        self._stdin_last_cr: bool = False

        HoneyPotBaseProtocol.__init__(self, avatar)

    def connectionMade(self) -> None:
        HoneyPotBaseProtocol.connectionMade(self)
        self.setTimeout(60)
        self.cmdstack = [honeypot.HoneyPotShell(self, interactive=False)]
        # The parser treats newlines as statement separators, so a multi-line
        # exec payload (including loops/conditionals) is run as written.
        self.cmdstack[0].lineReceived(self.execcmd)

    def keystrokeReceived(self, keyID, modifier):
        if not self.stdin_line_mode:
            self.input_data += keyID
            return
        if not isinstance(keyID, bytes):
            # A function key parsed from an escape sequence has no place in a
            # line-based stdin stream.
            return
        last_cr = self._stdin_last_cr
        self._stdin_last_cr = keyID == b"\r"
        if keyID == b"\n" and last_cr:
            # The \n of a \r\n pair; the \r already dispatched the line.
            return
        # Control bytes are handled as a tty would, which is only strictly
        # right when the client requested a pty; in a plain pipe they are
        # rare enough that the difference does not matter.
        if keyID in (b"\r", b"\n"):
            self._dispatch_stdin_line()
        elif keyID == b"\x04" and not self._stdin_line:
            # CTRL-D on an empty line is EOF for the shell reading stdin.
            HoneyPotBaseProtocol.eofReceived(self)
        elif keyID in (b"\x08", b"\x7f"):
            # Backspace / delete: drop the last byte of the pending line.
            del self._stdin_line[-1:]
        elif keyID == b"\x03":
            # CTRL-C: discard the pending line.
            self._stdin_line.clear()
        elif len(self._stdin_line) < self.STDIN_LINE_MAX:
            self._stdin_line += keyID

    def _dispatch_stdin_line(self) -> None:
        """Run the accumulated stdin line through the command stack."""
        line = bytes(self._stdin_line)
        self._stdin_line.clear()
        self.lineReceived(line)

    def setInsertMode(self) -> None:
        """Insert-mode toggle requested when an interactive shell resumes; an
        exec channel has no recvline editor, so there is no mode to switch."""

    def eofReceived(self) -> None:
        if self.stdin_line_mode:
            if self._stdin_line:
                # A final line without a terminator still runs, as bash does
                # when its stdin ends without a newline.
                self._dispatch_stdin_line()
            if not any(getattr(item, "reads_stdin", False) for item in self.cmdstack):
                # The stdin-reading shell already exited (e.g. `exit`) and
                # ended the process; nothing is left to deliver EOF to.
                return
        HoneyPotBaseProtocol.eofReceived(self)


class HoneyPotInteractiveProtocol(HoneyPotBaseProtocol, recvline.HistoricRecvLine):
    def __init__(self, avatar):
        recvline.HistoricRecvLine.__init__(self)
        HoneyPotBaseProtocol.__init__(self, avatar)

    def connectionMade(self) -> None:
        self.displayMOTD()

        HoneyPotBaseProtocol.connectionMade(self)
        recvline.HistoricRecvLine.connectionMade(self)

        self.cmdstack = [honeypot.HoneyPotShell(self)]
        # Show the first prompt now that the interactive shell is on the stack.
        self.cmdstack[0].showPrompt()

        self.keyHandlers.update(
            {
                b"\x00": self.handle_NUL,  # NUL (NVT line ending, keepalive padding)
                b"\x01": self.handle_HOME,  # CTRL-A
                b"\x02": self.handle_LEFT,  # CTRL-B
                b"\x03": self.handle_CTRL_C,  # CTRL-C
                b"\x04": self.handle_CTRL_D,  # CTRL-D
                b"\x05": self.handle_END,  # CTRL-E
                b"\x06": self.handle_RIGHT,  # CTRL-F
                b"\x08": self.handle_BACKSPACE,  # CTRL-H
                b"\x09": self.handle_TAB,
                b"\x0b": self.handle_CTRL_K,  # CTRL-K
                b"\x0c": self.handle_CTRL_L,  # CTRL-L
                b"\x0e": self.handle_DOWN,  # CTRL-N
                b"\x10": self.handle_UP,  # CTRL-P
                b"\x15": self.handle_CTRL_U,  # CTRL-U
                b"\x16": self.handle_CTRL_V,  # CTRL-V
                b"\x1b": self.handle_ESC,  # ESC
            }
        )

    def displayMOTD(self) -> None:
        try:
            self.terminal.write(self.fs.file_contents("/etc/motd"))
        except Exception:
            pass

    def timeoutConnection(self) -> None:
        """
        this logs out when connection times out
        """
        assert self.terminal is not None
        self.terminal.write(b"timed out waiting for input: auto-logout\n")
        HoneyPotBaseProtocol.timeoutConnection(self)

    def connectionLost(self, reason: failure.Failure = connectionDone) -> None:
        HoneyPotBaseProtocol.connectionLost(self, reason)
        recvline.HistoricRecvLine.connectionLost(self, reason)
        self.keyHandlers = {}

    def initializeScreen(self) -> None:
        """
        Overriding super to prevent terminal.reset()
        """
        self.setInsertMode()

    def call_command(self, pp, cmd, *args):
        self.pp = pp
        self.setTypeoverMode()
        HoneyPotBaseProtocol.call_command(self, pp, cmd, *args)

    def characterReceived(self, ch, moreCharactersComing):
        """
        Easier way to implement password input?
        """
        self.resetTimeout()  # Reset the idle timeout
        if self.mode == "insert":
            self.lineBuffer.insert(self.lineBufferIndex, ch)
        else:
            self.lineBuffer[self.lineBufferIndex : self.lineBufferIndex + 1] = [ch]
        self.lineBufferIndex += 1
        if not self.password_input:
            assert self.terminal is not None
            self.terminal.write(ch)

    def handle_RETURN(self) -> None:
        if len(self.cmdstack) == 1:
            if self.lineBuffer:
                self.historyLines.append(b"".join(self.lineBuffer))
            self.historyPosition = len(self.historyLines)
        recvline.RecvLine.handle_RETURN(self)

    def handle_NUL(self) -> None:
        """
        Ignore NUL bytes. They are routine NVT traffic: telnet clients send
        CR NUL as the line ending per RFC 854, and some clients send stray NUL
        bytes as keepalive padding.
        """

    def handle_CTRL_C(self) -> None:
        if self.cmdstack:
            self.cmdstack[-1].handle_CTRL_C()

    def handle_CTRL_D(self) -> None:
        # CTRL-D at the terminal signals end-of-file on stdin.
        self.eofReceived()

    def handle_TAB(self) -> None:
        if self.cmdstack:
            self.cmdstack[-1].handle_TAB()

    def handle_CTRL_K(self) -> None:
        assert self.terminal is not None
        self.terminal.eraseToLineEnd()
        self.lineBuffer = self.lineBuffer[0 : self.lineBufferIndex]

    def handle_CTRL_L(self) -> None:
        """
        Handle a 'form feed' byte - generally used to request a screen
        refresh/redraw.
        """
        assert self.terminal is not None
        self.terminal.eraseDisplay()
        self.terminal.cursorHome()
        self.drawInputLine()

    def handle_CTRL_U(self) -> None:
        assert self.terminal is not None
        for _ in range(self.lineBufferIndex):
            self.terminal.cursorBackward()
            self.terminal.deleteCharacter()
        self.lineBuffer = self.lineBuffer[self.lineBufferIndex :]
        self.lineBufferIndex = 0

    def handle_CTRL_V(self) -> None:
        pass

    def handle_ESC(self) -> None:
        pass


class HoneyPotInteractiveTelnetProtocol(HoneyPotInteractiveProtocol):
    """
    Specialized HoneyPotInteractiveProtocol that provides Telnet specific
    overrides.
    """

    def __init__(self, avatar):
        HoneyPotInteractiveProtocol.__init__(self, avatar)

    def getProtoTransport(self):
        """
        Due to protocol nesting differences, we need to override how we grab
        the proper transport to access underlying Telnet information.
        """
        return self.terminal.transport.session.transport
