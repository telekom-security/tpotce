# SPDX-FileCopyrightText: 2026 Michel Oosterhof <michel@oosterhof.net>
#
# SPDX-License-Identifier: BSD-3-Clause

# ABOUTME: Lark-based bash-subset parser that turns a command line into a list
# ABOUTME: of statements (token lists, subshells, syntax errors) for the shell.

"""
A small Lark grammar and evaluator that tokenises a command line and resolves
its quoting, redirection, command substitution and subshells for the shell in
``honeypot.py``.

The parser produces a list of :class:`Statement` objects -- simple commands and
the compound commands ``for`` / ``if`` / ``while`` / ``until`` / ``case``, brace
groups, subshells and function definitions -- each carrying the operator that
joins it to the previous one (``;`` / ``&&`` / ``||``) and still-unevaluated word
trees. Words are expanded only when a statement is about to run (see
:meth:`BashParser.evaluate`), so the shell can interleave parsing and execution
like a real shell. The control operators a real shell cares about — ``|`` and
the redirection operators — pass through as their own string tokens so the
existing ``CommandParser`` / ``runCommand`` machinery consumes the result
unchanged.

There is a single tokeniser for the whole language: the Lark grammar lexes a
line (or a whole multi-line script) into word trees and control tokens, and the
recursive descent in :meth:`BashParser._parse_list` recognises reserved words
only at a command position to build the compound structure, exactly as a real
shell parses. The one clause the grammar recognises itself is ``case``: its
pattern close is the only ``)`` bash allows without a matching ``(``, and
keeping every paren paired is what makes a line parse in linear time.
Newlines separate statements like ``;`` so a script is parsed directly rather
than line-by-line.

The grammar models the subset of bash that Cowrie emulates: lists separated by
``;`` / newline / ``&&`` / ``||``, pipelines, simple redirections, single/double
quoting, ``$VAR`` / ``${VAR}`` expansion, command substitution (``$(...)`` and
backticks), subshells (``(...)``), the compound commands above, and function
definitions.

Known deviations from standard bash (none are emulated yet):

* A trailing ``&`` is treated as a literal argument, not a background job.
* No word expansions beyond ``$VAR`` / ``${VAR}``: tilde (``~``), brace
  (``{a,b}`` / ``{1..3}``), arithmetic (``$((...))``), the parameter
  expansion operators (``${x:-y}``, ``${#x}``, ``${x/a/b}`` ...), and ANSI-C
  quoting (``$'...'``) are all passed through literally.
* Word splitting always uses the default ``IFS`` (space, tab, newline); an
  assigned ``IFS`` is ignored.
* Pathname expansion (globbing of ``*`` / ``?`` / ``[...]``) is left to the
  individual command implementations, not done by the parser; ``case`` patterns
  are matched with fnmatch.
* Here-documents (``<<EOF``) and here-strings (``<<<``) are not supported; the
  operators are tokenised as plain ``<`` redirections.
* A compound command nested inside ``$(...)`` is not captured (its output is
  empty); command substitution captures simple commands and subshells only.
* ``$@`` / ``$#`` / ``$*`` expand to the positional parameters only while a
  function is running; elsewhere they are kept literally. Other special
  parameters (``$!``, ``$$`` ...) other than ``$?`` are kept literally.

Input the grammar cannot parse at all (e.g. an unterminated quote) surfaces as
a syntax error, exactly as the previous implementation degraded on input it
could not handle.
"""

from __future__ import annotations

import contextlib
import gc
import re
import signal
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Awaitable, Iterator

from lark import Lark, Token, Tree
from lark.exceptions import LarkError, UnexpectedCharacters
from twisted.logger import Logger

from cowrie.core.config import CowrieConfig
from cowrie.shell.parser import Field

