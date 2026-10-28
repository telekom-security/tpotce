# SPDX-FileCopyrightText: 2026 Michel Oosterhof <michel@oosterhof.net>
#
# SPDX-License-Identifier: BSD-3-Clause

# ABOUTME: Lark-based bash-subset parser that turns a command line into a list
# (excerpt of v3.1.3: imports, the grammar and its parser, ShellContext, _UNQUOTED_EXPANSIONS and
# BashParser._eval_atom, _eval_dquoted and _special_param)

from __future__ import annotations

import contextlib
import gc
import re
import signal
import threading

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


# The characters an unquoted expansion splits on: bash's default IFS.
_IFS_WHITESPACE = " \t\n"
_UNQUOTED_EXPANSIONS = frozenset({"dollar_var", "dollar_brace", "cmdsub", "backtick"})


class BashParser:
    """Parse a command line into a list of statements for the shell to run."""

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

