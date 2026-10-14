# SPDX-FileCopyrightText: 2009-2014 Upi Tamminen <desaster@gmail.com>
# SPDX-FileCopyrightText: 2014-2026 Michel Oosterhof <michel@oosterhof.net>
#
# SPDX-License-Identifier: BSD-3-Clause

# coding=utf-8

from __future__ import annotations

import codecs
import datetime
import getopt
import random
import re
import stat
import time
from typing import TYPE_CHECKING, Any, ClassVar

from twisted.internet import reactor
from twisted.logger import Logger

from cowrie.core import utils
from cowrie.shell.command import HoneyPotCommand
from cowrie.shell.fs import A_MODE, A_SIZE
from cowrie.shell.honeypot import LoopSignal

if TYPE_CHECKING:
    from collections.abc import Callable

commands: dict[str, Callable] = {}


class Command_whoami(HoneyPotCommand):
    def call(self) -> None:
        self.write(f"{self.user['username']}\n")


commands["/usr/bin/whoami"] = Command_whoami
commands["whoami"] = Command_whoami
commands["/usr/bin/users"] = Command_whoami
commands["users"] = Command_whoami


class Command_help(HoneyPotCommand):
    def call(self) -> None:
        self.write(
            """GNU bash, version 4.2.37(1)-release (x86_64-pc-linux-gnu)
These shell commands are defined internally.  Type `help' to see this list.
Type `help name' to find out more about the function `name'.
Use `info bash' to find out more about the shell in general.
Use `man -k' or `info' to find out more about commands not in this list.

A star (*) next to a name means that the command is disabled.

 job_spec [&]                                                                                   history [-c] [-d offset] [n] or history -anrw [filename] or history -ps arg [arg...]
 (( expression ))                                                                               if COMMANDS; then COMMANDS; [ elif COMMANDS; then COMMANDS; ]... [ else COMMANDS; ] fi
 . filename [arguments]                                                                         jobs [-lnprs] [jobspec ...] or jobs -x command [args]
 :                                                                                              kill [-s sigspec | -n signum | -sigspec] pid | jobspec ... or kill -l [sigspec]
 [ arg... ]                                                                                     let arg [arg ...]
 [[ expression ]]                                                                               local [option] name[=value] ...
 alias [-p] [name[=value] ... ]                                                                 logout [n]
 bg [job_spec ...]                                                                              mapfile [-n count] [-O origin] [-s count] [-t] [-u fd] [-C callback] [-c quantum] [array]
 bind [-lpvsPVS] [-m keymap] [-f filename] [-q name] [-u name] [-r keyseq] [-x keyseq:shell-c>  popd [-n] [+N | -N]
 break [n]                                                                                      printf [-v var] format [arguments]
 builtin [shell-builtin [arg ...]]                                                              pushd [-n] [+N | -N | dir]
 caller [expr]                                                                                  pwd [-LP]
 case WORD in [PATTERN [| PATTERN]...) COMMANDS ;;]... esac                                     read [-ers] [-a array] [-d delim] [-i text] [-n nchars] [-N nchars] [-p prompt] [-t timeout>
 cd [-L|[-P [-e]]] [dir]                                                                        readarray [-n count] [-O origin] [-s count] [-t] [-u fd] [-C callback] [-c quantum] [array]>
 command [-pVv] command [arg ...]                                                               readonly [-aAf] [name[=value] ...] or readonly -p
 compgen [-abcdefgjksuv] [-o option]  [-A action] [-G globpat] [-W wordlist]  [-F function] [>  return [n]
 complete [-abcdefgjksuv] [-pr] [-DE] [-o option] [-A action] [-G globpat] [-W wordlist]  [-F>  select NAME [in WORDS ... ;] do COMMANDS; done
 compopt [-o|+o option] [-DE] [name ...]                                                        set [-abefhkmnptuvxBCHP] [-o option-name] [--] [arg ...]
 continue [n]                                                                                   shift [n]
 coproc [NAME] command [redirections]                                                           shopt [-pqsu] [-o] [optname ...]
 declare [-aAfFgilrtux] [-p] [name[=value] ...]                                                 source filename [arguments]
 dirs [-clpv] [+N] [-N]                                                                         suspend [-f]
 disown [-h] [-ar] [jobspec ...]                                                                test [expr]
 echo [-neE] [arg ...]                                                                          time [-p] pipeline
 enable [-a] [-dnps] [-f filename] [name ...]                                                   times
 eval [arg ...]                                                                                 trap [-lp] [[arg] signal_spec ...]
 exec [-cl] [-a name] [command [arguments ...]] [redirection ...]                               true
 exit [n]                                                                                       type [-afptP] name [name ...]
 export [-fn] [name[=value] ...] or export -p                                                   typeset [-aAfFgilrtux] [-p] name[=value] ...
 false                                                                                          ulimit [-SHacdefilmnpqrstuvx] [limit]
 fc [-e ename] [-lnr] [first] [last] or fc -s [pat=rep] [command]                               umask [-p] [-S] [mode]
 fg [job_spec]                                                                                  unalias [-a] name [name ...]
 for NAME [in WORDS ... ] ; do COMMANDS; done                                                   unset [-f] [-v] [name ...]
 for (( exp1; exp2; exp3 )); do COMMANDS; done                                                  until COMMANDS; do COMMANDS; done
 function name { COMMANDS ; } or name () { COMMANDS ; }                                         variables - Names and meanings of some shell variables
 getopts optstring name [arg]                                                                   wait [id]
 hash [-lr] [-p pathname] [-dt] [name ...]                                                      while COMMANDS; do COMMANDS; done
 help [-dms] [pattern ...]                                                                      { COMMANDS ; }\n"""
        )


