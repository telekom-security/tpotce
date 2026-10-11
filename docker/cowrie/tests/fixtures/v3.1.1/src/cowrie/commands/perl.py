# SPDX-FileCopyrightText: 2016 davegermiquet <davegermiquet@trulycanadian.net>
# SPDX-FileCopyrightText: 2016-2024 Michel Oosterhof <michel@oosterhof.net>
#
# SPDX-License-Identifier: BSD-3-Clause

"""
This module contains the perl command
"""

from __future__ import annotations

import getopt

from cowrie.shell.command import HoneyPotCommand

commands = {}


class Command_perl(HoneyPotCommand):
    def version(self) -> None:
        output = (
            "",
            "This is perl 5, version 14, subversion 2 (v5.14.2) built for x86_64-linux-thread-multi",
            "",
            "Copyright 1987-2014, Larry Wall",
            "",
            "Perl may be copied only under the terms of either the Artistic License or the",
            "GNU General Public License, which may be found in the Perl 5 source kit.",
            "",
            "Complete documentation for Perl, including FAQ lists, should be found on",
            'this system using "man perl" or "perldoc perl".  If you have access to the',
            "Internet, point your browser at http://www.perl.org/, the Perl Home Page.",
            "",
        )
        for line in output:
            self.write(line + "\n")

    def help(self) -> None:
        output = (
            "",
            "Usage: perl [switches] [--] [programfile] [arguments]",
            "  -0[octal]         specify record separator (\\0, if no argument)",
            "  -a                autosplit mode with -n or -p (splits $_ into @F)",
            "  -C[number/list]   enables the listed Unicode features",
            "  -c                check syntax only (runs BEGIN and CHECK blocks)",
            "  -d[:debugger]     run program under debugger",
            "  -D[number/list]   set debugging flags (argument is a bit mask or alphabets)",
            "  -e program        one line of program (several -e's allowed, omit programfile)",
            "  -E program        like -e, but enables all optional features",
            "  -f                don't do $sitelib/sitecustomize.pl at startup",
            "  -F/pattern/       split() pattern for -a switch (//'s are optional)",
            "  -i[extension]     edit <> files in place (makes backup if extension supplied)",
            "  -Idirectory       specify @INC/#include directory (several -I's allowed)",
            "  -l[octal]         enable line ending processing, specifies line terminator",
            '  -[mM][-]module    execute "use/no module..." before executing program',
            '  -n                assume "while (<>) { ... }" loop around program',
            "  -p                assume loop like -n but print line also, like sed",
            "  -s                enable rudimentary parsing for switches after programfile",
            "  -S                look for programfile using PATH environment variable",
            "  -t                enable tainting warnings",
            "  -T                enable tainting checks",
            "  -u                dump core after parsing program",
            "  -U                allow unsafe operations",
            "  -v                print version, subversion (includes VERY IMPORTANT perl info)",
            "  -V[:variable]     print configuration summary (or a single Config.pm variable)",
            "  -w                enable many useful warnings (RECOMMENDED)",
            "  -W                enable all warnings",
            "  -x[directory]     strip off text before #!perl line and perhaps cd to directory",
            "  -X                disable all warnings",
            "",
        )
        for line in output:
            self.write(line + "\n")

    def start(self) -> None:
        try:
            opts, args = getopt.gnu_getopt(
                self.args, "acfhnpsStTuUvwWXC:D:e:E:F:i:I:l:m:M:V:X:"
            )
        except getopt.GetoptError as err:
            self.write(
                "Unrecognized switch: -" + err.opt + " (-h will show valid options).\n"
            )
            self.exit()
            return

        # Parse options
        for o, _a in opts:
            if o in ("-v"):
                self.version()
                self.exit()
                return
            elif o in ("-h"):
                self.help()
                self.exit()
                return

        for value in args:
            sourcefile = self.fs.resolve_path(value, self.cwd)

            if self.fs.exists(sourcefile):
                self.exit()
            else:
                self.write(
                    f'Can\'t open perl script "{value}": No such file or directory\n'
                )
                self.exit()

        if not len(self.args):
            pass

    def lineReceived(self, line: str) -> None:
        self.protocol.events.dispatch(
            "cowrie.command.input",
            "INPUT (%(realm)s): %(input)s",
            realm="perl",
            input=line,
        )

    def eofReceived(self) -> None:
        self.exit()


commands["/usr/bin/perl"] = Command_perl
commands["perl"] = Command_perl
