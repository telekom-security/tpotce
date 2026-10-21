# SPDX-FileCopyrightText: 2020-2025 Michel Oosterhof <michel@oosterhof.net>
#
# SPDX-License-Identifier: BSD-3-Clause

"""
awk command

limited implementation: `pattern { print ... }` rules with -F field
separators, where print takes fields, NR, NF, string literals and
concatenation.
"""

from __future__ import annotations

import getopt
import re

from cowrie.shell.command import HoneyPotCommand
from cowrie.shell.fs import FileNotFound

commands = {}

_STRING_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "/": "/"}


class Command_awk(HoneyPotCommand):
    """
    awk command
    """

    consumes_stdin = True

    # code is an array of dictionaries contain the regexes to match and the code to execute
    code: list[dict[str, str]]
    # The -F field separator; None splits on runs of whitespace.
    separator: str | None = None
    record_number: int = 0

    def start(self) -> None:
        try:
            optlist, args = getopt.gnu_getopt(
                self.args,
                "F:v:f:",
                ["field-separator=", "assign=", "file=", "help", "version"],
            )
        except getopt.GetoptError as err:
            self.errorWrite(
                f"awk: invalid option -- '{err.opt}'\nTry 'awk --help' for more information.\n"
            )
            self.exit()
            return

        program = None
        for o, value in optlist:
            if o in "--help":
                self.help()
                self.exit()
                return
            elif o in "--version":
                self.version()
                self.exit()
                return
            elif o in ("-F", "--field-separator"):
                self.separator = None if value == " " else value
            elif o in ("-f", "--file"):
                try:
                    program = self.fs.file_contents(
                        self.fs.resolve_path(value, self.cwd)
                    ).decode("utf-8", errors="replace")
                except FileNotFound:
                    self.errorWrite(
                        f"awk: fatal: can't open source file `{value}' for reading: "
                        "No such file or directory\n"
                    )
                    self.exit_code = 2
                    self.exit()
                    return

        # first argument is program (generally between quotes if contains spaces)
        # second and onward arguments are files to operate on

        if program is None:
            if len(args) == 0:
                self.help()
                self.exit()
                return
            program = args.pop(0)

        self.code = self.awk_parser(program)

        if len(args) > 0:
            for arg in args:
                if arg == "-":
                    self.output(self.input_data)
                    continue

                pname = self.fs.resolve_path(arg, self.cwd)

                if self.fs.isdir(pname):
                    self.errorWrite(f"awk: {arg}: Is a directory\n")
                    continue

                try:
                    contents = self.fs.file_contents(pname)
                    self.output(contents)
                except FileNotFound:
                    self.errorWrite(f"awk: {arg}: No such file or directory\n")

        else:
            self.output(self.input_data)
        self.exit()

    def awk_parser(self, program: str) -> list[dict[str, str]]:
        """
        search for awk execution patterns, either direct {} code or only executed for a certain regex
        { }
        /regex/ { }
        """
        code = []
        rule = re.compile(
            r"\s*(?:/(?P<pattern>(?:\\.|[^/\\])*)/)?\s*(?:\{(?P<code>[^}]*)\})?\s*;?"
        )
        pos = 0
        while pos < len(program):
            m = rule.match(program, pos)
            if not m or m.end() == pos:
                break
            if m.group("pattern") is not None or m.group("code") is not None:
                # A pattern without an action prints the matching line.
                action = m.group("code") if m.group("code") is not None else "print"
                code.append({"regex": m.group("pattern") or "", "code": action})
            pos = m.end()
        return code

    def split_fields(self, line: str) -> list[str]:
        if self.separator is None:
            return line.split()
        if len(self.separator) == 1:
            return line.split(self.separator)
        try:
            return re.split(self.separator, line)
        except re.error:
            return line.split(self.separator)

    def evaluate_print(self, arguments: str, line: str, fields: list[str]) -> str:
        """Evaluate a print statement's arguments: comma-separated
        expressions, each a concatenation of fields ($N, $NF), NR, NF,
        numbers and string literals."""
        if not arguments.strip():
            return line
        values = [line, *fields]
        term = re.compile(r'\s*(\$NF|\$\d+|NR|NF|"(?:\\.|[^"\\])*"|\d+|,)')
        output: list[str] = []
        current = ""
        pos = 0
        while pos < len(arguments):
            m = term.match(arguments, pos)
            if not m:
                break
            token = m.group(1)
            pos = m.end()
            if token == ",":
                output.append(current)
                current = ""
            elif token == "$NF":
                current += values[len(fields)] if fields else line
            elif token.startswith("$"):
                index = int(token[1:])
                current += values[index] if index < len(values) else ""
            elif token == "NR":
                current += str(self.record_number)
            elif token == "NF":
                current += str(len(fields))
            elif token.startswith('"'):
                current += re.sub(
                    r"\\(.)",
                    lambda e: _STRING_ESCAPES.get(e.group(1), e.group(1)),
                    token[1:-1],
                )
            else:
                current += token
        output.append(current)
        return " ".join(output)

    def output(self, inb: bytes | None) -> None:
        """
        This is the awk output.
        """
        if inb:
            # Piped input is attacker bytes and need not be valid UTF-8.
            inp = inb.decode("utf-8", errors="replace")
        else:
            return

        inputlines = inp.split("\n")
        if inputlines[-1] == "":
            inputlines.pop()

        for inputline in inputlines:
            self.record_number += 1
            fields = self.split_fields(inputline)
            for c in self.code:
                try:
                    if c["regex"] and not re.search(c["regex"], inputline):
                        continue
                except re.error:
                    continue
                for statement in c["code"].split(";"):
                    m = re.match(r"\s*print\b(.*)", statement, re.DOTALL)
                    if m:
                        self.write(
                            self.evaluate_print(m.group(1), inputline, fields) + "\n"
                        )

    def lineReceived(self, line: str) -> None:
        """
        This function logs standard input from the user send to awk
        """
        self.protocol.events.dispatch(
            "cowrie.session.input",
            "INPUT (%(realm)s): %(input)s",
            realm="awk",
            input=line,
        )

        self.output(line.encode())

    def eofReceived(self) -> None:
        """
        ctrl-d is end-of-file, time to terminate
        """
        self.exit()

    def help(self) -> None:
        self.write(
            """Usage: awk [POSIX or GNU style options] -f progfile [--] file ...
Usage: awk [POSIX or GNU style options] [--] 'program' file ...
POSIX options:          GNU long options: (standard)
        -f progfile             --file=progfile
        -F fs                   --field-separator=fs
        -v var=val              --assign=var=val
Short options:          GNU long options: (extensions)
        -b                      --characters-as-bytes
        -c                      --traditional
        -C                      --copyright
        -d[file]                --dump-variables[=file]
        -D[file]                --debug[=file]
        -e 'program-text'       --source='program-text'
        -E file                 --exec=file
        -g                      --gen-pot
        -h                      --help
        -i includefile          --include=includefile
        -l library              --load=library
        -L[fatal|invalid]       --lint[=fatal|invalid]
        -M                      --bignum
        -N                      --use-lc-numeric
        -n                      --non-decimal-data
        -o[file]                --pretty-print[=file]
        -O                      --optimize
        -p[file]                --profile[=file]
        -P                      --posix
        -r                      --re-interval
        -S                      --sandbox
        -t                      --lint-old
        -V                      --version

To report bugs, see node `Bugs' in `gawk.info', which is
section `Reporting Problems and Bugs' in the printed version.

gawk is a pattern scanning and processing language.
By default it reads standard input and writes standard output.

Examples:
        gawk '{ sum += $1 }; END { print sum }' file
        gawk -F: '{ print $1 }' /etc/passwd
"""
        )

    def version(self) -> None:
        self.write(
            """GNU Awk 4.1.4, API: 1.1 (GNU MPFR 4.0.1, GNU MP 6.1.2)
Copyright (C) 1989, 1991-2016 Free Software Foundation.

This program is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation; either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program. If not, see http://www.gnu.org/licenses/.
"""
        )


commands["/bin/awk"] = Command_awk
commands["awk"] = Command_awk