# Grammar for the supported bash subset. Whitespace is significant as a word
# separator, so it is matched explicitly rather than ignored: adjacent atoms
# with no whitespace between them form a single word ("a"$x'b' -> one word).
#
# The grammar is a single tokeniser for the whole language: it emits a flat
# stream of word trees, "(...)" and case groups, and control tokens
# (separators, pipes, redirections, newlines). The structure above a simple
# command -- &&/|| lists, pipelines, and the compound commands
# for/if/while/until, brace groups and function definitions -- is recognised
# by the recursive descent in :meth:`BashParser._parse_list`, which reads
# reserved words only at a command position, exactly as a real shell does.
# Keeping one tokeniser means the script-level constructs reuse the same
# quoting, expansion and redirection lexing as a single interactive line.
#
# The grammar is unambiguous: every line has exactly one parse. That is what
# keeps the Earley parse linear in the input size (issue #40597), and
# test_bashparse asserts it with an explicit-ambiguity parse of sample lines.
_GRAMMAR = r"""
// A line is contents (words, groups, comments) and operators in any order.
// Whitespace is REQUIRED between two contents, so a run of atoms with no
// spaces is a single word (a"b"$c -> one word) while operators stay
// self-delimiting (echo a|b is three tokens); it is optional around an
// operator. Two left-recursive sequences -- _after_content and _after_op,
// named for what they end in -- give every whitespace run exactly one place
// to attach. (Left recursion: Earley handles it in linear time where a
// right-recursive list is quadratic.)
start: _WS? _seq?
_seq: (_after_content | _after_op) _WS?
_after_content: _content | _after_content _WS _arg | _after_op _WS? _content
// After another word, "esac" is an ordinary argument ("echo esac"). In command
// position it only ever closes a case (LITERAL never matches it), so a case
// body without a final ";;" cannot absorb the next "esac" as a command.
_arg: _content | esac_arg
esac_arg: ESAC -> word
_after_op: _op | _after_content _WS? _op | _after_op _WS? _op
_content: subshell | word | _funcdef | case_clause | _COMMENT
// A function definition's "()" is one token, so "f()", "f ()" and "f ( )"
// all read the same way, and the body may follow it directly ("f(){ ...; }").
_funcdef: word _WS? FUNC_PARENS _content?
FUNC_PARENS: /\([ \t]*\)/
_op: SEP | PIPE | AMP | IO_REDIR | REDIR | NEWLINE

// "(" only ever opens a group and ")" only ever closes one: a "(...)" is a
// subshell, a "(" that never closes is a syntax error, and the one ")" bash
// allows without a matching "(" -- the close of a case pattern like "x86*)"
// -- is recognised by the case_clause rule below. Keeping the parens out of
// _op matters for parse cost: Earley builds every reading of the input, and
// if either could stand alone, every "(...)" and "$(...)" could close at any
// later ")" on the line (issue #40597). A subshell has at least one command:
// "( )" is a syntax error in bash, and an empty "()" is FUNC_PARENS.
subshell: LPAR body RPAR
body: _WS? _seq

// "case WORD in [(]PAT[|PAT]...) BODY ;; ... esac". The keywords are only
// keywords here: elsewhere "case", "in" and "esac" lex as ordinary words.
case_clause: CASE _WS word _WS IN _blank* case_item* ESAC
case_item: (LPAR _WS?)? case_patterns _WS? RPAR start (DSEMI _blank*)?
case_patterns: word (_WS? PIPE _WS? word)*
_blank: _WS | NEWLINE
CASE: /case(?=\s)/
IN: /in(?=\s)/
ESAC: /esac(?![^\s;&|<>()])/

// A "#" starts a comment only at a word start (see _COMMENT), so a LITERAL
// never begins with one; inside a word ("a#b", "''#x") it is ordinary text.
word: _atom (_atom | HASH_LITERAL)*
_atom: sq | dq | cmdsub | backtick | dollar_brace | dollar_var | ESC | BARE_DOLLAR | LITERAL
HASH_LITERAL: /#[^ \t\r\n|&;<>()$`'"\\]*/

// An explicit "$(" terminal: BARE_DOLLAR never matches before a "(".
cmdsub: CMDSUB_OPEN start ")"
CMDSUB_OPEN: "$("

backtick: "`" _bq_atom* "`"
_bq_atom: dollar_var | dollar_brace | BQ_LITERAL
BQ_LITERAL: /[^`]+/

dq: "\"" _dq_part* "\""
_dq_part: cmdsub | backtick | dollar_brace | dollar_var | DQ_ESC | DQ_TEXT | BARE_DOLLAR
DQ_TEXT: /[^"$`\\]+/
// A lone backslash is literal only where it escapes nothing, so "\\" has
// exactly one reading: one escaped backslash, never two literal ones. The
// backslash comes last in each class: Lark reads \\" as an escaped quote and
// would drop it from the class.
DQ_ESC: /\\[$`"\\]/ | /\\(?![$`"\\])/

sq: SQ
SQ: /'[^']*'/

dollar_brace: "${" BRACE_NAME "}"
BRACE_NAME: /[_a-zA-Z0-9]+/

dollar_var: DOLLAR_NAME | DOLLAR_SPECIAL
DOLLAR_NAME: /\$[_a-zA-Z0-9]+/
DOLLAR_SPECIAL: /\$[?@$#!*]/

ESC: /\\./

// A single-character operator never matches where a longer one starts
// (";;", "&&", "||", "&>"), so each operator has exactly one reading.
DSEMI: ";;"
SEP: "&&" | "||" | /;(?!;)/
PIPE: /\|(?!\|)/
AMP: /&(?![&>])/
// A redirection with a file descriptor directly attached to it ("2>", "2>&",
// "1>>"): one token, so the digit is a file descriptor, not an argument (a
// LITERAL never matches digits directly before "<" or ">"). A digit separated
// by whitespace ("2 >") stays an ordinary word, which is how bash tells the
// two apart.
IO_REDIR: /\d+(?:>>|>&|>|<)/
REDIR: />>|>&|&>>|&>|>(?![>&])|</

LPAR: "("
RPAR: ")"
// A newline separates statements like ";" (so a multi-line script is parsed
// directly); a backslash-newline is a line continuation and counts as
// whitespace (see _WS below).
NEWLINE: /\r?\n/

// A "$" that does not start an expansion: never where DOLLAR_NAME,
// DOLLAR_SPECIAL, "${" or "$(" would match, so a "$x" has one reading.
BARE_DOLLAR: /\$(?![_a-zA-Z0-9{(?@$#!*])/
// Not a "#" first (that is a comment), and not digits directly before a
// redirection operator (that is an IO_REDIR file descriptor, "2>&1").
LITERAL: /(?!#|\d+[<>]|esac(?=[ \t\r\n|&;<>()]|$))[^ \t\r\n|&;<>()$`'"\\]+/

// A "#" at a word start begins a comment.
_COMMENT: /#[^\r\n]*/

// Inline whitespace and line continuations only; a bare newline is NEWLINE.
_WS: /([ \t]|\\\r?\n)+/
"""

_parser = Lark(
    _GRAMMAR,
    start="start",
    parser="earley",
    lexer="dynamic",
    propagate_positions=True,
)


def max_input_size() -> int:
    """Maximum size of shell input fed to the parser in one call
    ([shell] max_input_size). The Earley parse cost grows superlinearly with
    input size, so unbounded attacker-controlled input (e.g. an HTML error
    page downloaded in place of a payload and run with ``sh x``) could block
    the reactor for hours and exhaust memory."""
    return CowrieConfig.getint("shell", "max_input_size", fallback=16384)


