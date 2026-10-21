# SPDX-FileCopyrightText: 2026 Michel Oosterhof <michel@oosterhof.net>
#
# SPDX-License-Identifier: BSD-3-Clause

# ABOUTME: Lark-based bash-subset parser that turns a command line into a list
# (excerpt of v3.1.3: imports, ShellContext and BashParser._special_param)

from __future__ import annotations

import contextlib
import gc
import re
import signal
import threading



class ShellContext(Protocol):
    """What the evaluator needs from the live shell to evaluate words."""

    def get_variable(self, name: str) -> str | None:
        """Return the value of a shell variable, or None if unset."""
        ...

    def get_status(self) -> str:
        """Return ``$?`` -- the last command's exit status, as a string."""
        ...

    def command_substitution(self, source: str) -> Awaitable[str]:
        """Execute ``source``; the awaitable completes with its captured stdout
        (trailing newlines stripped) once every command in it has finished."""
        ...


class BashParser:
    """Parse a command line into a list of statements for the shell to run."""

    def _special_param(self, node: Tree) -> str | None:
        """Expand a special parameter ($?, $@, $#, ...) to its string value, or
        None for an ordinary ``$name`` reference the caller should look up.

        ``$?`` is the last exit status. ``$@`` / ``$#`` / ``$*`` resolve to the
        positional parameters the shell sets while running a function; outside a
        function they are unset and kept verbatim. Other specials are kept
        verbatim.
        """
        _, special = self._var_name(node)
        if special is None:
            return None
        if special == "?":
            return self.context.get_status()
        if special in ("@", "#", "*"):
            value = self.context.get_variable(special)
            return value if value is not None else self._leaf_value(node)
        return self._leaf_value(node)

