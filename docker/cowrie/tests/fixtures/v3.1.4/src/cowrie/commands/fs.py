# SPDX-FileCopyrightText: 2009-2011 Upi Tamminen <desaster@gmail.com>
# SPDX-FileCopyrightText: 2015-2026 Michel Oosterhof <michel@oosterhof.net>
#
# SPDX-License-Identifier: BSD-3-Clause


"""
Filesystem related commands
"""

from __future__ import annotations

import copy
import getopt
import os.path
import posixpath
import re
from typing import TYPE_CHECKING

from cowrie.shell import fs
from cowrie.shell.command import HoneyPotCommand

if TYPE_CHECKING:
    from collections.abc import Callable

commands: dict[str, Callable] = {}


class Command_grep(HoneyPotCommand):
    """
    grep command
    """

    consumes_stdin = True

    interactive: bool = False
    matched: bool = False
    # A file could not be read: grep then exits 2, unless -q and a match.
    errored: bool = False
    quiet: bool = False
    max_count: int | None = None
    match_count: int = 0
    ignore_case: bool = False

    def grep_get_contents(self, filename: str, match: str) -> None:
        try:
            contents = self.fs.file_contents(filename)
            self.grep_application(contents, match)
        except Exception:
            self.errorWrite(f"grep: {filename}: No such file or directory\n")
            self.errored = True

    def compile_match(self, match: str) -> re.Pattern[bytes]:
        bmatch = os.path.basename(match).replace('"', "").encode("utf8")
        flags = re.NOFLAG
        if self.ignore_case:
            flags = re.IGNORECASE
        return re.compile(bmatch, flags)

    def grep_application(self, contents: bytes, match: str) -> None:
        matcher = self.compile_match(match)
        for line in contents.split(b"\n"):
            if self.max_count is not None and self.match_count >= self.max_count:
                break
            if matcher.search(line):
                self.matched = True
                self.match_count += 1
                if not self.quiet:
                    self.writeBytes(line + b"\n")

    def help(self) -> None:
        self.writeBytes(
            b"usage: grep [-abcDEFGHhIiJLlmnOoPqRSsUVvwxZ] [-A num] [-B num] [-C[num]]\n"
        )
        self.writeBytes(
            b"\t[-e pattern] [-f file] [--binary-files=value] [--color=when]\n"
        )
        self.writeBytes(
            b"\t[--context[=num]] [--directories=action] [--label] [--line-buffered]\n"
        )
        self.writeBytes(b"\t[--null] [pattern] [file ...]\n")

    def start(self) -> None:
        if not self.args:
            self.help()
            self.exit(2)
            return

        try:
            optlist, args = getopt.getopt(
                self.args,
                "abcDEFGHhIiJLlnOoPqRSsUVvwxZA:B:C:e:f:m:",
                [
                    "binary-files=",
                    "color=",
                    "color",
                    "context=",
                    "directories=",
                    "label",
                    "line-buffered",
                    "quiet",
                    "silent",
                ],
            )
        except getopt.GetoptError as err:
            self.errorWrite(f"grep: invalid option -- {err.opt}\n")
            self.help()
            self.exit(2)
            return

        for opt, arg in optlist:
            if opt == "-h":
                self.help()
            elif opt in ("-q", "--quiet", "--silent"):
                self.quiet = True
            elif opt == "-m":
                try:
                    n = int(arg)
                except ValueError:
                    n = -1
                if n < 0:
                    self.errorWrite("grep: invalid max count\n")
                    self.exit(2)
                    return
                self.max_count = n
            elif opt == "-i":
                self.ignore_case = True

        if not args:
            # Options only, no pattern (e.g. `grep -h`).
            self.exit()
            return

        self.match = args[0]

        # grep validates the pattern before it reads any input, so a malformed
        # one is reported once rather than per file or per line of stdin.
        try:
            self.compile_match(self.match)
        except re.error:
            self.errorWrite("grep: Invalid regular expression\n")
            self.exit(2)
            return

        files = args[1:]

        if self.input_data is not None:
            self.grep_application(self.input_data, self.match)
        elif files:
            for pname in self.check_arguments("grep", files):
                self.grep_get_contents(pname, self.match)
        else:
            # No file and no pipe: read stdin until EOF.
            self.interactive = True
            return

        self.exit(self._status())

    def _status(self) -> int:
        """grep's exit status: 0 on a match, 1 on none, 2 if a file could not
        be read -- except that with -q any match is success."""
        if self.matched and self.quiet:
            return 0
        if self.errored:
            return 2
        return 0 if self.matched else 1

    def lineReceived(self, line: str) -> None:
        self.protocol.events.dispatch(
            "cowrie.command.input",
            "INPUT (%(realm)s): %(input)s",
            realm="grep",
            input=line,
        )
        if self.interactive:
            self.grep_application(line.encode("utf8"), self.match)

    def eofReceived(self) -> None:
        if self.interactive:
            terminal = self.protocol.terminal
            if (
                getattr(terminal, "stdinlogOpen", False)
                and getattr(terminal, "stdinlogFile", "")
                and os.path.exists(terminal.stdinlogFile)
            ):
                # Live exec-channel stdin (e.g. `grep foo < file` over an ssh
                # exec): the bytes were streamed to the stdin log rather than
                # arriving via lineReceived, so match against them now.
                with open(terminal.stdinlogFile, "rb") as f:
                    self.grep_application(f.read(), self.match)
        self.exit(self._status())