def gc_collect_threshold() -> int:
    """Input length in characters that triggers post-parse garbage collection.

    Lark's Earley parser builds cyclic intermediate structures. CPython's
    reference counting cannot reclaim those structures promptly, so collect
    after larger inputs instead of allowing a busy honeypot to accumulate
    them until a later generation-two collection.
    """
    return CowrieConfig.getint("shell", "gc_collect_threshold", fallback=512)


class ParseTimeoutError(Exception):
    """Raised when a shell parse exceeds ``parse_timeout_seconds``."""


def parse_timeout_seconds() -> float:
    """Maximum wall-clock time for one shell parse; zero disables the limit."""
    return CowrieConfig.getfloat("shell", "parse_timeout_seconds", fallback=10.0)


_HAS_PARSE_ALARM = all(
    hasattr(signal, name)
    for name in ("SIGALRM", "ITIMER_REAL", "getitimer", "setitimer")
)


def _raise_parse_timeout(signum: int, frame: object) -> None:
    raise ParseTimeoutError


@contextlib.contextmanager
def _parse_alarm(seconds: float) -> Iterator[None]:
    """Apply a POSIX wall-clock limit without disturbing an embedding app.

    Signals are process-global and Python only permits changing their handlers
    in the main thread. Cowrie uses the alarm only when SIGALRM has its default
    handler and no real-time alarm is already pending; otherwise the caller's
    signal handling takes precedence and parsing proceeds normally.
    """
    if (
        seconds <= 0
        or not _HAS_PARSE_ALARM
        or threading.current_thread() is not threading.main_thread()
    ):
        yield
        return

    if signal.getsignal(signal.SIGALRM) is not signal.SIG_DFL:
        yield
        return
    delay, interval = signal.getitimer(signal.ITIMER_REAL)
    if delay or interval:
        yield
        return

    previous_handler = signal.signal(signal.SIGALRM, _raise_parse_timeout)
    try:
        signal.setitimer(signal.ITIMER_REAL, seconds)
        try:
            yield
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
    finally:
        signal.signal(signal.SIGALRM, previous_handler)


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


@dataclass
class Command:
    """A simple command, structure only.

    ``items`` keeps the ordered word trees (still unevaluated) interleaved with
    the redirection operator strings (``>``, ``2>`` ...). Words are expanded
    against the live shell by :meth:`BashParser.evaluate` only when the command
    is about to run, so a same-line ``x=hi; echo $x`` sees the assignment.
    ``op`` is the operator that joins this statement to the previous one
    (``None`` / ``;`` / ``&&`` / ``||``); ``line`` is the source the word trees
    point into, and ``lineno`` the 1-based line of that source it starts on.
    """

    items: list[str | Tree] = field(default_factory=list)
    line: str = ""
    op: str | None = None
    lineno: int = 0


@dataclass
class Pipeline:
    """``cmd | cmd ...``: two or more stages, each any kind of command (a simple
    command, a ``(...)`` or ``{ ...; }`` group, a loop ...), as in a real
    shell. ``op`` joins the pipeline to the previous statement; the stages
    themselves carry no join operator.
    """

    stages: list[Statement] = field(default_factory=list)
    op: str | None = None


@dataclass
class Subshell:
    """A ``(...)`` group, holding its parsed (still unevaluated) inner statements.

    Cowrie does not emulate a subshell's isolated environment, so the caller
    runs these statements in order with the surrounding line. ``op`` joins this
    group to the previous statement.
    """

    statements: list[Statement] = field(default_factory=list)
    op: str | None = None
    redirections: Command | None = None


@dataclass
class BraceGroup:
    """A ``{ ...; }`` command group: its statements run in the current shell."""

    statements: list[Statement] = field(default_factory=list)
    op: str | None = None
    redirections: Command | None = None


@dataclass
class ForClause:
    """``for VAR in WORDS; do BODY; done``.

    ``items`` is a :class:`Command` whose words are the (unevaluated) ``in``
    list, expanded against the live shell when the loop runs.
    """

    var: str = ""
    items: Command | None = None
    body: list[Statement] = field(default_factory=list)
    op: str | None = None
    redirections: Command | None = None


@dataclass
class IfClause:
    """``if COND; then BODY; [elif COND; then BODY;]* [else BODY;] fi``.

    ``branches`` pairs each condition statement-list with its body; ``else_body``
    is the optional trailing ``else`` arm.
    """

    branches: list[tuple[list[Statement], list[Statement]]] = field(
        default_factory=list
    )
    else_body: list[Statement] | None = None
    op: str | None = None
    redirections: Command | None = None


@dataclass
class WhileClause:
    """``while COND; do BODY; done`` (or ``until`` when ``until`` is True)."""

    condition: list[Statement] = field(default_factory=list)
    body: list[Statement] = field(default_factory=list)
    until: bool = False
    op: str | None = None
    redirections: Command | None = None


@dataclass
class CaseClause:
    """``case WORD in PATTERN[|PATTERN]) BODY ;; ... esac``.

    ``word`` is a :class:`Command` carrying the (unevaluated) word to match;
    ``items`` pairs each list of raw glob patterns with its body.
    """

    word: Command | None = None
    items: list[tuple[list[str], list[Statement]]] = field(default_factory=list)
    op: str | None = None
    redirections: Command | None = None


@dataclass
class FunctionDef:
    """``name() { BODY; }`` -- registers ``name`` for later invocation."""

    name: str = ""
    body: list[Statement] = field(default_factory=list)
    op: str | None = None


@dataclass
class SyntaxError_:
    """A bash-style syntax error to report verbatim.

    TODO: the trailing underscore avoids shadowing the builtin SyntaxError.
    Consider renaming to ParseError for clarity (touches honeypot.py and the
    tests).
    """

    token: str
    # The 1-based line of the parsed input the error is on, and that line's
    # text, which a non-interactive bash quotes after the message.
    lineno: int = 0
    source: str = ""