commands["help"] = Command_help


class Command_w(HoneyPotCommand):
    def call(self) -> None:
        self.write(
            f" {time.strftime('%H:%M:%S')} up {utils.uptime(self.protocol.uptime())},  1 user,  load average: 0.00, 0.00, 0.00\n"
        )
        self.write(
            "USER     TTY      FROM              LOGIN@   IDLE   JCPU   PCPU WHAT\n"
        )
        self.write(
            f"{self.user['username']:8s} pts/0    {self.protocol.clientIP[:17].ljust(17)} {time.strftime('%H:%M', time.localtime(self.protocol.logintime))}    0.00s  0.00s  0.00s w\n"
        )


commands["/usr/bin/w"] = Command_w
commands["w"] = Command_w


class Command_who(HoneyPotCommand):
    def call(self) -> None:
        self.write(
            f"{self.user['username']:8s} pts/0        {time.strftime('%Y-%m-%d', time.localtime(self.protocol.logintime))} {time.strftime('%H:%M', time.localtime(self.protocol.logintime))} ({self.protocol.clientIP})\n"
        )


commands["/usr/bin/who"] = Command_who
commands["who"] = Command_who


class Command_echo(HoneyPotCommand):
    _log = Logger()

    def call(self) -> None:
        newline = True
        escape_decode = False

        try:
            optlist, args = getopt.getopt(self.args, "eEn")
            for opt in optlist:
                if opt[0] == "-e":
                    escape_decode = True
                elif opt[0] == "-E":
                    escape_decode = False
                elif opt[0] == "-n":
                    newline = False
        except Exception:
            args = self.args

        try:
            # replace r'\\x' with r'\x'
            string = " ".join(args).replace(r"\\x", r"\x")

            # replace single character escape \x0 with \x00
            string = re.sub(
                r"(?<=\\)x([0-9a-fA-F])(?=\\|\"|\'|\s|$)", r"x0\g<1>", string
            )

            # if the string ends with \c escape, strip it and set newline flag to False
            if string.endswith("\\c"):
                string = string[:-2]
                newline = False

            if newline is True:
                string += "\n"

            if escape_decode:
                data: bytes = codecs.escape_decode(string)[0]
                self.writeBytes(data)
            else:
                self.write(string)

        except ValueError:
            self._log.info("echo command received Python incorrect hex escape")


commands["/bin/echo"] = Command_echo
commands["echo"] = Command_echo


class Command_clear(HoneyPotCommand):
    def call(self) -> None:
        self.protocol.terminal.reset()


commands["/usr/bin/clear"] = Command_clear
commands["clear"] = Command_clear
commands["/usr/bin/reset"] = Command_clear
commands["reset"] = Command_clear


class Command_hostname(HoneyPotCommand):
    def call(self) -> None:
        if self.args:
            if self.user["uid"] == 0:
                self.protocol.hostname = self.args[0]
            else:
                self.write("hostname: you must be root to change the host name\n")
        else:
            self.write(f"{self.protocol.hostname}\n")


commands["/bin/hostname"] = Command_hostname
commands["hostname"] = Command_hostname


