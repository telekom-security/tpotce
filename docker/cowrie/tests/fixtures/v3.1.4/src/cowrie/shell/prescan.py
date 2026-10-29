# SPDX-FileCopyrightText: 2026 Michel Oosterhof <michel@oosterhof.net>
#
# SPDX-License-Identifier: BSD-3-Clause

# ABOUTME: Linear pre-scan of shell input that cuts out here-document bodies
# ABOUTME: and tells whether a quote, substitution or heredoc is still open.

"""
A single pass over shell input, run before the Lark grammar in
``bashparse``, for the two things a context-free grammar cannot do well:

* Here-documents. The body of ``cat <<EOF`` is whatever follows the next
  newline up to a line holding the delimiter, which depends on an earlier
  word (issue #40597 is why the grammar keeps every construct unambiguous).
  The scanner lifts each body out into a :class:`HereDoc`, leaving an empty
  line in its place so line numbers are unchanged, and records the cuts so
  a span of the stripped text can be mapped back onto the original.
* Incomplete input. A shell reading line by line must know whether the text
  so far still has an open quote, ``$(``, ``${``, backtick, a trailing
  backslash or an unfinished heredoc body before it asks the grammar, which
  cannot tell an unterminated quote from any other error.

The scanner mirrors the grammar's lexing of quotes and escapes exactly (see
the cross-references in the code): where the two disagree, a line could be
"complete" for one and not the other.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Frames of the context stack, named by the character that closes each
# (a subshell and a command substitution both close at ")", but bash
# reports an unfinished one differently, so they are kept apart).
_SQ = "'"
_DQ = '"'
_BQ = "`"
_PAREN = "("
_CMDSUB = ")"
_BRACE = "}"

# Characters a here-document delimiter word ends at (unless quoted).
_DELIMITER_END = " \t\r\n;|&<>()"
# After one of these a "#" starts a comment and a digit run can be an fd.
_WORD_BREAK = " \t\r\n;|&<>()"


@dataclass
class HereDoc:
    """A here-document attached to a command's redirection list.

    ``body`` is the raw text between the ``<<WORD`` line and the delimiter
    line, each line ending in a newline, with leading tabs removed for
    ``<<-``. ``expand`` is True when the delimiter was unquoted, so the body
    undergoes parameter and command substitution when the command runs.
    ``delimiter`` is the word after quote removal and ``lineno`` the 1-based
    line of the ``<<`` operator, for the warning bash prints when
    ``terminated`` is False (EOF came before the delimiter). ``fd`` is the
    descriptor the operator names (``3<<EOF``), filled in by the parser.
    """

    body: str
    expand: bool
    delimiter: str
    lineno: int
    fd: int = 0
    terminated: bool = True


@dataclass
class ScanResult:
    """What :func:`scan` found.

    ``stripped`` is the input with every heredoc body and delimiter line
    replaced by an empty line; ``heredocs`` maps the position of each ``<<``
    in it to its body; ``cuts`` lists ``(stripped_pos, removed)``: ``removed``
    characters of the original were dropped just before ``stripped_pos``.
    ``incomplete`` is None, or what the input still needs: the closer of an
    open ``"``, ``'``, ````` (reported as that character), ``$(`` (``)``) or
    ``${`` (``}``); ``(`` for an open subshell, which bash reports like an
    unfinished compound command; ``\\`` for a trailing backslash; or
    ``heredoc`` for a body without its delimiter. ``incomplete_line`` is
    where bash reports it: the opener's line for quotes, backticks and
    ``${``, the last line for the others. ``truncated`` is set when a body
    ran past the size cap and lost lines.
    """

    stripped: str
    heredocs: dict[int, HereDoc] = field(default_factory=dict)
    cuts: list[tuple[int, int]] = field(default_factory=list)
    incomplete: str | None = None
    incomplete_line: int = 0
    truncated: bool = False


@dataclass
class _Pending:
    """A ``<<`` seen on the current line, whose body starts after it."""

    pos: int  # of "<<" in the stripped text
    delimiter: str
    expand: bool
    strip_tabs: bool
    lineno: int


class _Scanner:
    def __init__(self, text: str, max_heredoc: int) -> None:
        self.text = text
        self.max_heredoc = max_heredoc
        self.n = len(text)
        self.i = 0
        self.line = 1
        self.stack: list[tuple[str, int]] = []  # (closer, line opened on)
        self.pending: list[_Pending] = []
        self.heredocs: dict[int, HereDoc] = {}
        self.cuts: list[tuple[int, int]] = []
        self.pieces: list[str] = []  # the stripped text, in order
        self.kept_from = 0  # start of the original text not yet copied out
        self.removed = 0  # characters cut so far: stripped pos = pos - removed
        self.word_start = True
        self.trailing_backslash = False
        self.truncated = False

    def run(self) -> ScanResult:
        text, n = self.text, self.n
        while self.i < n:
            frame = self.stack[-1][0] if self.stack else None
            if frame == _DQ:
                self._in_double_quotes()
            elif frame in (_SQ, _BQ, _BRACE):
                self._in_literal()
            else:
                self._in_command()
        self.pieces.append(text[self.kept_from :])
        # A "<<WORD" with no newline after it yet: its body is still to come.
        for spec in self.pending:
            self.heredocs[spec.pos] = HereDoc(
                body="",
                expand=spec.expand,
                delimiter=spec.delimiter,
                lineno=spec.lineno,
                terminated=False,
            )
        self.pending.clear()

        incomplete: str | None = None
        incomplete_line = 0
        if self.stack:
            closer, opened = self.stack[-1]
            incomplete = closer
            if closer == _CMDSUB:
                # bash names the line it hit EOF on, which is one past the
                # last unless that already ended in a newline.
                incomplete_line = self.line + (0 if text.endswith("\n") else 1)
            elif closer == _PAREN:
                incomplete_line = self.line
            else:
                incomplete_line = opened
        elif any(not h.terminated for h in self.heredocs.values()):
            incomplete = "heredoc"
            incomplete_line = self.line
        elif self.trailing_backslash:
            incomplete = "\\"
            incomplete_line = self.line
        return ScanResult(
            stripped="".join(self.pieces),
            heredocs=self.heredocs,
            cuts=self.cuts,
            incomplete=incomplete,
            incomplete_line=incomplete_line,
            truncated=self.truncated,
        )

    # -- one character in each context ---------------------------------

    def _newline(self) -> None:
        """Past a newline: count it and, with heredocs pending on the line
        just ended, lift their bodies out of the lines that follow."""
        self.line += 1
        self.i += 1
        self.word_start = True
        if self.pending:
            self._collect_bodies()

    def _in_literal(self) -> None:
        """Inside single quotes, backticks or ``${...}``: only the closer
        ends the frame. Grammar: SQ: /'[^']*'/, BQ_LITERAL: /[^`]+/ and
        dollar_brace: "${" BRACE_NAME "}" -- nothing in them is escaped."""
        c = self.text[self.i]
        if c == self.stack[-1][0]:
            self.stack.pop()
        elif c == "\n":
            # Inside a word: the command line has not ended, so no
            # here-document body starts here.
            self.line += 1
        self.i += 1

    def _in_double_quotes(self) -> None:
        # Grammar: dq / DQ_ESC -- a backslash escapes only $ ` " \.
        text, c = self.text, self.text[self.i]
        if c == "\\":
            self.i += 2 if text[self.i + 1 : self.i + 2] in ("$", "`", '"', "\\") else 1
            return
        if c == "$" and text.startswith("$(", self.i):
            self.stack.append((_CMDSUB, self.line))
            self.i += 2
            return
        if c == _BQ:
            self.stack.append((_BQ, self.line))
        elif c == _DQ:
            self.stack.pop()
        elif c == "\n":
            self.line += 1
        self.i += 1

    def _in_command(self) -> None:
        """Unquoted text at the top level or inside ``(...)`` / ``$(...)``."""
        text, i, n = self.text, self.i, self.n
        c = text[i]
        if c == "\\":
            # Grammar: ESC: /\\./ and the \<newline> continuation in _WS. One
            # escaping the input's last newline continues onto a line not yet
            # read; a bare backslash at the very end the grammar rejects.
            if i + 1 >= n:
                self.i = n
                return
            if text[i + 1] == "\n":
                skip = 2
            elif text[i + 1] == "\r" and text[i + 2 : i + 3] == "\n":
                skip = 3
            else:
                self.i += 2
                self.word_start = False
                return
            self.line += 1
            self.i += skip
            self.word_start = True
            if self.i >= n:
                self.trailing_backslash = True
            return
        if c == "\n":
            self._newline()
            return
        if c == "#" and self.word_start:
            # Grammar: _COMMENT / HASH_LITERAL -- a comment only at a word start.
            end = text.find("\n", i)
            self.i = n if end == -1 else end
            return
        if c == "<" and text.startswith("<<", i):
            if text.startswith("<<<", i):
                # A here-string: its word is ordinary.
                self.i += 3
                self.word_start = True
                return
            self._heredoc_operator()
            return
        if c == _SQ or c == _DQ or c == _BQ:
            self.stack.append((c, self.line))
            self.word_start = False
        elif c == "$" and text.startswith("$(", i):
            self.stack.append((_CMDSUB, self.line))
            self.i += 2
            self.word_start = False
            return
        elif c == "$" and text.startswith("${", i):
            self.stack.append((_BRACE, self.line))
            self.i += 2
            self.word_start = False
            return
        elif c == "(":
            self.stack.append((_PAREN, self.line))
            self.word_start = True
        elif c == ")":
            # Closes a group; otherwise it is a case pattern's close, which
            # the grammar pairs for itself.
            if self.stack and self.stack[-1][0] in (_PAREN, _CMDSUB):
                self.stack.pop()
            self.word_start = True
        elif c in _WORD_BREAK:
            self.word_start = True
        else:
            self.word_start = False
        self.i += 1

    # -- here-documents ---------------------------------------------------

    def _heredoc_operator(self) -> None:
        """At ``<<``: read the operator and its delimiter word, and note that
        a body follows the current line. The delimiter word is read with the
        shell's quoting rules; any quoting in it turns off expansion."""
        text, n = self.text, self.n
        op_pos = self.i
        i = op_pos + 2
        strip_tabs = False
        if i < n and text[i] == "-":
            strip_tabs = True
            i += 1
        while i < n and text[i] in " \t":
            i += 1
        parts: list[str] = []
        quoted = False
        while i < n and text[i] not in _DELIMITER_END:
            c = text[i]
            if c == "\\":
                quoted = True
                parts.append(text[i + 1 : i + 2])
                i += 2
            elif c == _SQ:
                quoted = True
                end = text.find(_SQ, i + 1)
                if end == -1:
                    # An unterminated quote: let the quote scan report it.
                    self.stack.append((_SQ, self.line))
                    self.i = n
                    return
                parts.append(text[i + 1 : end])
                i = end + 1
            elif c == _DQ:
                quoted = True
                i += 1
                while i < n and text[i] != _DQ:
                    if text[i] == "\\" and text[i + 1 : i + 2] in ("$", "`", '"', "\\"):
                        i += 1
                    parts.append(text[i])
                    i += 1
                if i >= n:
                    self.stack.append((_DQ, self.line))
                    self.i = n
                    return
                i += 1
            else:
                parts.append(c)
                i += 1
        delimiter = "".join(parts)
        if delimiter or quoted:
            self.pending.append(
                _Pending(
                    pos=op_pos - self.removed,
                    delimiter=delimiter,
                    expand=not quoted,
                    strip_tabs=strip_tabs,
                    lineno=self.line,
                )
            )
        # With no word at all the grammar reports the syntax error.
        self.i = i
        self.word_start = True

    def _cut(self, start: int, end: int) -> None:
        """Drop ``text[start:end]`` from the stripped output."""
        if end <= start:
            return
        self.pieces.append(self.text[self.kept_from : start])
        self.kept_from = end
        self.cuts.append((start - self.removed, end - start))
        self.removed += end - start

    def _collect_bodies(self) -> None:
        """Read the body of each pending heredoc, in order, from the lines
        starting at the current position; each ends at the first line equal
        to its delimiter, or at the end of the input."""
        text, n = self.text, self.n
        for spec in self.pending:
            lines: list[str] = []
            size = 0
            terminated = False
            while self.i < n:
                start = self.i
                end = text.find("\n", start)
                has_newline = end != -1
                if not has_newline:
                    end = n
                line = text[start:end]
                if spec.strip_tabs:
                    line = line.lstrip("\t")
                # The line goes, its newline stays: line numbers are unchanged.
                self._cut(start, end)
                self.i = end + 1 if has_newline else n
                if has_newline:
                    self.line += 1
                if line == spec.delimiter:
                    terminated = True
                    break
                if size + len(line) + 1 <= self.max_heredoc:
                    lines.append(line + "\n")
                    size += len(line) + 1
                else:
                    self.truncated = True
            self.heredocs[spec.pos] = HereDoc(
                body="".join(lines),
                expand=spec.expand,
                delimiter=spec.delimiter,
                lineno=spec.lineno,
                terminated=terminated,
            )
        self.pending.clear()


def scan(text: str, *, max_heredoc: int) -> ScanResult:
    """Scan ``text`` in one pass: lift out here-document bodies (each capped
    at ``max_heredoc`` characters) and report whether the input is still
    lexically open. See :class:`ScanResult`."""
    return _Scanner(text, max_heredoc).run()