Statement = (
    Command
    | Pipeline
    | Subshell
    | BraceGroup
    | ForClause
    | IfClause
    | WhileClause
    | CaseClause
    | FunctionDef
    | SyntaxError_
)

# The compound commands a redirection can follow ("(...) > f", "done 2>&1"),
# carrying it in their ``redirections`` field.
REDIRECTABLE = (Subshell, BraceGroup, ForClause, IfClause, WhileClause, CaseClause)

# Reserved words recognised only at a command position (the start of a
# statement). Anywhere else they are ordinary arguments, so ``echo done`` still
# prints "done", exactly as in bash.
_RESERVED = frozenset(
    {
        "for",
        "in",
        "do",
        "done",
        "if",
        "then",
        "elif",
        "else",
        "fi",
        "while",
        "until",
        "case",
        "esac",
        "function",
        "{",
        "}",
    }
)

# Reserved words that only ever close a construct: at the start of a statement
# that is not the one closing the construct being parsed, each is a syntax
# error, never a command name.
_CLOSERS = frozenset({"then", "elif", "else", "fi", "do", "done", "esac", "}"})

# Tokens that end a statement, and those that end one pipeline stage.
_STATEMENT_END = frozenset({"SEP", "NEWLINE"})
_STAGE_END = _STATEMENT_END | {"PIPE"}

_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# A word whose leading literal is NAME= is an assignment.
_ASSIGNMENT_PREFIX = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# Builtins whose NAME=value arguments are assignments, so not split.
_DECLARATION_BUILTINS = frozenset({"declare", "export", "local", "readonly", "typeset"})
# The characters an unquoted expansion splits on: bash's default IFS.
_IFS_WHITESPACE = " \t\n"
_UNQUOTED_EXPANSIONS = frozenset({"dollar_var", "dollar_brace", "cmdsub", "backtick"})


class _Cursor:
    """A forward cursor over the grammar's flat child stream."""

    def __init__(self, tokens: list[Tree | Token]) -> None:
        self.tokens = tokens
        self.pos = 0

    def peek(self, ahead: int = 0) -> Tree | Token | None:
        index = self.pos + ahead
        return self.tokens[index] if index < len(self.tokens) else None

    def next(self) -> Tree | Token:
        token = self.tokens[self.pos]
        self.pos += 1
        return token