class Command_ps(HoneyPotCommand):
    def columns(self) -> int:
        """Width to truncate each line to. COLUMNS is an ordinary shell
        variable that export sets to anything at all, so fall back to the
        default width rather than trusting it to be a number."""
        try:
            return int(self.environ["COLUMNS"])
        except (KeyError, ValueError):
            return 80

    def call(self) -> None:
        user = str(self.user["username"])
        args = ""
        if self.args:
            args = self.args[0].strip()
        (
            _user,
            _pid,
            _cpu,
            _mem,
            _vsz,
            _rss,
            _tty,
            _stat,
            _start,
            _time,
            _command,
        ) = list(range(11))
        output_array = []

        output = (
            "%s".ljust(15 - len("USER")) % "USER",
            "%s".ljust(8 - len("PID")) % "PID",
            "%s".ljust(13 - len("%CPU")) % "%CPU",
            "%s".ljust(13 - len("%MEM")) % "%MEM",
            "%s".ljust(12 - len("VSZ")) % "VSZ",
            "%s".ljust(12 - len("RSS")) % "RSS",
            "%s".ljust(10 - len("TTY")) % "TTY",
            "%s".ljust(8 - len("STAT")) % "STAT",
            "%s".ljust(8 - len("START")) % "START",
            "%s".ljust(8 - len("TIME")) % "TIME",
            "%s".ljust(30 - len("COMMAND")) % "COMMAND",
        )
        output_array.append(output)
        if self.protocol.user.server.process:
            for single_ps in self.protocol.user.server.process:
                output = (
                    "%s".ljust(15 - len(str(single_ps["USER"])))
                    % str(single_ps["USER"]),
                    "%s".ljust(8 - len(str(single_ps["PID"]))) % str(single_ps["PID"]),
                    "%s".ljust(13 - len(str(round(single_ps["CPU"], 2))))
                    % str(round(single_ps["CPU"], 2)),
                    "%s".ljust(13 - len(str(round(single_ps["MEM"], 2))))
                    % str(round(single_ps["MEM"], 2)),
                    "%s".ljust(12 - len(str(single_ps["VSZ"]))) % str(single_ps["VSZ"]),
                    "%s".ljust(12 - len(str(single_ps["RSS"]))) % str(single_ps["RSS"]),
                    "%s".ljust(10 - len(str(single_ps["TTY"]))) % str(single_ps["TTY"]),
                    "%s".ljust(8 - len(str(single_ps["STAT"])))
                    % str(single_ps["STAT"]),
                    "%s".ljust(8 - len(str(single_ps["START"])))
                    % str(single_ps["START"]),
                    "%s".ljust(8 - len(str(single_ps["TIME"])))
                    % str(single_ps["TIME"]),
                    "%s".ljust(30 - len(str(single_ps["COMMAND"])))
                    % str(single_ps["COMMAND"]),
                )
                output_array.append(output)
            process = random.randint(4000, 8000)
            output = (
                "%s".ljust(15 - len("root")) % "root",
                "%s".ljust(8 - len(str(process))) % str(process),
                "%s".ljust(13 - len("0.0")) % "0.0",
                "%s".ljust(13 - len("0.1")) % "0.1",
                "%s".ljust(12 - len("5416")) % "5416",
                "%s".ljust(12 - len("1024")) % "1024",
                "%s".ljust(10 - len("?")) % "?",
                "%s".ljust(8 - len("Ss")) % "Ss",
                "%s".ljust(8 - len("Jul22")) % "Jul22",
                "%s".ljust(8 - len("0:00")) % "0:00",
                "%s".ljust(30 - len("/usr/sbin/sshd: %s@pts/0"))
                % "/usr/sbin/sshd: %s@pts/0"
                % user,
            )
            output_array.append(output)
            process = process + 5
            output = (
                "%s".ljust(15 - len(user)) % user,
                "%s".ljust(8 - len(str(process))) % str(process),
                "%s".ljust(13 - len("0.0")) % "0.0",
                "%s".ljust(13 - len("0.1")) % "0.1",
                "%s".ljust(12 - len("2925")) % "5416",
                "%s".ljust(12 - len("1541")) % "1024",
                "%s".ljust(10 - len("pts/0")) % "pts/0",
                "%s".ljust(8 - len("Ss")) % "Ss",
                "%s".ljust(8 - len("06:30")) % "06:30",
                "%s".ljust(8 - len("0:00")) % "0:00",
                "%s".ljust(30 - len("bash")) % "-bash",
            )
            output_array.append(output)
            process = process + 2
            output = (
                "%s".ljust(15 - len(user)) % user,
                "%s".ljust(8 - len(str(process))) % str(process),
                "%s".ljust(13 - len("0.0")) % "0.0",
                "%s".ljust(13 - len("0.1")) % "0.1",
                "%s".ljust(12 - len("2435")) % "2435",
                "%s".ljust(12 - len("929")) % "929",
                "%s".ljust(10 - len("pts/0")) % "pts/0",
                "%s".ljust(8 - len("Ss")) % "Ss",
                "%s".ljust(8 - len("06:30")) % "06:30",
                "%s".ljust(8 - len("0:00")) % "0:00",
                "%s".ljust(30 - len("ps")) % "ps %s" % " ".join(self.args),
            )

            output_array.append(output)
        else:
            output_array = [
                (
                    "USER      ",
                    " PID",
                    " %CPU",
                    " %MEM",
                    "    VSZ",
                    "   RSS",
                    " TTY      ",
                    "STAT ",
                    "START",
                    "   TIME ",
                    "COMMAND",
                ),
                (
                    "root      ",
                    "   1",
                    "  0.0",
                    "  0.1",
                    "   2100",
                    "   688",
                    " ?        ",
                    "Ss   ",
                    "Nov06",
                    "   0:07 ",
                    "init [2]  ",
                ),
                (
                    "root      ",
                    "   2",
                    "  0.0",
                    "  0.0",
                    "      0",
                    "     0",
                    " ?        ",
                    "S<   ",
                    "Nov06",
                    "   0:00 ",
                    "[kthreadd]",
                ),
                (
                    "root      ",
                    "   3",
                    "  0.0",
                    "  0.0",
                    "      0",
                    "     0",
                    " ?        ",
                    "S<   ",
                    "Nov06",
                    "   0:00 ",
                    "[migration/0]",
                ),
                (
                    "root      ",
                    "   4",
                    "  0.0",
                    "  0.0",
                    "      0",
                    "     0",
                    " ?        ",
                    "S<   ",
                    "Nov06",
                    "   0:00 ",
                    "[ksoftirqd/0]",
                ),
                (
                    "root      ",
                    "   5",
                    "  0.0",
                    "  0.0",
                    "      0",
                    "     0",
                    " ?        ",
                    "S<   ",
                    "Nov06",
                    "   0:00 ",
                    "[watchdog/0]",
                ),
                (
                    "root      ",
                    "   6",
                    "  0.0",
                    "  0.0",
                    "      0",
                    "     0",
                    " ?        ",
                    "S<   ",
                    "Nov06",
                    "   0:17 ",
                    "[events/0]",
                ),
                (
                    "root      ",
                    "   7",
                    "  0.0",
                    "  0.0",
                    "      0",
                    "     0",
                    " ?        ",
                    "S<   ",
                    "Nov06",
                    "   0:00 ",
                    "[khelper]",
                ),
                (
                    "root      ",
                    "  39",
                    "  0.0",
                    "  0.0",
                    "      0",
                    "     0",
                    " ?        ",
                    "S<   ",
                    "Nov06",
                    "   0:00 ",
                    "[kblockd/0]",
                ),
                (
                    "root      ",
                    "  41",
                    "  0.0",
                    "  0.0",
                    "      0",
                    "     0",
                    " ?        ",
                    "S<   ",
                    "Nov06",
                    "   0:00 ",
                    "[kacpid]",
                ),
                (
                    "root      ",
                    "  42",
                    "  0.0",
                    "  0.0",
                    "      0",
                    "     0",
                    " ?        ",
                    "S<   ",
                    "Nov06",
                    "   0:00 ",
                    "[kacpi_notify]",
                ),
                (
                    "root      ",
                    " 170",
                    "  0.0",
                    "  0.0",
                    "      0",
                    "     0",
                    " ?        ",
                    "S<   ",
                    "Nov06",
                    "   0:00 ",
                    "[kseriod]",
                ),
                (
                    "root      ",
                    " 207",
                    "  0.0",
                    "  0.0",
                    "      0",
                    "     0",
                    " ?        ",
                    "S    ",
                    "Nov06",
                    "   0:01 ",
                    "[pdflush]",
                ),
                (
                    "root      ",
                    " 208",
                    "  0.0",
                    "  0.0",
                    "      0",
                    "     0",
                    " ?        ",
                    "S    ",
                    "Nov06",
                    "   0:00 ",
                    "[pdflush]",
                ),
                (
                    "root      ",
                    " 209",
                    "  0.0",
                    "  0.0",
                    "      0",
                    "     0",
                    " ?        ",
                    "S<   ",
                    "Nov06",
                    "   0:00 ",
                    "[kswapd0]",
                ),
                (
                    "root      ",
                    " 210",
                    "  0.0",
                    "  0.0",
                    "      0",
                    "     0",
                    " ?        ",
                    "S<   ",
                    "Nov06",
                    "   0:00 ",
                    "[aio/0]",
                ),
                (
                    "root      ",
                    " 748",
                    "  0.0",
                    "  0.0",
                    "      0",
                    "     0",
                    " ?        ",
                    "S<   ",
                    "Nov06",
                    "   0:00 ",
                    "[ata/0]",
                ),
                (
                    "root      ",
                    " 749",
                    "  0.0",
                    "  0.0",
                    "      0",
                    "     0",
                    " ?        ",
                    "S<   ",
                    "Nov06",
                    "   0:00 ",
                    "[ata_aux]",
                ),
                (
                    "root      ",
                    " 929",
                    "  0.0",
                    "  0.0",
                    "      0",
                    "     0",
                    " ?        ",
                    "S<   ",
                    "Nov06",
                    "   0:00 ",
                    "[scsi_eh_0]",
                ),
                (
                    "root      ",
                    "1014",
                    "  0.0",
                    "  0.0",
                    "      0",
                    "     0",
                    " ?        ",
                    "D<   ",
                    "Nov06",
                    "   0:03 ",
                    "[kjournald]",
                ),
                (
                    "root      ",
                    "1087",
                    "  0.0",
                    "  0.1",
                    "   2288",
                    "   772",
                    " ?        ",
                    "S<s  ",
                    "Nov06",
                    "   0:00 ",
                    "udevd --daemon",
                ),
                (
                    "root      ",
                    "1553",
                    "  0.0",
                    "  0.0",
                    "      0",
                    "     0",
                    " ?        ",
                    "S<   ",
                    "Nov06",
                    "   0:00 ",
                    "[kpsmoused]",
                ),
                (
                    "root      ",
                    "2054",
                    "  0.0",
                    "  0.2",
                    "  28428",
                    "  1508",
                    " ?        ",
                    "Sl   ",
                    "Nov06",
                    "   0:01 ",
                    "/usr/sbin/rsyslogd -c3",
                ),
                (
                    "root      ",
                    "2103",
                    "  0.0",
                    "  0.2",
                    "   2628",
                    "  1196",
                    " tty1     ",
                    "Ss   ",
                    "Nov06",
                    "   0:00 ",
                    "/bin/login --     ",
                ),
                (
                    "root      ",
                    "2105",
                    "  0.0",
                    "  0.0",
                    "   1764",
                    "   504",
                    " tty2     ",
                    "Ss+  ",
                    "Nov06",
                    "   0:00 ",
                    "/sbin/getty 38400 tty2",
                ),
                (
                    "root      ",
                    "2107",
                    "  0.0",
                    "  0.0",
                    "   1764",
                    "   504",
                    " tty3     ",
                    "Ss+  ",
                    "Nov06",
                    "   0:00 ",
                    "/sbin/getty 38400 tty3",
                ),
                (
                    "root      ",
                    "2109",
                    "  0.0",
                    "  0.0",
                    "   1764",
                    "   504",
                    " tty4     ",
                    "Ss+  ",
                    "Nov06",
                    "   0:00 ",
                    "/sbin/getty 38400 tty4",
                ),
                (
                    "root      ",
                    "2110",
                    "  0.0",
                    "  0.0",
                    "   1764",
                    "   504",
                    " tty5     ",
                    "Ss+  ",
                    "Nov06",
                    "   0:00 ",
                    "/sbin/getty 38400 tty5",
                ),
                (
                    "root      ",
                    "2112",
                    "  0.0",
                    "  0.0",
                    "   1764",
                    "   508",
                    " tty6     ",
                    "Ss+  ",
                    "Nov06",
                    "   0:00 ",
                    "/sbin/getty 38400 tty6",
                ),
                (
                    "root      ",
                    "2133",
                    "  0.0",
                    "  0.1",
                    "   2180",
                    "   620",
                    " ?        ",
                    "S<s  ",
                    "Nov06",
                    "   0:00 ",
                    "dhclient3 -pf /var/run/dhclient.eth0.pid -lf /var/lib/dhcp3/dhclien",
                ),
                (
                    "root      ",
                    "4969",
                    "  0.0",
                    "  0.1",
                    "   5416",
                    "  1024",
                    " ?        ",
                    "Ss   ",
                    "Nov08",
                    "   0:00 ",
                    f"/usr/sbin/sshd: {user}@pts/0",
                ),
                (
                    "%s".ljust(8) % user,
                    "5673",
                    "  0.0",
                    "  0.2",
                    "   2924",
                    "  1540",
                    " pts/0    ",
                    "Ss   ",
                    "04:30",
                    "   0:00 ",
                    "-bash",
                ),
                (
                    "%s".ljust(8) % user,
                    "5679",
                    "  0.0",
                    "  0.1",
                    "   2432",
                    "   928",
                    " pts/0    ",
                    "R+   ",
                    "04:32",
                    "   0:00 ",
                    f"ps {' '.join(self.args)}",
                ),
            ]

        for i in range(len(output_array)):
            if i != 0:
                if "a" not in args and output_array[i][_user].strip() != user:
                    continue
                elif (
                    "a" not in args
                    and "x" not in args
                    and output_array[i][_tty].strip() != "pts/0"
                ):
                    continue
            line = [_pid, _tty, _time, _command]
            if "a" in args or "x" in args:
                line = [_pid, _tty, _stat, _time, _command]
            if "u" in args:
                line = [
                    _user,
                    _pid,
                    _cpu,
                    _mem,
                    _vsz,
                    _rss,
                    _tty,
                    _stat,
                    _start,
                    _time,
                    _command,
                ]
            s = "".join([output_array[i][x] for x in line])
            if "w" not in args:
                s = s[: self.columns()]
            self.write(f"{s}\n")