commands["/bin/grep"] = Command_grep
commands["grep"] = Command_grep
commands["/bin/egrep"] = Command_grep
commands["/bin/fgrep"] = Command_grep


# GNU size suffixes accepted by head and tail counts.
_COUNT_SUFFIXES = {
    "": 1,
    "b": 512,
    "kB": 1000,
    "k": 1024,
    "K": 1024,
    "KiB": 1024,
    "MB": 1000**2,
    "M": 1024**2,
    "MiB": 1024**2,
    "GB": 1000**3,
    "G": 1024**3,
    "GiB": 1024**3,
}


def _parse_count(text: str) -> tuple[str, int] | None:
    """Parse a head/tail count: an optional sign ("-" or "+"), digits and a
    GNU size suffix. Returns (sign, value), or None when it is invalid."""
    match = re.fullmatch(r"([-+]?)(\d+)([A-Za-z]*)", text)
    if not match or match.group(3) not in _COUNT_SUFFIXES:
        return None
    return match.group(1), int(match.group(2)) * _COUNT_SUFFIXES[match.group(3)]


def _split_lines(data: bytes) -> list[bytes]:
    """Split into lines that keep their newline; a final line without one
    stays without one."""
    parts = data.split(b"\n")
    lines = [part + b"\n" for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


class _HeadTail(HoneyPotCommand):
    """The shared option handling and file loop of head and tail."""

    consumes_stdin = True
    name: str

    # Count mode ("lines" or "bytes"), its sign as typed and its value.
    mode: str = "lines"
    sign: str = ""
    count: int = 10

    def select(self, data: bytes) -> bytes:
        raise NotImplementedError

    def start(self) -> None:
        args = list(self.args)
        # The obsolete "-NUM" form (and "+NUM" for tail) as the first argument.
        if args and re.fullmatch(r"-\d+", args[0]):
            args[0:1] = ["-n", args[0][1:]]
        try:
            optlist, files = getopt.gnu_getopt(
                args,
                "c:n:qvfF",
                ["bytes=", "lines=", "quiet", "silent", "verbose", "follow"],
            )
        except getopt.GetoptError as err:
            self.errorWrite(
                f"{self.name}: invalid option -- '{err.opt}'\n"
                f"Try '{self.name} --help' for more information.\n"
            )
            self.exit_code = 1
            self.exit()
            return

        for opt, value in optlist:
            if opt in ("-n", "--lines", "-c", "--bytes"):
                mode = "lines" if opt in ("-n", "--lines") else "bytes"
                parsed = _parse_count(value)
                if parsed is None:
                    self.errorWrite(
                        f"{self.name}: invalid number of {mode}: '{value}'\n"
                    )
                    self.exit_code = 1
                    self.exit()
                    return
                self.mode = mode
                self.sign, self.count = parsed

        if files:
            for index, name in enumerate(files):
                if len(files) > 1:
                    prefix = "\n" if index else ""
                    self.write(f"{prefix}==> {name} <==\n")
                self.write_file(name)
        elif self.input_data is not None:
            self.writeBytes(self.select(self.input_data))
        else:
            # Reading the terminal: wait for its end of input.
            return
        self.exit()

    def write_file(self, name: str) -> None:
        path = self.fs.resolve_path(name, self.cwd)
        if self.fs.isdir(path):
            self.errorWrite(f"{self.name}: error reading '{name}': Is a directory\n")
            self.exit_code = 1
            return
        try:
            contents = self.fs.file_contents(path)
        except fs.FileNotFound:
            self.errorWrite(
                f"{self.name}: cannot open '{name}' for reading: "
                "No such file or directory\n"
            )
            self.exit_code = 1
            return
        self.writeBytes(self.select(contents))

    def lineReceived(self, line: str) -> None:
        self.protocol.events.dispatch(
            "cowrie.command.input",
            "INPUT (%(realm)s): %(input)s",
            realm=self.name,
            input=line,
        )

    def eofReceived(self) -> None:
        self.exit()


class Command_tail(_HeadTail):
    """
    tail command
    """

    name = "tail"

    def select(self, data: bytes) -> bytes:
        if self.mode == "bytes":
            if self.sign == "+":
                return data[max(self.count - 1, 0) :]
            return data[-self.count :] if self.count else b""
        lines = _split_lines(data)
        if self.sign == "+":
            return b"".join(lines[max(self.count - 1, 0) :])
        return b"".join(lines[-self.count :]) if self.count else b""


commands["/bin/tail"] = Command_tail
commands["/usr/bin/tail"] = Command_tail
commands["tail"] = Command_tail


class Command_head(_HeadTail):
    """
    head command
    """

    name = "head"

    def select(self, data: bytes) -> bytes:
        if self.mode == "bytes":
            if self.sign == "-":
                return data[: max(len(data) - self.count, 0)]
            return data[: self.count]
        lines = _split_lines(data)
        if self.sign == "-":
            return b"".join(lines[: max(len(lines) - self.count, 0)])
        return b"".join(lines[: self.count])


commands["/bin/head"] = Command_head
commands["/usr/bin/head"] = Command_head
commands["head"] = Command_head


class Command_cd(HoneyPotCommand):
    """
    cd command
    """

    def call(self) -> None:
        if not self.args or self.args[0] == "~":
            pname = self.user["home"]
        else:
            pname = self.args[0]
        newpath = ""
        try:
            newpath = self.fs.resolve_path(pname, self.cwd)
            inode = self.fs.getfile(newpath)
        except Exception:
            inode = None
        if pname == "-":
            self.errorWrite(f"{self.shell.error_prefix()}cd: OLDPWD not set\n")
            self.exit_code = 1
            return
        if inode is None or inode is False:
            self.errorWrite(
                f"{self.shell.error_prefix()}cd: {pname}: No such file or directory\n"
            )
            self.exit_code = 1
            return
        if inode[fs.A_TYPE] != fs.T_DIR:
            self.errorWrite(
                f"{self.shell.error_prefix()}cd: {pname}: Not a directory\n"
            )
            self.exit_code = 1
            return
        # cd is a builtin: it changes the running shell's directory, not this
        # command process's own.
        self.shell.cwd = newpath


commands["cd"] = Command_cd


class Command_rm(HoneyPotCommand):
    """
    rm command
    """

    def help(self) -> None:
        self.write(
            """Usage: rm [OPTION]... [FILE]...
Remove (unlink) the FILE(s).

 -f, --force           ignore nonexistent files and arguments, never prompt
 -i                    prompt before every removal
 -I                    prompt once before removing more than three files, or
                         when removing recursively; less intrusive than -i,
                         while still giving protection against most mistakes
      --interactive[=WHEN]  prompt according to WHEN: never, once (-I), or
                         always (-i); without WHEN, prompt always
      --one-file-system  when removing a hierarchy recursively, skip any
                         directory that is on a file system different from
                         that of the corresponding command line argument
      --no-preserve-root  do not treat '/' specially
      --preserve-root   do not remove '/' (default)
 -r, -R, --recursive   remove directories and their contents recursively
 -d, --dir             remove empty directories
 -v, --verbose         explain what is being done
     --help     display this help and exit
     --version  output version information and exit

By default, rm does not remove directories.  Use the --recursive (-r or -R)
option to remove each listed directory, too, along with all of its contents.

To remove a file whose name starts with a '-', for example '-foo',
use one of these commands:
 rm -- -foo

 rm ./-foo

Note that if you use rm to remove a file, it might be possible to recover
some of its contents, given sufficient expertise and/or time.  For greater
assurance that the contents are truly unrecoverable, consider using shred.

GNU coreutils online help: <http://www.gnu.org/software/coreutils/>
Full documentation at: <http://www.gnu.org/software/coreutils/rm>
or available locally via: info '(coreutils) rm invocation'\n"""
        )

    def paramError(self) -> None:
        self.errorWrite("Try 'rm --help' for more information\n")
        self.exit_code = 1

    def call(self) -> None:
        recursive = False
        force = False
        verbose = False
        if not self.args:
            self.errorWrite("rm: missing operand\n")
            self.paramError()
            return

        try:
            optlist, args = getopt.gnu_getopt(
                self.args, "rTfvh", ["help", "recursive", "force", "verbose"]
            )
        except getopt.GetoptError as err:
            self.errorWrite(f"rm: invalid option -- '{err.opt}'\n")
            self.paramError()
            self.exit()
            return

        for o, _a in optlist:
            if o in ("--recursive", "-r", "-R"):
                recursive = True
            elif o in ("--force", "-f"):
                force = True
            elif o in ("--verbose", "-v"):
                verbose = True
            elif o in ("--help", "-h"):
                self.help()
                return

        for f in args:
            pname = self.fs.resolve_path(f, self.cwd)
            node = self.fs.getfile(pname, follow_symlinks=False)
            if node is None:
                if not force:
                    self.errorWrite(
                        f"rm: cannot remove `{f}': No such file or directory\n"
                    )
                    self.exit_code = 1
                continue
            if node[fs.A_TYPE] == fs.T_DIR and not recursive:
                self.errorWrite(
                    f"rm: cannot remove `{node[fs.A_NAME]}': Is a directory\n"
                )
                self.exit_code = 1
                continue
            self.fs.remove(pname)
            if verbose:
                if node[fs.A_TYPE] == fs.T_DIR:
                    self.write(f"removed directory '{node[fs.A_NAME]}'\n")
                else:
                    self.write(f"removed '{node[fs.A_NAME]}'\n")


commands["/bin/rm"] = Command_rm
commands["rm"] = Command_rm


class Command_cp(HoneyPotCommand):
    """
    cp command
    """

    def call(self) -> None:
        if not len(self.args):
            self.errorWrite("cp: missing file operand\n")
            self.errorWrite("Try `cp --help' for more information.\n")
            self.exit_code = 1
            return
        try:
            optlist, args = getopt.gnu_getopt(self.args, "-abdfiHlLPpRrsStTuvx")
        except getopt.GetoptError:
            self.errorWrite("Unrecognized option\n")
            self.exit_code = 1
            return
        recursive = False
        for opt in optlist:
            if opt[0] in ("-r", "-a", "-R"):
                recursive = True

        def resolv(pname: str) -> str:
            rsv: str = self.fs.resolve_path(pname, self.cwd)
            return rsv

        if len(args) < 2:
            self.errorWrite(
                f"cp: missing destination file operand after `{self.args[0]}'\n"
            )
            self.errorWrite("Try `cp --help' for more information.\n")
            self.exit_code = 1
            return
        sources, dest = args[:-1], args[-1]
        # Quoting reaches the command as an empty argument; there is no such
        # path, so there is nothing to resolve or index into.
        if not dest:
            self.errorWrite(
                f"cp: cannot create regular file `{dest}': No such file or directory\n"
            )
            self.exit_code = 1
            return
        if len(sources) > 1 and not self.fs.isdir(resolv(dest)):
            self.errorWrite(f"cp: target `{dest}' is not a directory\n")
            self.exit_code = 1
            return

        if dest[-1] == "/" and not self.fs.exists(resolv(dest)) and not recursive:
            self.errorWrite(
                f"cp: cannot create regular file `{dest}': Is a directory\n"
            )
            self.exit_code = 1
            return

        if self.fs.isdir(resolv(dest)):
            isdir = True
        else:
            isdir = False
            parent = posixpath.dirname(resolv(dest))
            if not self.fs.exists(parent):
                self.errorWrite(
                    "cp: cannot create regular file "
                    + f"`{dest}': No such file or directory\n"
                )
                self.exit_code = 1
                return

        for src in sources:
            if not self.fs.exists(resolv(src)):
                self.errorWrite(f"cp: cannot stat `{src}': No such file or directory\n")
                self.exit_code = 1
                continue
            if not recursive and self.fs.isdir(resolv(src)):
                self.errorWrite(f"cp: omitting directory `{src}'\n")
                self.exit_code = 1
                continue
            s = copy.deepcopy(self.fs.getfile(resolv(src)))
            if isdir:
                destdir = resolv(dest)
                outfile = posixpath.basename(src)
            else:
                destdir = posixpath.dirname(resolv(dest))
                outfile = posixpath.basename(dest.rstrip("/"))
            s[fs.A_NAME] = outfile
            self.fs.link_entry(s, destdir)


commands["/bin/cp"] = Command_cp
commands["cp"] = Command_cp


class Command_mv(HoneyPotCommand):
    """
    mv command
    """

    def call(self) -> None:
        if not len(self.args):
            self.errorWrite("mv: missing file operand\n")
            self.errorWrite("Try `mv --help' for more information.\n")
            self.exit_code = 1
            return

        try:
            _optlist, args = getopt.gnu_getopt(self.args, "-bfiStTuv")
        except getopt.GetoptError:
            self.errorWrite("Unrecognized option\n")
            self.exit_code = 1
            return

        def resolv(pname: str) -> str:
            rsv: str = self.fs.resolve_path(pname, self.cwd)
            return rsv

        if len(args) < 2:
            self.errorWrite(
                f"mv: missing destination file operand after `{self.args[0]}'\n"
            )
            self.errorWrite("Try `mv --help' for more information.\n")
            self.exit_code = 1
            return
        sources, dest = args[:-1], args[-1]
        # Quoting reaches the command as an empty argument; there is no such
        # path, so there is nothing to resolve or index into.
        if not dest:
            self.errorWrite(
                f"mv: cannot move `{sources[0]}' to `{dest}': "
                "No such file or directory\n"
            )
            self.exit_code = 1
            return
        if len(sources) > 1 and not self.fs.isdir(resolv(dest)):
            self.errorWrite(f"mv: target `{dest}' is not a directory\n")
            self.exit_code = 1
            return

        if dest[-1] == "/" and not self.fs.exists(resolv(dest)) and len(sources) != 1:
            self.errorWrite(
                f"mv: cannot create regular file `{dest}': Is a directory\n"
            )
            self.exit_code = 1
            return

        if self.fs.isdir(resolv(dest)):
            isdir = True
        else:
            isdir = False
            parent = posixpath.dirname(resolv(dest))
            if not self.fs.exists(parent):
                self.errorWrite(
                    "mv: cannot create regular file "
                    + f"`{dest}': No such file or directory\n"
                )
                self.exit_code = 1
                return

        for src in sources:
            srcpath = resolv(src)
            if not self.fs.exists(srcpath):
                self.errorWrite(f"mv: cannot stat `{src}': No such file or directory\n")
                self.exit_code = 1
                continue
            if isdir:
                destpath = posixpath.join(resolv(dest), posixpath.basename(src))
            else:
                destpath = resolv(dest)
            self.fs.rename(srcpath, destpath)


commands["/bin/mv"] = Command_mv
commands["mv"] = Command_mv


class Command_mkdir(HoneyPotCommand):
    """
    mkdir command
    """

    def call(self) -> None:
        if not self.args:
            self.errorWrite("mkdir: missing operand\n")
            self.errorWrite("Try 'mkdir --help' for more information.\n")
            self.exit_code = 1
            return
        for f in self.args:
            pname = self.fs.resolve_path(f, self.cwd)
            if self.fs.exists(pname):
                self.errorWrite(f"mkdir: cannot create directory `{f}': File exists\n")
                self.exit_code = 1
                continue
            try:
                self.fs.mkdir(pname, self.user["uid"], self.user["gid"], 4096, 16877)
            except fs.FileNotFound:
                self.errorWrite(
                    f"mkdir: cannot create directory `{f}': No such file or directory\n"
                )
                self.exit_code = 1
            except OSError as e:
                self.errorWrite(
                    f"mkdir: cannot create directory `{f}': {e.strerror}\n"
                )
                self.exit_code = 1


commands["/bin/mkdir"] = Command_mkdir
commands["mkdir"] = Command_mkdir


class Command_rmdir(HoneyPotCommand):
    """
    rmdir command
    """

    def call(self) -> None:
        if not self.args:
            self.errorWrite("rmdir: missing operand\n")
            self.errorWrite("Try 'rmdir --help' for more information.\n")
            self.exit_code = 1
            return
        for f in self.args:
            pname = self.fs.resolve_path(f, self.cwd)
            try:
                if len(self.fs.get_path(pname)):
                    self.errorWrite(
                        f"rmdir: failed to remove `{f}': Directory not empty\n"
                    )
                    self.exit_code = 1
                    continue
                directory = self.fs.get_path("/".join(pname.split("/")[:-1]))
            except (IndexError, fs.FileNotFound):
                directory = None
            fname = posixpath.basename(f)
            if not directory or fname not in [x[fs.A_NAME] for x in directory]:
                self.errorWrite(
                    f"rmdir: failed to remove `{f}': No such file or directory\n"
                )
                self.exit_code = 1
                continue
            for i in directory[:]:
                if i[fs.A_NAME] == fname:
                    if i[fs.A_TYPE] != fs.T_DIR:
                        self.errorWrite(
                            f"rmdir: failed to remove '{f}': Not a directory\n"
                        )
                        self.exit_code = 1
                        continue
                    directory.remove(i)
                    break


commands["/bin/rmdir"] = Command_rmdir
commands["rmdir"] = Command_rmdir


class Command_pwd(HoneyPotCommand):
    """
    pwd command
    """

    def call(self) -> None:
        self.write(self.cwd + "\n")


commands["/bin/pwd"] = Command_pwd
commands["pwd"] = Command_pwd


class Command_touch(HoneyPotCommand):
    """
    touch command
    """

    def call(self) -> None:
        if not len(self.args):
            self.errorWrite("touch: missing file operand\n")
            self.errorWrite("Try `touch --help' for more information.\n")
            self.exit_code = 1
            return
        for f in self.args:
            pname = self.fs.resolve_path(f, self.cwd)
            if not self.fs.exists(posixpath.dirname(pname)):
                self.errorWrite(
                    f"touch: cannot touch `{pname}`: No such file or directory\n"
                )
                self.exit_code = 1
                continue
            if self.fs.exists(pname):
                # FIXME: modify the timestamp here
                continue
            # can't touch in special directories
            if any([pname.startswith(_p) for _p in fs.SPECIAL_PATHS]):
                self.errorWrite(f"touch: cannot touch `{pname}`: Permission denied\n")
                self.exit_code = 1
                continue

            self.fs.mkfile(pname, self.user["uid"], self.user["gid"], 0, 33188)


commands["/bin/touch"] = Command_touch
commands["touch"] = Command_touch
commands[">"] = Command_touch