class BashParser:
    """Parse a command line into a list of statements for the shell to run."""

    _log = Logger()

    def __init__(self, context: ShellContext) -> None:
        self.context = context

    # -- public API ---------------------------------------------------------

    def parse(self, line: str) -> list[Statement]:
        """
        Parse ``line`` into ordered statements. Raises nothing for input the
        grammar rejects: it returns a single :class:`SyntaxError_` with an
        empty token so the caller can emit the generic message, matching the
        previous "unexpected end of file" fallback.
        """
        timed_out = False
        try:
            with _parse_alarm(parse_timeout_seconds()):
                tree = _parser.parse(line)
        except UnexpectedCharacters as error:
            return [
                SyntaxError_(
                    token=self._unexpected_char(line, error), lineno=error.line
                )
            ]
        except LarkError:
            return [SyntaxError_(token="", lineno=self._end_line(line))]
        except ParseTimeoutError:
            timed_out = True
            self._log.warn(
                "Shell parse exceeded {timeout}s (input: {length} characters)",
                timeout=parse_timeout_seconds(),
                length=len(line),
            )
            return [SyntaxError_(token="", lineno=self._end_line(line))]
        finally:
            if timed_out or len(line) >= gc_collect_threshold():
                gc.collect()
        return self._split_statements(line, tree)

    @staticmethod
    def _end_line(line: str) -> int:
        """The line bash reports an unexpected end of input on: the one after
        the last."""
        return len(line.rstrip("\n").split("\n")) + 1

    @staticmethod
    def _node_line(node: Tree | Token | None) -> int:
        """The 1-based source line a grammar node starts on, or 0."""
        if isinstance(node, Token):
            return node.line or 0
        if isinstance(node, Tree) and not node.meta.empty:
            return node.meta.line
        return 0

    @staticmethod
    def _unexpected_char(line: str, error: UnexpectedCharacters) -> str:
        """The token bash names for input the grammar rejects outright: a
        ")", ";;" or "esac" with nothing to close. Anything else gets the
        generic "unexpected end of file"."""
        rest = line[error.pos_in_stream :]
        for token in (";;", ")"):
            if rest.startswith(token):
                return token
        if re.match(r"esac(?=[ \t\r\n|&;<>()]|$)", rest):
            return "esac"
        return ""

    # -- statement splitting ------------------------------------------------

    def _split_statements(self, line: str, tree: Tree) -> list[Statement]:
        """Recursive-descent parse of the flat token stream into statements."""
        cursor = _Cursor(list(tree.children))
        return self._parse_list(line, cursor, stop=frozenset())

    @staticmethod
    def _token_type(node: Tree | Token | None) -> str | None:
        return node.type if isinstance(node, Token) else None

    def _is_separator(self, node: Tree | Token | None) -> bool:
        return self._token_type(node) in ("SEP", "NEWLINE")

    def _separator_op(self, node: Tree | Token) -> str:
        """The join operator a separator implies: && / || keep their meaning;
        ``;``, a newline and ``&`` all sequence without gating."""
        if self._token_type(node) == "SEP":
            assert isinstance(node, Token)
            return str(node.value)
        return ";"  # NEWLINE

    def _word_literal(self, node: Tree | Token | None) -> str | None:
        """Return the text of a word that is a single unquoted literal atom, else
        None. Keywords are recognised only through this, so a quoted or expanded
        word (``'for'``, ``$kw``) is never treated as reserved."""
        if not isinstance(node, Tree) or node.data != "word":
            return None
        if len(node.children) != 1:
            return None
        atom = node.children[0]
        if isinstance(atom, Token) and atom.type == "LITERAL":
            return str(atom.value)
        return None

    def _keyword(self, node: Tree | Token | None) -> str | None:
        text = self._word_literal(node)
        return text if text in _RESERVED else None

    def _parse_list(
        self, line: str, cursor: _Cursor, stop: frozenset[str]
    ) -> list[Statement]:
        """Parse statements separated by ``;`` / newline / ``&&`` / ``||`` until a
        reserved stop word or the end of input."""
        statements: list[Statement] = []
        pending_op: str | None = None
        seen = False
        # Index of the first statement on the current input line: bash rejects
        # a line with a syntax error as a whole, but has run the lines before.
        line_start = 0

        while True:
            # Consume the separators between statements, tracking the operator
            # that will join the next statement to the previous one.
            while self._is_separator(cursor.peek()):
                separator = cursor.next()
                if self._token_type(separator) == "NEWLINE":
                    line_start = len(statements)
                value = self._separator_op(separator)
                if value in ("&&", "||"):
                    if not seen:
                        # A leading && / || is a bash syntax error.
                        statements.append(
                            SyntaxError_(token=value, lineno=self._node_line(separator))
                        )
                        return statements
                    pending_op = value
                elif pending_op not in ("&&", "||"):
                    pending_op = ";"

            node = cursor.peek()
            if node is None:
                break
            keyword = self._keyword(node)
            if keyword in stop:
                break
            if keyword in _CLOSERS:
                del statements[line_start:]
                statements.append(
                    SyntaxError_(token=keyword, lineno=self._node_line(node))
                )
                return statements

            statement = self._parse_statement(
                line, cursor, pending_op if seen else None
            )
            statements.append(statement)
            seen = True
            pending_op = None
            if isinstance(statement, SyntaxError_):
                if not statement.lineno:
                    statement.lineno = self._node_line(cursor.peek()) or self._end_line(
                        line
                    )
                return statements

        return statements

    def _parse_statement(self, line: str, cursor: _Cursor, op: str | None) -> Statement:
        """One statement: a command, or a pipeline of commands joined by ``|``.

        A newline may follow a ``|`` (bash keeps reading the pipeline on the
        next line); a ``|`` with no command on either side is a syntax error.
        """
        if self._token_type(cursor.peek()) == "PIPE":
            return SyntaxError_(token="|")
        stages = [self._parse_command(line, cursor, op)]
        while self._token_type(cursor.peek()) == "PIPE":
            if isinstance(stages[-1], SyntaxError_):
                break
            cursor.next()  # "|"
            while self._token_type(cursor.peek()) == "NEWLINE":
                cursor.next()
            node = cursor.peek()
            if node is None:
                return SyntaxError_(token="")
            if self._token_type(node) == "PIPE":
                return SyntaxError_(token="|")
            stages.append(self._parse_command(line, cursor, None))
        if len(stages) == 1:
            return stages[0]
        if isinstance(stages[-1], SyntaxError_):
            return stages[-1]
        first = stages[0]
        assert not isinstance(first, SyntaxError_)
        first.op = None  # the join operator belongs to the pipeline
        return Pipeline(stages=stages, op=op)

    def _parse_command(self, line: str, cursor: _Cursor, op: str | None) -> Statement:
        """One command: a simple command or a compound command, with any
        redirection that follows a compound (`(...) > f`, `done 2>&1`)
        attached to it so the runtime can apply it to the whole group."""
        statement = self._parse_command_head(line, cursor, op)
        if isinstance(statement, REDIRECTABLE):
            redirections = self._collect_redirections(line, cursor)
            if redirections is not None:
                statement.redirections = redirections
        return statement

    def _parse_command_head(
        self, line: str, cursor: _Cursor, op: str | None
    ) -> Statement:
        node = cursor.peek()

        if isinstance(node, Tree) and node.data == "subshell":
            cursor.next()
            after = cursor.peek()
            if isinstance(after, Tree):
                # A word or another group directly after ")" -- bash reports
                # a syntax error near that token. A redirection ("(a) > f")
                # is a REDIR/IO_REDIR token, not a Tree, and is collected by
                # _parse_command instead.
                if after.data == "subshell":
                    return SyntaxError_(token=self._error_token(line, after))
                return SyntaxError_(token=self._word_source(line, after))
            return Subshell(statements=self._subshell_statements(line, node), op=op)

        if isinstance(node, Tree) and node.data == "case_clause":
            cursor.next()
            return self._case_clause(line, node, op)

        keyword = self._keyword(node)
        if keyword == "for":
            return self._parse_for(line, cursor, op)
        if keyword == "if":
            return self._parse_if(line, cursor, op)
        if keyword in ("while", "until"):
            return self._parse_while(line, cursor, op, until=keyword == "until")
        if keyword == "case":
            # The grammar did not recognise a complete case clause here, so
            # something after "case" is misplaced.
            cursor.next()
            return SyntaxError_(token=self._unexpected(line, cursor))
        if keyword == "{":
            return self._parse_brace_group(line, cursor, op)
        if keyword == "function":
            return self._parse_function_keyword(line, cursor, op)
        if self._looks_like_funcdef(cursor):
            return self._parse_function(line, cursor, op)

        return self._parse_simple(line, cursor, op)

    def _collect_redirections(self, line: str, cursor: _Cursor) -> Command | None:
        """Gather the redirections that follow a compound command as a
        :class:`Command` of operator strings and target words, or None if
        there are none. The runtime evaluates and applies them like a simple
        command's own redirections."""
        items: list[str | Tree] = []
        while self._token_type(cursor.peek()) in ("REDIR", "IO_REDIR"):
            operator = cursor.next()
            assert isinstance(operator, Token)
            items.append(str(operator.value))
            target = cursor.peek()
            if isinstance(target, Tree) and target.data == "word":
                items.append(cursor.next())
        if not items:
            return None
        return Command(items=items, line=line)

    def _parse_simple(self, line: str, cursor: _Cursor, op: str | None) -> Statement:
        """Gather one simple command up to the next ``|`` or statement end."""
        units: list[Tree | Token] = []
        while True:
            node = cursor.peek()
            if node is None or self._token_type(node) in _STAGE_END:
                break
            # A "(...)" that survived as a unit here is a subshell in the middle
            # of a command -- a bash syntax error reported on the "(" token.
            if isinstance(node, Tree) and node.data == "subshell":
                return SyntaxError_(token=self._error_token(line, node))
            # Likewise a case clause, which bash rejects at the pattern's ")",
            # and a "()" after an argument ("echo f()"), rejected at the "(".
            if isinstance(node, Tree) and node.data == "case_clause":
                return SyntaxError_(token=")")
            if self._token_type(node) == "FUNC_PARENS":
                return SyntaxError_(token="(")
            units.append(cursor.next())
        if (
            self._token_type(node) == "PIPE"
            and units
            and self._token_type(units[-1]) in ("REDIR", "IO_REDIR")
        ):
            # A redirection with the "|" where its target should be.
            return SyntaxError_(token="|")
        return self._make_command(line, units, op)

    def _make_command(
        self, line: str, units: list[Tree | Token], op: str | None
    ) -> Statement:
        # A redirection operator must be followed by a target word. A redirect at
        # the end of a statement, or directly before another operator, is a bash
        # syntax error (e.g. `echo >` reports the unexpected token `newline').
        for i, unit in enumerate(units):
            if isinstance(unit, Token) and unit.type in ("REDIR", "IO_REDIR"):
                target = units[i + 1] if i + 1 < len(units) else None
                if target is None:
                    return SyntaxError_(token="newline")
                if isinstance(target, Token):
                    return SyntaxError_(token=target.value)

        # Keep the structure; words are evaluated later by evaluate(). An
        # operator token (PIPE / AMP / REDIR / IO_REDIR) is a fixed string.
        items: list[str | Tree] = [
            unit.value if isinstance(unit, Token) else unit for unit in units
        ]
        return Command(items=items, line=line, op=op, lineno=self._node_line(units[0]))

    # -- compound commands --------------------------------------------------

    def _parse_for(self, line: str, cursor: _Cursor, op: str | None) -> Statement:
        cursor.next()  # "for"
        name = self._word_literal(cursor.peek())
        if name is None or not _NAME_RE.match(name):
            return SyntaxError_(token=self._unexpected(line, cursor))
        cursor.next()  # NAME

        words: list[Tree] = []
        if self._keyword(cursor.peek()) == "in":
            cursor.next()  # "in"
            while True:
                node = cursor.peek()
                if node is None or self._is_separator(node):
                    break
                if self._keyword(node) == "do":
                    break
                if isinstance(node, Tree) and node.data == "word":
                    cursor.next()
                    words.append(node)
                    continue
                break

        self._skip_separators(cursor)
        if self._keyword(cursor.peek()) != "do":
            return SyntaxError_(token=self._unexpected(line, cursor))
        cursor.next()  # "do"
        body = self._parse_list(line, cursor, stop=frozenset({"done"}))
        error = self._expect(cursor, "done")
        if error is not None:
            return error
        items = Command(items=list(words), line=line)
        return ForClause(var=name, items=items, body=body, op=op)

    def _parse_if(self, line: str, cursor: _Cursor, op: str | None) -> Statement:
        cursor.next()  # "if"
        branches: list[tuple[list[Statement], list[Statement]]] = []
        while True:
            condition = self._parse_list(line, cursor, stop=frozenset({"then"}))
            error = self._expect(cursor, "then")
            if error is not None:
                return error
            body = self._parse_list(
                line, cursor, stop=frozenset({"elif", "else", "fi"})
            )
            branches.append((condition, body))
            if self._keyword(cursor.peek()) == "elif":
                cursor.next()
                continue
            break

        else_body: list[Statement] | None = None
        if self._keyword(cursor.peek()) == "else":
            cursor.next()
            else_body = self._parse_list(line, cursor, stop=frozenset({"fi"}))

        error = self._expect(cursor, "fi")
        if error is not None:
            return error
        return IfClause(branches=branches, else_body=else_body, op=op)

    def _parse_while(
        self, line: str, cursor: _Cursor, op: str | None, until: bool
    ) -> Statement:
        cursor.next()  # "while" / "until"
        condition = self._parse_list(line, cursor, stop=frozenset({"do"}))
        error = self._expect(cursor, "do")
        if error is not None:
            return error
        body = self._parse_list(line, cursor, stop=frozenset({"done"}))
        error = self._expect(cursor, "done")
        if error is not None:
            return error
        return WhileClause(condition=condition, body=body, until=until, op=op)

    def _case_clause(self, line: str, tree: Tree, op: str | None) -> Statement:
        """Build a :class:`CaseClause` from the grammar's ``case_clause`` tree."""
        word: Tree | None = None
        items: list[tuple[list[str], list[Statement]]] = []
        for child in tree.children:
            if not isinstance(child, Tree):
                continue  # the case / in / esac keywords
            if child.data == "word":
                word = child
            elif child.data == "case_item":
                items.append(self._case_item(line, child))
        assert word is not None
        return CaseClause(word=Command(items=[word], line=line), items=items, op=op)

    def _case_item(self, line: str, item: Tree) -> tuple[list[str], list[Statement]]:
        """The raw pattern strings and parsed body of one ``pat[|pat]*) body ;;``."""
        patterns: list[str] = []
        body: list[Statement] = []
        for child in item.children:
            if not isinstance(child, Tree):
                continue  # "(", ")" and ";;"
            if child.data == "case_patterns":
                patterns = [
                    self._word_source(line, node)
                    for node in child.children
                    if isinstance(node, Tree) and node.data == "word"
                ]
            elif child.data == "start":
                body = self._split_statements(line, child)
        return patterns, body

    def _parse_brace_group(
        self, line: str, cursor: _Cursor, op: str | None
    ) -> Statement:
        cursor.next()  # "{"
        body = self._parse_list(line, cursor, stop=frozenset({"}"}))
        error = self._expect(cursor, "}")
        if error is not None:
            return error
        return BraceGroup(statements=body, op=op)

    def _looks_like_funcdef(self, cursor: _Cursor) -> bool:
        """Detect ``name ()`` at command position."""
        name = self._word_literal(cursor.peek())
        if name is None or not _NAME_RE.match(name):
            return False
        return self._token_type(cursor.peek(1)) == "FUNC_PARENS"

    def _parse_function(self, line: str, cursor: _Cursor, op: str | None) -> Statement:
        name = self._word_literal(cursor.next())
        assert name is not None
        self._consume_empty_parens(cursor)
        return self._finish_function(line, cursor, name, op)

    def _parse_function_keyword(
        self, line: str, cursor: _Cursor, op: str | None
    ) -> Statement:
        cursor.next()  # "function"
        name = self._word_literal(cursor.peek())
        if name is None or not _NAME_RE.match(name):
            return SyntaxError_(token=self._unexpected(line, cursor))
        cursor.next()  # NAME
        self._consume_empty_parens(cursor)
        return self._finish_function(line, cursor, name, op)

    def _consume_empty_parens(self, cursor: _Cursor) -> None:
        if self._token_type(cursor.peek()) == "FUNC_PARENS":
            cursor.next()

    def _finish_function(
        self, line: str, cursor: _Cursor, name: str, op: str | None
    ) -> Statement:
        self._skip_separators(cursor)
        if self._keyword(cursor.peek()) != "{":
            return SyntaxError_(token=self._unexpected(line, cursor))
        group = self._parse_brace_group(line, cursor, None)
        if isinstance(group, SyntaxError_):
            return group
        assert isinstance(group, BraceGroup)
        return FunctionDef(name=name, body=group.statements, op=op)

    # -- parser helpers -----------------------------------------------------

    def _skip_separators(self, cursor: _Cursor) -> None:
        while self._is_separator(cursor.peek()):
            cursor.next()

    def _expect(self, cursor: _Cursor, keyword: str) -> Statement | None:
        if self._keyword(cursor.peek()) != keyword:
            return SyntaxError_(token=self._unexpected("", cursor))
        cursor.next()
        return None

    def _unexpected(self, line: str, cursor: _Cursor) -> str:
        node = cursor.peek()
        if node is None:
            return "newline"
        if isinstance(node, Token):
            return str(node.value)
        if node.data == "word":
            return self._word_source(line, node) or "newline"
        return "newline"

    def _word_source(self, line: str, word: Tree) -> str:
        """The raw source text of a word, for keywords and case patterns."""
        if not line or word.meta.empty:
            literal = self._word_literal(word)
            return literal if literal is not None else ""
        return line[word.meta.start_pos : word.meta.end_pos]

    # -- word evaluation ----------------------------------------------------

    async def evaluate(self, command: Command, *, split: bool = True) -> list[str]:
        """Expand a command's words against the live context, now.

        Operator strings pass through; each word tree is evaluated against the
        current shell (variables, command substitution) into its fields. An
        unquoted expansion splits on whitespace and vanishes when empty, as in
        bash. Leading assignments, ``NAME=value`` arguments of declaration
        builtins (``export`` ...) and redirection targets stay one field, as
        does every word when ``split`` is false (a ``case`` word). A command
        substitution suspends the expansion until its subshell finishes, as
        bash blocks on the substitution pipe; substitutions therefore run left
        to right, each to completion before the next.
        """
        tokens: list[str] = []
        leading = True  # still in the NAME=value words before the command
        declaration = False
        redirect_target = False
        for item in command.items:
            if isinstance(item, str):
                tokens.append(item)
                redirect_target = "<" in item or ">" in item
                continue
            assignment = self._is_assignment(item)
            whole = (
                not split
                or redirect_target
                or (assignment and (leading or declaration))
            )
            redirect_target = False
            fields = await self._eval_word(command.line, item, split=not whole)
            if leading and not assignment and fields:
                leading = False
                declaration = fields[0] in _DECLARATION_BUILTINS
            tokens.extend(Field(value) for value in fields)
        return tokens

    @staticmethod
    def _is_assignment(word: Tree) -> bool:
        """Whether a word is NAME=value: its first atom is a literal NAME=."""
        first = word.children[0] if word.children else None
        return (
            isinstance(first, Token)
            and first.type == "LITERAL"
            and _ASSIGNMENT_PREFIX.match(first) is not None
        )

    async def _eval_word(self, line: str, word: Tree, *, split: bool) -> list[str]:
        """Evaluate a word to its fields.

        Literal and quoted text joins the field it is in; an unquoted
        expansion's result is split on whitespace when ``split`` is set. A
        word made only of empty unquoted expansions yields no field at all,
        like ``echo $unset`` or ``$(true)``, while a quoted empty string
        (``""``) is a field.
        """
        pieces: list[tuple[str, bool]] = []
        for atom in word.children:
            unquoted = isinstance(atom, Tree) and atom.data in _UNQUOTED_EXPANSIONS
            pieces.append((await self._eval_atom(line, atom), unquoted))
        if not split:
            if all(unquoted and not text for text, unquoted in pieces):
                return []
            return ["".join(text for text, _ in pieces)]
        return self._split_fields(pieces)

    @staticmethod
    def _split_fields(pieces: list[tuple[str, bool]]) -> list[str]:
        """Join a word's pieces into fields, splitting unquoted expansions.

        ``pieces`` pairs each atom's text with whether it came from an unquoted
        expansion. Whitespace inside such text ends the current field; other
        text, and any quoted text even when empty, extends it.
        """
        fields: list[str] = []
        current = ""
        started = False
        for text, unquoted in pieces:
            if not unquoted:
                current += text
                started = True
                continue
            for char in text:
                if char in _IFS_WHITESPACE:
                    if started:
                        fields.append(current)
                        current = ""
                        started = False
                else:
                    current += char
                    started = True
        if started:
            fields.append(current)
        return fields

    async def _eval_atom(self, line: str, atom: Tree | Token) -> str:
        if isinstance(atom, Token):
            if atom.type == "ESC":
                # Unquoted backslash escape: the backslash is removed.
                return str(atom.value)[1:]
            return str(atom.value)

        if atom.data == "sq":
            return self._leaf_value(atom)[1:-1]  # strip surrounding quotes
        if atom.data == "dq":
            return await self._eval_dquoted(line, atom)
        if atom.data == "dollar_var" or atom.data == "dollar_brace":
            return self._expand_embedded(atom)
        if atom.data == "cmdsub":
            return await self.context.command_substitution(
                self._group_source(line, atom)
            )
        if atom.data == "backtick":
            return await self.context.command_substitution(
                self._backtick_source(line, atom)
            )
        return ""

    async def _eval_dquoted(self, line: str, dq: Tree) -> str:
        parts: list[str] = []
        for part in dq.children:
            if isinstance(part, Token):
                if part.type in ("DQ_TEXT", "BARE_DOLLAR"):
                    parts.append(part.value)
                elif part.type == "DQ_ESC":
                    parts.append(self._unescape_dq(part.value))
                continue
            if part.data == "dollar_var" or part.data == "dollar_brace":
                parts.append(self._expand_embedded(part))
            elif part.data == "cmdsub":
                parts.append(
                    await self.context.command_substitution(
                        self._group_source(line, part)
                    )
                )
            elif part.data == "backtick":
                parts.append(
                    await self.context.command_substitution(
                        self._backtick_source(line, part)
                    )
                )
        return "".join(parts)

    def _unescape_dq(self, text: str) -> str:
        # Inside double quotes a backslash is literal unless it precedes one of
        # \ " $ ` ; only then is it removed.
        if len(text) == 2 and text[1] in '\\"$`':
            return text[1]
        return text

    def _expand_embedded(self, node: Tree) -> str:
        """Expand a ``$VAR`` reference that is embedded in a larger word.

        A set variable expands to its value (empty included); an unset
        reference expands to the empty string, as bash does for an unquoted or
        double-quoted reference. A single-quoted reference is kept literal by
        the grammar (it never reaches here), so quoted awk/sed field references
        still survive.
        """
        special = self._special_param(node)
        if special is not None:
            return special
        value = self.context.get_variable(self._var_name(node)[0])
        if value is None:
            return ""
        return value

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

    def _leaf_value(self, node: Tree) -> str:
        """Return the string value of a node's single leaf token."""
        token = node.children[0]
        assert isinstance(token, Token)
        return str(token)

    def _var_name(self, node: Tree) -> tuple[str, str | None]:
        """Return (name, special) for a dollar_var/dollar_brace node."""
        if node.data == "dollar_brace":
            return self._leaf_value(node), None
        tok = node.children[0]
        assert isinstance(tok, Token)
        text = str(tok)
        if tok.type == "DOLLAR_SPECIAL":
            return text, text[1:]
        return text[1:], None  # DOLLAR_NAME: strip leading $

    # -- source slicing for substitution / subshells ------------------------

    def _subshell_statements(self, line: str, subshell: Tree) -> list[Statement]:
        """Parse the ``body`` tree of a ``(...)`` group into statements."""
        for child in subshell.children:
            if isinstance(child, Tree) and child.data == "body":
                return self._split_statements(line, child)
        return []

    def _group_source(self, line: str, node: Tree) -> str:
        """Raw source of the inner ``start`` tree of a ``$(...)`` or ``(...)``."""
        for child in node.children:
            if isinstance(child, Tree) and child.data == "start":
                if child.meta.empty:
                    return ""
                return line[child.meta.start_pos : child.meta.end_pos]
        return ""

    def _backtick_source(self, line: str, node: Tree) -> str:
        if node.meta.empty:
            return ""
        # Drop the surrounding backtick delimiters from the matched span.
        return line[node.meta.start_pos + 1 : node.meta.end_pos - 1]

    def _error_token(self, line: str, subshell: Tree) -> str:
        """Reconstruct bash's reported token for a misplaced ``(``.

        bash points at the ``(`` together with the run of non-space characters
        that follow it, e.g. ``(echo`` for ``( echo middle )`` written as
        ``(echo middle)``.
        """
        start = subshell.meta.start_pos
        end = start + 1
        while end < len(line) and not line[end].isspace() and line[end] != ")":
            end += 1
        return line[start:end]