commands["/bin/ps"] = Command_ps
commands["ps"] = Command_ps


class Command_id(HoneyPotCommand):
    def call(self) -> None:
        u = self.user
        self.write(
            f"uid={u['uid']}({u['username']}) gid={u.get('gid', u['uid'])}({u['username']}) groups={u.get('gid', u['uid'])}({u['username']})\n"
        )


commands["/usr/bin/id"] = Command_id
commands["id"] = Command_id


class Command_passwd(HoneyPotCommand):
    callbacks: list[Callable]
    passwd: str | None

    def start(self) -> None:
        self.write("Enter new UNIX password: ")
        self.protocol.password_input = True
        self.callbacks = [self.ask_again, self.finish]
        self.passwd = None

    def ask_again(self, line: str) -> None:
        self.passwd = line
        self.write("Retype new UNIX password: ")

    def finish(self, line: str) -> None:
        self.protocol.password_input = False

        if line != self.passwd or self.passwd == "*":
            self.write("Sorry, passwords do not match\n")
        else:
            self.write("passwd: password updated successfully\n")
        self.exit()

    def lineReceived(self, line: str) -> None:
        self.protocol.events.dispatch(
            "cowrie.command.success",
            "INPUT (%(realm)s): %(input)s",
            realm="passwd",
            input=line,
        )
        self.password = line.strip()
        self.callbacks.pop(0)(line)


