# SPDX-FileCopyrightText: 2025-2026 Michel Oosterhof <michel@oosterhof.net>
#
# SPDX-License-Identifier: BSD-3-Clause

# ABOUTME: Parses shell commands including tokenization, redirection operators, and variable expansion.
# ABOUTME: Handles FD redirections like 2>/dev/null, stdin redirects, and command substitution.

from __future__ import annotations

import re
from typing import Any, Self

# Pre-compiled regex for extracting redirection operators
_REDIR_OP_RE = re.compile(r"^(\d*)(>>|>&|>|<)(.*)$")


class Field(str):
    """
    A field produced by expanding a shell word. It is always an argument,
    never a redirection operator, whatever its text: in bash only an
    unquoted operator redirects, so ``echo '>x'`` prints ``>x``.
    """


class HereDocBody(str):
    """
    The expanded body of a here-document (``<<EOF``) or here-string
    (``<<<word``), standing in the token list for the operator and its
    word. It is never an argument: :meth:`CommandParser.parse_redirections`
    turns it into a ``heredoc`` redirection feeding ``fd`` (0 unless the
    operator named one, as in ``3<<EOF``).
    """

    fd: int

    def __new__(cls, text: str, fd: int = 0) -> Self:
        body = super().__new__(cls, text)
        body.fd = fd
        return body


class CommandParser:
    """
    Handles parsing of shell commands, including tokenization, redirection,
    and variable expansion.
    """

    def parse_redirections(
        self, arguments: list[str]
    ) -> tuple[list[str], list[dict[str, Any]]]:
        """
        Parse arguments for redirections and return cleaned args and ordered redirection operations.
        """
        cleaned: list[str] = []
        ops: list[dict[str, Any]] = []

        i = 0
        while i < len(arguments):
            tok = arguments[i]
            next_token = arguments[i + 1] if (i + 1) < len(arguments) else None

            if isinstance(tok, HereDocBody):
                # The body is attacker text decoded with replacement, so it
                # always encodes.
                ops.append({"type": "heredoc", "fd": tok.fd, "data": tok.encode()})
                i += 1
                continue

            if isinstance(tok, Field):
                cleaned.append(tok)
                i += 1
                continue

            amp = self._amp_redirect(tok)
            if amp is not None:
                consumed = self._apply_amp_redirection(
                    amp, next_token, ops, cleaned, tok
                )
                i += consumed
                continue

            op, fd, inline_target = self._extract_redir_op(tok)

            if not op and tok in (">", ">>", "<", ">&"):
                op = tok

            if op:
                consumed = self._apply_redirection(
                    op,
                    fd,
                    inline_target,
                    next_token,
                    ops,
                    cleaned,
                    tok,
                )
                if consumed:
                    i += consumed
                    continue

            cleaned.append(tok)
            i += 1

        return cleaned, ops

    def _amp_redirect(self, token: str) -> tuple[bool, str] | None:
        """Parse a ``&>`` / ``&>>`` token into (append, inline target).

        Returns None if the token is not an ``&>`` redirection. ``&>`` truncates
        and ``&>>`` appends; both send stdout and stderr to the target.
        """
        if token.startswith("&>>"):
            return True, token[3:]
        if token.startswith("&>"):
            return False, token[2:]
        return None

    def _apply_amp_redirection(
        self,
        amp: tuple[bool, str],
        next_token: str | None,
        ops: list[dict[str, Any]],
        cleaned: list[str],
        raw_token: str,
    ) -> int:
        """Redirect both stdout and stderr (``&>file`` == ``>file 2>&1``)."""
        append_flag, inline_target = amp
        target = inline_target or next_token
        if target is None:
            cleaned.append(raw_token)
            return 1
        ops.append({"type": "file", "fd": 1, "target": target, "append": append_flag})
        ops.append({"type": "dup", "fd": 2, "target": 1})
        return 1 if inline_target else 2

    def _extract_redir_op(self, token: str) -> tuple[str | None, int | None, str]:
        """Parse a combined redirection token into (op, fd, inline target)."""
        match = _REDIR_OP_RE.match(token)
        if not match:
            return None, None, ""
        fd_text, op, inline_target = match.groups()
        fd = int(fd_text) if fd_text else None
        return op, fd, inline_target

    def _apply_redirection(
        self,
        op: str,
        fd: int | None,
        inline_target: str,
        next_token: str | None,
        ops: list[dict[str, Any]],
        cleaned: list[str],
        raw_token: str,
    ) -> int:
        """Handle one redirection token and append it to the ops list."""
        if op in (">", ">>"):
            target_fd = 1 if fd is None else fd
            append_flag = op == ">>"
            target = inline_target or next_token
            if target is None:
                cleaned.append(raw_token)
                return 1

            ops.append(
                {
                    "type": "file",
                    "fd": target_fd,
                    "target": target,
                    "append": append_flag,
                }
            )
            return 1 if inline_target else 2

        if op == "<":
            source_fd = 0 if fd is None else fd
            target = inline_target or next_token
            if target is None:
                cleaned.append(raw_token)
                return 1
            ops.append({"type": "stdin", "fd": source_fd, "target": target})
            return 1 if inline_target else 2

        if op == ">&":
            target = inline_target or next_token
            if target is None:
                cleaned.append(raw_token)
                return 1

            consume = 1 if inline_target else 2
            source_fd = 1 if fd is None else fd

            if target == "-":
                # Close FD
                # Not fully supported yet, but we can parse it
                pass
            elif target.isdigit():
                # FD duplication: source_fd = target_fd
                ops.append({"type": "dup", "fd": source_fd, "target": int(target)})
            else:
                # Handle `>&`. Standard `sh` expects a digit or `-` after `>&` for file descriptor duplication.
                # Bash allows `>&file` or `&>file` to redirect both stdout and stderr to a file.
                # However, Cowrie's previous implementation strictly required a digit for `>&`.
                # If the target is not a digit, we treat it as a normal argument rather than a redirection
                # to maintain backward compatibility and avoid ambiguous parsing.
                cleaned.append(raw_token)
                return 1

            return consume

        return 0
