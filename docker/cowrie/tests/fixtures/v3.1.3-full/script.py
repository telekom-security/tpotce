# SPDX-FileCopyrightText: 2026 Michel Oosterhof <michel@oosterhof.net>
#
# SPDX-License-Identifier: BSD-3-Clause

# ABOUTME: Shared helpers for running an uploaded file as a shell script:
# ABOUTME: binary-vs-script detection and executing the contents through the shell.

"""
Running uploaded code is a core honeypot job: a second-stage downloader is
usually a shell script dropped by ``wget`` / ``curl`` / ``tftp`` and then run as
``./x``, ``sh x`` or via a shebang. This module concentrates the two decisions
that path needs, so ``bash``/``sh`` and direct ``./file`` execution behave the
same:

* :func:`is_executable_binary` -- tell an ELF/PE/Mach-O or otherwise binary
  payload from a text script, so we refuse to "run" a binary instead of feeding
  its bytes to the parser (as both a real kernel and ``bash`` do).
* :func:`run_script_file` -- read the file from the emulated filesystem and run
  its contents through a fresh :class:`~cowrie.shell.honeypot.HoneyPotShell`.
  The whole text is handed to the parser unchanged: the grammar treats newlines
  as separators and ``#`` lines (including the shebang) as comments, so loops,
  conditionals and functions that span lines are emulated rather than flattened.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cowrie.shell.bashparse import max_input_size
from cowrie.shell.fs import FileNotFound
from cowrie.shell.honeypot import HoneyPotShell

if TYPE_CHECKING:
    from cowrie.shell.command import HoneyPotCommand

# How deep one script may invoke another (a script that runs a script ...),
# guarding against runaway or self-referential droppers.
MAX_SCRIPT_DEPTH = 5

# Magic numbers for common executable formats that are clearly not scripts.
_BINARY_MAGIC = (
    b"\x7fELF",  # ELF (Linux, the usual malware case)
    b"MZ",  # DOS / PE (Windows)
    b"\xca\xfe\xba\xbe",  # Mach-O universal / Java class
    b"\xfe\xed\xfa\xce",  # Mach-O 32-bit
    b"\xfe\xed\xfa\xcf",  # Mach-O 64-bit
    b"\xcf\xfa\xed\xfe",  # Mach-O 64-bit (LE)
    b"\xce\xfa\xed\xfe",  # Mach-O 32-bit (LE)
)

# How much of the file to inspect for non-UTF-8 decodability. The NUL scan
# covers the whole file (see below); the UTF-8 check samples the head, which is
# enough to tell a text script from packed/encrypted bytes.
_SAMPLE_BYTES = 8192


def is_executable_binary(contents: bytes) -> bool:
    """Return True if ``contents`` looks like a binary executable, not a script.

    A shell script is text: ASCII, or otherwise valid UTF-8. So anything with
    a known executable magic number, a NUL byte, or bytes that are not valid
    UTF-8 is treated as a binary -- which covers ELF/PE/Mach-O droppers and
    packed payloads while leaving non-ASCII (e.g. UTF-8 comment) scripts alone.

    The NUL scan looks at the whole file, not just the head: a self-extracting
    dropper is a text header with a binary blob appended, so the NUL that gives
    it away can sit well past any sampled prefix.
    """
    if not contents:
        return False
    if contents.startswith(_BINARY_MAGIC):
        return True

    if b"\x00" in contents:
        return True

    try:
        contents[:_SAMPLE_BYTES].decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def run_script_file(
    command: HoneyPotCommand,
    path: str,
    *,
    name: str,
    not_found_message: str,
    binary_message: str,
) -> None:
    """Read ``path`` from the emulated filesystem and run it as a shell script.

    ``name`` is the script as invoked, which names it in its own errors.
    ``not_found_message`` / ``binary_message`` are the errors to write (with the
    caller's preferred ``bash:`` / ``-bash:`` prefix) when the file is missing or
    is an executable binary. The command's ``exit_code`` is set to the status of
    the last command the script ran.
    """
    protocol = command.protocol
    depth = getattr(protocol, "_script_depth", 0)
    if depth >= MAX_SCRIPT_DEPTH:
        command.errorWrite(
            f"{command.shell.error_prefix()}{name}: too many levels of recursion\n"
        )
        return

    try:
        contents = command.fs.file_contents(path)
    except (FileNotFound, FileNotFoundError):
        command.errorWrite(not_found_message)
        command.exit_code = 127
        return

    # A file past the input cap is refused like a binary: parsing it would be
    # superlinearly expensive (see max_input_size), and a legitimate shell
    # script that large does not occur as a honeypot payload -- oversized
    # "scripts" are binaries or web pages fetched from a dead payload URL.
    if len(contents) > max_input_size() or is_executable_binary(contents):
        command.errorWrite(binary_message)
        command.exit_code = 126
        return

    text = contents.decode("utf-8", errors="replace")
    if not text.strip():
        return

    protocol._script_depth = depth + 1
    try:
        shell = HoneyPotShell(protocol, interactive=False)
        # Errors in a script name it as invoked, with the line: "./x.sh: line 3:".
        shell.name = name
        shell.syntax_label = None
        protocol.cmdstack.append(shell)
        # Hand the whole script to the parser: newlines separate statements and
        # "#" lines (the shebang included) are comments, so flow control that
        # spans lines is emulated rather than joined onto one line.
        shell.lineReceived(text)
        command.exit_code = shell.last_exit_code
        # The script shell removes itself from cmdstack in _finish() once its
        # queue is drained -- which may be now (all-synchronous script) or later
        # (an async wget/curl in the script keeps it mid-stack until it resumes
        # and drains). A pop() here would remove whatever is on top, which is the
        # in-flight async command, not this shell.
    finally:
        protocol._script_depth = depth