commands["/usr/bin/passwd"] = Command_passwd
commands["passwd"] = Command_passwd


class Command_shutdown(HoneyPotCommand):
    def start(self) -> None:
        if self.args and self.args[0].strip().count("--help"):
            output = [
                "Usage:     shutdown [-akrhHPfnc] [-t secs] time [warning message]",
                "-a:      use /etc/shutdown.allow ",
                "-k:      don't really shutdown, only warn. ",
                "-r:      reboot after shutdown. ",
                "-h:      halt after shutdown. ",
                "-P:      halt action is to turn off power. ",
                "-H:      halt action is to just halt. ",
                "-f:      do a 'fast' reboot (skip fsck). ",
                "-F:      Force fsck on reboot. ",
                '-n:      do not go through "init" but go down real fast. ',
                "-c:      cancel a running shutdown. ",
                "-t secs: delay between warning and kill signal. ",
                '** the "time" argument is mandatory! (try "now") **',
            ]
            for line in output:
                self.write(f"{line}\n")
            self.exit()
        elif (
            len(self.args) > 1
            and self.args[0].strip().count("-h")
            and self.args[1].strip().count("now")
        ):
            self.write("\n")
            self.write(
                f"Broadcast message from root@{self.protocol.hostname} (pts/0) ({time.ctime()}):\n"
            )
            self.write("\n")
            self.write("The system is going down for maintenance NOW!\n")
            reactor.callLater(3, self.finish)
        elif (
            len(self.args) > 1
            and self.args[0].strip().count("-r")
            and self.args[1].strip().count("now")
        ):
            self.write("\n")
            self.write(
                f"Broadcast message from root@{self.protocol.hostname} (pts/0) ({time.ctime()}):\n"
            )
            self.write("\n")
            self.write("The system is going down for reboot NOW!\n")
            reactor.callLater(3, self.finish)
        else:
            self.write("Try `shutdown --help' for more information.\n")
            self.exit()

    def finish(self) -> None:
        # The client may have disconnected during the delay.
        self.protocol.end_process(0)


commands["/sbin/shutdown"] = Command_shutdown
commands["shutdown"] = Command_shutdown
commands["/sbin/poweroff"] = Command_shutdown
commands["poweroff"] = Command_shutdown
commands["/sbin/halt"] = Command_shutdown
commands["halt"] = Command_shutdown


class Command_reboot(HoneyPotCommand):
    def start(self) -> None:
        self.write("\n")
        self.write(
            f"Broadcast message from root@{self.protocol.hostname} (pts/0) ({time.ctime()}):\n\n"
        )
        self.write("The system is going down for reboot NOW!\n")
        reactor.callLater(3, self.finish)

    def finish(self) -> None:
        # The client may have disconnected during the delay.
        self.protocol.end_process(0)


commands["/sbin/reboot"] = Command_reboot
commands["reboot"] = Command_reboot


class Command_history(HoneyPotCommand):
    def call(self) -> None:
        try:
            if self.args and self.args[0] == "-c":
                self.protocol.historyLines = []
                self.protocol.historyPosition = 0
                return
            count = 1
            for line in self.protocol.historyLines:
                # A typed line need not be valid UTF-8.
                self.write(f" {count:4d}  {line.decode(errors='replace')}\n")
                count += 1
        except Exception:
            # Non-interactive shell, do nothing
            pass


commands["history"] = Command_history


class Command_date(HoneyPotCommand):
    def call(self) -> None:
        time = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
        self.write(f"{time.strftime('%a %b %d %H:%M:%S UTC %Y')}\n")


commands["/bin/date"] = Command_date
commands["date"] = Command_date


class Command_yes(HoneyPotCommand):
    scheduled: Any

    def start(self) -> None:
        self.y()

    def y(self) -> None:
        if self.args:
            self.write(f"{' '.join(self.args)}\n")
        else:
            self.write("y\n")
        self.scheduled = reactor.callLater(0.01, self.y)

    def handle_CTRL_C(self) -> None:
        self.scheduled.cancel()
        self.exit()


commands["/usr/bin/yes"] = Command_yes
commands["yes"] = Command_yes


class Command_php(HoneyPotCommand):
    HELP = (
        "Usage: php [options] [-f] <file> [--] [args...]\n"
        "       php [options] -r <code> [--] [args...]\n"
        "       php [options] [-B <begin_code>] -R <code> [-E <end_code>] [--] [args...]\n"
        "       php [options] [-B <begin_code>] -F <file> [-E <end_code>] [--] [args...]\n"
        "       php [options] -- [args...]\n"
        "       php [options] -a\n"
        "\n"
        "  -a               Run interactively\n"
        "  -c <path>|<file> Look for php.ini file in this directory\n"
        "  -n               No php.ini file will be used\n"
        "  -d foo[=bar]     Define INI entry foo with value 'bar'\n"
        "  -e               Generate extended information for debugger/profiler\n"
        "  -f <file>        Parse and execute <file>.\n"
        "  -h               This help\n"
        "  -i               PHP information\n"
        "  -l               Syntax check only (lint)\n"
        "  -m               Show compiled in modules\n"
        "  -r <code>        Run PHP <code> without using script tags <?..?>\n"
        "  -B <begin_code>  Run PHP <begin_code> before processing input lines\n"
        "  -R <code>        Run PHP <code> for every input line\n"
        "  -F <file>        Parse and execute <file> for every input line\n"
        "  -E <end_code>    Run PHP <end_code> after processing all input lines\n"
        "  -H               Hide any passed arguments from external tools.\n"
        "  -s               Output HTML syntax highlighted source.\n"
        "  -v               Version number\n"
        "  -w               Output source with stripped comments and whitespace.\n"
        "  -z <file>        Load Zend extension <file>.\n"
        "\n"
        "  args...          Arguments passed to script. Use -- args when first argument\n"
        "                   starts with - or script is read from stdin\n"
        "\n"
        "  --ini            Show configuration file names\n"
        "\n"
        "  --rf <name>      Show information about function <name>.\n"
        "  --rc <name>      Show information about class <name>.\n"
        "  --re <name>      Show information about extension <name>.\n"
        "  --ri <name>      Show configuration for extension <name>.\n"
        "\n"
    )

    VERSION = "PHP 5.3.5 (cli)\nCopyright (c) 1997-2010 The PHP Group\n"

    def start(self) -> None:
        if self.args:
            if self.args[0] == "-v":
                self.write(Command_php.VERSION)
            elif self.args[0] == "-h":
                self.write(Command_php.HELP)
            self.exit()

    def lineReceived(self, line: str) -> None:
        self.protocol.events.dispatch(
            "cowrie.command.success",
            "INPUT (%(realm)s): %(input)s",
            realm="php",
            input=line,
        )

    def eofReceived(self) -> None:
        self.exit()


commands["/usr/bin/php"] = Command_php
commands["php"] = Command_php


class Command_chattr(HoneyPotCommand):
    def call(self) -> None:
        if len(self.args) < 1:
            self.write("Usage: chattr [-RVf] [-+=AacDdeijsSu] [-v version] files...\n")
            return
        elif len(self.args) < 2:
            self.write("Must use '-v', =, - or +'\n")
            return
        if not self.fs.exists(self.args[1]):
            self.write(
                "chattr: No such file or directory while trying to stat "
                + self.args[1]
                + "\n"
            )


commands["/usr/bin/chattr"] = Command_chattr
commands["chattr"] = Command_chattr


class Command_set(HoneyPotCommand):
    # Basic functionaltly (show only), need enhancements
    # This will show ALL environ vars, not only the global ones
    # With enhancements it should work like env when -o posix is used
    def call(self) -> None:
        for i in sorted(self.environ):
            self.write(f"{i}={self.environ[i]}\n")


commands["set"] = Command_set


class Command_export(HoneyPotCommand):
    """
    Mark shell variables for export so they appear in the environment of
    child processes. With no operands, list the exported variables.
    """

    def call(self) -> None:
        args = [a for a in self.args if a != "-p"]
        if not args:
            for name in sorted(self.exported):
                self.write(f'declare -x {name}="{self.environ.get(name, "")}"\n')
            return
        for arg in args:
            if "=" in arg:
                key, val = arg.split("=", 1)
                self.environ[key] = val
                self.exported.add(key)
            else:
                self.exported.add(arg)


commands["export"] = Command_export


class Command_unset(HoneyPotCommand):
    """
    Remove variables from both the shell and the exported environment.
    """

    def call(self) -> None:
        for arg in self.args:
            if arg.startswith("-"):
                continue
            self.environ.pop(arg, None)
            self.exported.discard(arg)


commands["unset"] = Command_unset


class Command_nop(HoneyPotCommand):
    def call(self) -> None:
        pass


commands["umask"] = Command_nop
commands["alias"] = Command_nop
commands["jobs"] = Command_nop
commands["kill"] = Command_nop
commands["/bin/kill"] = Command_nop
commands["/bin/pkill"] = Command_nop
commands["/bin/killall"] = Command_nop
commands["/bin/killall5"] = Command_nop
commands["/bin/chown"] = Command_nop
commands["chown"] = Command_nop
commands["/bin/chgrp"] = Command_nop
commands["chgrp"] = Command_nop
commands[":"] = Command_nop
commands["do"] = Command_nop
commands["done"] = Command_nop


class Command_true(HoneyPotCommand):
    """The ``true`` utility: do nothing, successfully (exit 0)."""

    def start(self) -> None:
        self.exit(0)


commands["true"] = Command_true
commands["/bin/true"] = Command_true


class Command_false(HoneyPotCommand):
    """The ``false`` utility: do nothing, unsuccessfully (exit 1)."""

    def start(self) -> None:
        self.exit(1)


commands["false"] = Command_false
commands["/bin/false"] = Command_false


class Command_break(HoneyPotCommand):
    """``break`` -- stop the innermost enclosing for/while/until loop.

    Signals the nearest shell that is currently running a loop; the loop's
    executor consumes the signal at the end of the current body. Outside any
    loop it is a no-op, matching how the honeypot tolerates stray builtins.

    Loop state is tracked per shell, not per function call, so a ``break`` in a
    function body still targets an enclosing loop in the same shell. Real bash
    ignores a ``break`` that is not lexically inside a loop in the running
    function; emulating that would need a per-function loop scope.
    """

    signal = LoopSignal.BREAK

    def call(self) -> None:
        for item in reversed(self.protocol.cmdstack):
            if getattr(item, "_loop_depth", 0) > 0:
                item._loop_signal = self.signal
                return


class Command_continue(Command_break):
    """``continue`` -- skip to the next iteration of the innermost loop."""

    signal = LoopSignal.CONTINUE


commands["break"] = Command_break
commands["continue"] = Command_continue


class _TestError(Exception):
    """A malformed test expression; ``test`` reports it and exits 2.

    Message templates live here so the call sites raise with a short tag,
    keeping the human-readable text in one place.
    """

    _MESSAGES: ClassVar[dict[str, str]] = {
        "too_many": "too many arguments",
        "integer": "integer expression expected",
        "unary": "{token}: unary operator expected",
        "binary": "{token}: binary operator expected",
    }

    def __init__(self, kind: str, token: str = "") -> None:
        super().__init__(self._MESSAGES[kind].format(token=token))


class Command_test(HoneyPotCommand):
    """The ``test`` / ``[`` conditional utility.

    Evaluates an expression and exits 0 (true) or 1 (false); a malformed
    expression exits 2. Supports the operators shell scripts rely on for flow
    control: file tests against the emulated filesystem (``-e -f -d -r -w -x
    -s -L -h -b -c -p -S -g -u -k``), string tests (``-z -n``, ``= == != < >``)
    and integer comparisons (``-eq -ne -lt -le -gt -ge``), plus ``!`` negation
    and ``-a`` / ``-o`` combination.

    Invoked as ``[`` the final argument must be ``]``.
    """

    # Set by the ``[`` alias so the closing bracket is required.
    require_bracket: bool = False
    name: str = "test"

    _STRING_OPS = ("=", "==", "!=", "<", ">")
    _INT_OPS = ("-eq", "-ne", "-lt", "-le", "-gt", "-ge")
    _UNARY_FILE_OPS = (
        "-e",
        "-f",
        "-d",
        "-r",
        "-w",
        "-x",
        "-s",
        "-L",
        "-h",
        "-b",
        "-c",
        "-p",
        "-S",
        "-g",
        "-u",
        "-k",
    )

    def call(self) -> None:
        args = list(self.args)
        if self.require_bracket:
            if not args or args[-1] != "]":
                self.errorWrite(f"{self.name}: missing `]'\n")
                self.exit_code = 2
                return
            args = args[:-1]

        try:
            result = self._eval(args)
        except _TestError as exc:
            self.errorWrite(f"{self.name}: {exc}\n")
            self.exit_code = 2
            return
        self.exit_code = 0 if result else 1

    def _eval(self, args: list[str]) -> bool:
        n = len(args)
        if n == 0:
            return False
        if n == 1:
            # A single argument is true when it is a non-empty string.
            return args[0] != ""
        if args[0] == "!":
            # Negation of the remaining expression.
            return not self._eval(args[1:])
        if n == 2:
            return self._unary(args[0], args[1])
        if n == 3:
            # `STR1 -a STR2` / `STR1 -o STR2` combine two single-argument
            # (non-empty) tests; otherwise the middle word is a binary operator.
            if args[1] == "-a":
                return self._eval(args[:1]) and self._eval(args[2:])
            if args[1] == "-o":
                return self._eval(args[:1]) or self._eval(args[2:])
            return self._binary(args[0], args[1], args[2])
        if n == 4 and args[0] == "(" and args[3] == ")":
            return self._eval(args[1:3])
        # `EXPR -a EXPR` / `EXPR -o EXPR`: bash deprecates these, but scripts
        # still use them. Split on the first top-level -a / -o we find.
        for combiner in ("-o", "-a"):
            if combiner in args:
                idx = args.index(combiner)
                left = self._eval(args[:idx])
                right = self._eval(args[idx + 1 :])
                return (left or right) if combiner == "-o" else (left and right)
        raise _TestError("too_many")

    def _unary(self, op: str, operand: str) -> bool:
        if op == "-z":
            return len(operand) == 0
        if op == "-n":
            return len(operand) != 0
        if op in self._UNARY_FILE_OPS:
            return self._file_test(op, operand)
        raise _TestError("unary", op)

    def _binary(self, left: str, op: str, right: str) -> bool:
        if op in self._STRING_OPS:
            if op in ("=", "=="):
                return left == right
            if op == "!=":
                return left != right
            if op == "<":
                return left < right
            return left > right  # ">"
        if op in self._INT_OPS:
            try:
                a, b = int(left), int(right)
            except ValueError:
                raise _TestError("integer", op) from None
            return {
                "-eq": a == b,
                "-ne": a != b,
                "-lt": a < b,
                "-le": a <= b,
                "-gt": a > b,
                "-ge": a >= b,
            }[op]
        raise _TestError("binary", op)

    def _file_test(self, op: str, operand: str) -> bool:
        if not operand:
            # An empty path is not any kind of file: bash reports every file
            # test on "" as false. Short-circuit before resolving it.
            return False
        path = self.fs.resolve_path(operand, self.cwd)
        if op in ("-L", "-h"):
            try:
                return bool(self.fs.islink(path))
            except Exception:
                return False
        if op == "-d":
            return bool(self.fs.isdir(path))
        if op == "-f":
            return bool(self.fs.isfile(path))
        if op == "-e":
            return bool(self.fs.exists(path))
        if op in ("-r", "-w"):
            # No per-user permission model; treat any existing path as readable
            # and writable, which is true for the honeypot's root sessions.
            return bool(self.fs.exists(path))
        if op == "-x":
            return self._has_mode(path, stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        if op == "-s":
            entry = self._stat(path)
            return entry is not None and entry[A_SIZE] > 0
        if op == "-g":
            return self._has_mode(path, stat.S_ISGID)
        if op == "-u":
            return self._has_mode(path, stat.S_ISUID)
        if op == "-k":
            return self._has_mode(path, stat.S_ISVTX)
        # -b -c -p -S: special file types we do not model; report absent.
        return False

    def _stat(self, path: str) -> list[Any] | None:
        try:
            entry: list[Any] | None = self.fs.getfile(path)
        except Exception:
            return None
        return entry

    def _has_mode(self, path: str, mask: int) -> bool:
        entry = self._stat(path)
        if entry is None:
            return False
        try:
            return bool(entry[A_MODE] & mask)
        except (IndexError, TypeError):
            return False


class Command_lbracket(Command_test):
    """The ``[`` alias of ``test``; requires a closing ``]``."""

    require_bracket = True
    name = "["


commands["test"] = Command_test
commands["/usr/bin/test"] = Command_test
commands["["] = Command_lbracket
commands["/usr/bin/["] = Command_lbracket
