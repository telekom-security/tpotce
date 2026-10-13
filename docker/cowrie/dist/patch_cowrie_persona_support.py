#!/usr/bin/env python3

import argparse
from pathlib import Path


def replace_once(path, old, new):
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"Could not find expected text in {path}")
    if text.count(old) > 1:
        raise RuntimeError(f"Expected text found more than once in {path}: {old.splitlines()[0]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_all(path, old, new):
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"Could not find expected text in {path}")
    path.write_text(text.replace(old, new), encoding="utf-8")


def patch_protocol(cowrie_root):
    path = cowrie_root / "src" / "cowrie" / "shell" / "protocol.py"
    text = path.read_text(encoding="utf-8")
    required_upstream = (
        "from pathlib import Path",
        'txtcmds_path = CowrieConfig.get("honeypot", "txtcmds_path", fallback="")',
        "operator_path = Path(txtcmds_path) / relpath",
    )
    missing = [needle for needle in required_upstream if needle not in text]
    if missing:
        raise RuntimeError(
            f"{path} does not contain upstream txtcmds_path support: {missing}"
        )
    replace_once(
        path,
        "from cowrie.shell import command, fs, honeypot\n",
        """from cowrie.shell import command, fs, honeypot


@functools.cache
def tpot_skip_python_commands() -> frozenset[str]:
    \"\"\"T-Pot: the commands of [shell] skip_python_commands, parsed once (the
    config does not change while Cowrie runs, getCommand runs for every word).\"\"\"
    return frozenset(
        name.strip()
        for name in CowrieConfig.get("shell", "skip_python_commands", fallback="").split(",")
        if name.strip()
    )
""",
    )
    replace_once(
        path,
        """        if cmd in self.commands:
            return self.commands[cmd]
        if cmd[0] in (".", "/"):
            path = self.fs.resolve_path(cmd, cwd)
            if not self.fs.exists(path):
                return None
        else:
            for i in [f"{self.fs.resolve_path(x, cwd)}/{cmd}" for x in paths if x]:
                if self.fs.exists(i):
                    path = i
                    break

        if path is None:
            return None
""",
        """        skip_python_commands = tpot_skip_python_commands()
        # the programs apt / dnf installed in this session ({name: version}) answer
        installed = getattr(self, "tpot_installed", None) or {}

        def skip_python_command(name):
            if Path(name).name in installed:
                return False
            return name in skip_python_commands or Path(name).name in skip_python_commands

        if cmd in self.commands and not skip_python_command(cmd):
            return self.commands[cmd]
        if cmd[0] in (".", "/"):
            path = self.fs.resolve_path(cmd, cwd)
            if not self.fs.exists(path):
                return None
        else:
            for i in [f"{self.fs.resolve_path(x, cwd)}/{cmd}" for x in paths if x]:
                if self.fs.exists(i):
                    path = i
                    break

        if path is None:
            return None
""",
    )
    replace_once(
        path,
        """        relpath = path.lstrip("/")
        txtcmds_path = CowrieConfig.get("honeypot", "txtcmds_path", fallback="")
        if txtcmds_path:
            operator_path = Path(txtcmds_path) / relpath
            if operator_path.is_file():
                return self.txtcmd(operator_path.read_bytes())
""",
        """        # T-Pot: the spellings of path through the directory links of the tree
        # (/bin -> usr/bin): `df` finds /usr/bin/df on PATH, the txtcmd is bin/df.
        import posixpath

        def tpot_real(name):
            parts = [part for part in name.split("/") if part]
            real = []
            for part in parts[:-1]:
                node = self.fs.getfile("/" + "/".join(real + [part]), follow_symlinks=False)
                if node is not None and node[fs.A_TYPE] == fs.T_LINK and node[fs.A_TARGET]:
                    target = node[fs.A_TARGET]
                    base = "" if target.startswith("/") else "/".join(real)
                    real = [x for x in posixpath.normpath(f"/{base}/{target}").split("/") if x]
                else:
                    real.append(part)
            return "/" + "/".join(real + parts[-1:])

        real = tpot_real(path)
        spellings = [path, real]
        for link in self.fs.fs[fs.A_CONTENTS]:
            target = "/" + "/".join(x for x in (link[fs.A_TARGET] or "").split("/") if x)
            if link[fs.A_TYPE] == fs.T_LINK and target != "/" and real.startswith(target + "/"):
                spellings.append("/" + link[fs.A_NAME] + real[len(target):])
        spellings = list(dict.fromkeys(spellings))
        # a program apt installed in this session that neither a txtcmd of the persona nor Cowrie's
        # command of its name answers (the persona does not have it)
        faked = getattr(self, "tpot_faked", None) or {}
        for spelling in spellings:
            if spelling in faked:
                return faked[spelling]

        relpath = path.lstrip("/")
        txtcmds_path = CowrieConfig.get("honeypot", "txtcmds_path", fallback="")
        if txtcmds_path:
            operator_path = Path(txtcmds_path) / relpath
            if operator_path.is_file():
                return self.txtcmd(operator_path.read_bytes())
        if txtcmds_path:
            for spelling in spellings[1:]:
                operator_path = Path(txtcmds_path) / spelling.lstrip("/")
                if operator_path.is_file():
                    return self.txtcmd(operator_path.read_bytes())
        # a Python command of one of the spellings answers before the txtcmds Cowrie ships
        # (data/txtcmds/bin/df, usr/bin/top), as it answers the bare name
        for spelling in spellings:
            if spelling in self.commands and not skip_python_command(spelling):
                return self.commands[spelling]
""",
    )
    replace_once(
        path,
        """        if path in self.commands:
            return self.commands[path]
""",
        """        # (the Python commands of the spellings answered before Cowrie's txtcmds)
""",
    )


def patch_fs_links(cowrie_root):
    """A relative link target is resolved from the directory of the link, as the kernel does
    (/usr/bin/python3 -> python3.12, /etc/os-release -> ../usr/lib/os-release); upstream reads
    it from the root, the form of its own pickle (tpot_personas.render.real_links rewrites it)."""
    path = cowrie_root / "src" / "cowrie" / "shell" / "fs.py"
    replace_once(
        path,
        """                            fileobj = self.getfile(
                                "/".join((cwd, x[A_TARGET])),
                                follow_symlinks=follow_symlinks,
                            )
""",
        """                            fileobj = self.getfile(
                                posixpath.normpath("/".join((cwd, x[A_TARGET]))),
                                follow_symlinks=follow_symlinks,
                            )
""",
    )
    replace_once(path, "            # cwd = '/'.join((cwd, piece))\n", "            cwd = \"/\".join((cwd, piece))\n")


def patch_busybox(cowrie_root):
    """busybox prints the help of the persona's BusyBox ([shell] busybox_help, a
    file of the runtime folder) on stdout, as busybox does, for no arguments and
    --help; applets run and `busybox ECCHI` says "ECCHI: applet not found" as
    upstream (on stderr, exit status 127). Without the key upstream's help stays."""
    path = cowrie_root / "src" / "cowrie" / "commands" / "busybox.py"
    replace_once(
        path,
        "from cowrie.shell.command import HoneyPotCommand\n",
        "from pathlib import Path\n\n"
        "from cowrie.core.config import CowrieConfig\n"
        "from cowrie.shell.command import HoneyPotCommand\n",
    )
    replace_once(
        path,
        """    def help(self) -> None:
        for ln in busybox_help:
            self.errorWrite(f"{ln}\\n")
""",
        """    def help(self) -> None:
        help_path = CowrieConfig.get("shell", "busybox_help", fallback="")
        if help_path:
            try:
                self.write(Path(help_path).read_text(encoding="utf-8"))
                return
            except OSError:
                pass
        for ln in busybox_help:
            self.errorWrite(f"{ln}\\n")
""",
    )
    replace_once(
        path,
        """        if len(self.args) == 0:
            self.help()
""",
        """        if len(self.args) == 0 or self.args == ["--help"]:
            self.help()
""",
    )
    # an unknown applet: busybox says so on stderr and exits with 127
    replace_once(
        path,
        """        if not cmdclass:
            self.write(f"{cmd}: applet not found\\n")
            self.exit()
            return
""",
        """        if not cmdclass:
            self.errorWrite(f"{cmd}: applet not found\\n")
            self.exit(127)
            return
""",
    )


CONFIG_IMPORT = (
    "from cowrie.shell.command import HoneyPotCommand\n",
    "from cowrie.core.config import CowrieConfig\nfrom cowrie.shell.command import HoneyPotCommand\n",
)


# module code of the patched perl.py (tpot_perl_inline and its helpers)
PERL_INLINE = r'''
# T-Pot: perl -e / -E return as perl does. The code is read and never run: print, say
# (-E), exit and die with quoted strings, numbers and scalars set to them act as in
# perl, every other statement is silent. An unterminated string or bracket gives
# perl's compile error; from a regex, backticks, a heredoc, a comment or a quote
# operator on, the code is not read.
TPOT_PERL_SWITCHES = "acfnpsStTuUwWX"
TPOT_PERL_ATTACHED = "CDFiImMdx"
TPOT_CODE_LIMIT = 65536
TPOT_OUTPUT_LIMIT = 1 << 20
TPOT_ABORTED = "Execution of -e aborted due to compilation errors.\n"
TPOT_PERL_STOP = re.compile(r"[/`#<]|(?<![\w$@%&])(?:qq|qw|qx|qr|tr|q|m|s|y)\s*+[^\w\s,;)=]", re.ASCII)
TPOT_PERL_ITEM = r"""(?:"[^"\\]*+(?:\\.[^"\\]*+)*+"|'[^'\\]*+(?:\\.[^'\\]*+)*+'|-?(?:0|[1-9][0-9]{0,14})(?:\.[0-9]{1,15})?|\$\w+|\$\{\w+\})"""
# possessive quantifiers (*+): long whitespace is read once, never backtracked
TPOT_PERL_LIST = re.compile(
    rf"\s*+(\(\s*+)?((?:{TPOT_PERL_ITEM}(?:\s*+,\s*+{TPOT_PERL_ITEM})*+\s*+,?)?)\s*+(?(1)\))\s*+", re.S | re.ASCII
)
TPOT_PERL_ITEMS = re.compile(TPOT_PERL_ITEM, re.S | re.ASCII)
TPOT_PERL_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "e": "\x1b", "f": "\f", "0": "\0"}
TPOT_PERL_INTERPOLATION = re.compile(r"\\x([0-9a-fA-F]{1,2})|\\(.)|\$(\w+|\{\w+\})(\[|\{|->)?|@\w", re.S)


class TpotPerlUnknown(Exception):
    pass


def tpot_perl_inline(cmd) -> bool:
    """T-Pot: perl -e / -E CODE (several are joined by newlines) returns. True when
    handled (the command has exited); False leaves -v, -h, -V, a script file and
    unknown switches to upstream."""
    args = cmd.args
    code: list[str] = []
    flags: set[str] = set()
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in ("-", "--") or not arg.startswith("-") or len(arg) == 1:
            break
        pos = 1
        while pos < len(arg):
            flag = arg[pos]
            if flag in "eE":
                flags.add(flag)
                value = arg[pos + 1:]
                if not value:
                    index += 1
                    if index == len(args):
                        cmd.errorWrite("No code specified for -%s.\n" % flag)
                        cmd.exit(29)
                        return True
                    value = args[index]
                code.append(value)
                break
            if flag in "l0":
                flags.add(flag)
                pos += 1
                while pos < len(arg) and arg[pos] in "01234567":
                    pos += 1
                continue
            if flag in TPOT_PERL_ATTACHED:
                break
            if flag not in TPOT_PERL_SWITCHES:
                return False
            flags.add(flag)
            pos += 1
        index += 1
    if not code:
        return False
    try:
        out, err, status = tpot_perl_code("\n".join(code), flags)
    except Exception:  # noqa: BLE001 - code this does not follow is silent, the command always exits
        out, err, status = b"", "", 0
    try:
        cmd.writeBytes(out)
        cmd.errorWrite(err)
    except Exception:  # noqa: BLE001 - the command always exits
        pass
    finally:
        cmd.exit(status)
    return True


def tpot_perl_split(code: str):
    """(statements as (offset, text), compile error or None): the code split at ;
    outside strings and brackets, up to the first construct this does not read."""
    statements, start, depth, pos = [], 0, 0, 0
    while pos < len(code):
        char = code[pos]
        if char in "\"'":
            end = pos + 1
            while end < len(code) and code[end] != char:
                end += 2 if code[end] == "\\" else 1
            if end >= len(code):
                mark = "'" if char == '"' else '"'
                return [], (f"Can't find string terminator {mark}{char}{mark} anywhere before EOF at -e line "
                            f"{code.count(chr(10), 0, pos) + 1}.\n")
            pos = end + 1
            continue
        if TPOT_PERL_STOP.match(code, pos):
            return statements, None
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
            if depth < 0:
                return [], (f"syntax error at -e line {code.count(chr(10), 0, pos) + 1}, near "
                            f"\"{char}\"\n{TPOT_ABORTED}")
        elif char == ";" and depth == 0:
            statements.append((start, code[start:pos]))
            start = pos + 1
        pos += 1
    if depth:
        return [], f"syntax error at -e line {code.count(chr(10)) + 1}, at EOF\n{TPOT_ABORTED}"
    statements.append((start, code[start:]))
    return statements, None


def tpot_perl_code(code: str, flags) -> tuple[bytes, str, int]:
    """(stdout, stderr, exit status) of perl -e code, read from its text alone."""
    if len(code) > TPOT_CODE_LIMIT:
        return b"", "", 0
    statements, error = tpot_perl_split(code)
    if error:
        if "c" in flags:
            return b"", error.replace(TPOT_ABORTED, "-e had compilation errors.\n"), 255
        return b"", error, 255
    if "c" in flags:
        return b"", "-e syntax OK\n", 0
    if "n" in flags or "p" in flags:
        return b"", "", 0
    names: dict[str, bytes] = {}
    stored = 0
    out: list[bytes] = []
    err: list[str] = []
    budget = TPOT_OUTPUT_LIMIT
    for offset, text in statements:
        match = re.fullmatch(r"\s*+(print|say|exit|die)\b(?:\s*+(STDOUT|STDERR)\b(?!\s*+[,(]))?(.*)", text, re.S)
        if not match:
            assign = re.fullmatch(r"\s*+(?:(?:my|our|local)\s++)?\$(\w+)\s*+=(?![=~])(.*)", text, re.S)
            if assign:
                name = assign.group(1)
                stored -= len(names.pop(name, b""))
                try:
                    (value,) = tpot_perl_list(assign.group(2), names)
                except (TpotPerlUnknown, ValueError):
                    continue
                # all scalars together stay within the output limit
                if stored + len(value) <= TPOT_OUTPUT_LIMIT:
                    names[name] = value
                    stored += len(value)
            continue
        word, stream, rest = match.groups()
        if word == "exit":
            status = re.fullmatch(r"\s*+\(?\s*+(\d{1,9})?\s*+\)?\s*+", rest)
            return b"".join(out), "".join(err), int(status.group(1) or 0) & 0xFF if status else 0
        try:
            items = tpot_perl_list(rest, names) if rest.strip() else [names.get("_", b"")]
        except TpotPerlUnknown:
            if word != "die":
                continue
            items = []
        if word == "die":
            message = b"".join(items).decode("utf-8", "replace") or "Died"
            if not message.endswith("\n"):
                line = code.count("\n", 0, offset + len(text) - len(text.lstrip())) + 1
                message += f" at -e line {line}.\n"
            return b"".join(out), "".join(err) + message, 255
        if word == "say" and "E" not in flags:
            continue
        budget -= sum(map(len, items)) + 1
        if budget < 0:
            break
        data = b"".join(items) + (b"\n" if word == "say" or "l" in flags else b"")
        if stream == "STDERR":
            err.append(data.decode("utf-8", "replace"))
        else:
            out.append(data)
    return b"".join(out), "".join(err), 0


def tpot_perl_list(text: str, names) -> list[bytes]:
    """The values of a list of quoted strings, numbers and scalars; TpotPerlUnknown for
    anything else."""
    match = TPOT_PERL_LIST.fullmatch(text)
    if not match:
        raise TpotPerlUnknown
    return [tpot_perl_value(item, names) for item in TPOT_PERL_ITEMS.findall(match.group(2))]


def tpot_perl_value(item: str, names) -> bytes:
    if item.startswith("'"):
        return re.sub(r"\\([\\'])", r"\1", item[1:-1]).encode("utf-8")
    if item.startswith('"'):
        return tpot_perl_interpolate(item[1:-1], names)
    if item.startswith("$"):
        return tpot_perl_scalar(item[1:].strip("{}"), names)
    return format(float(item), ".15g").encode() if "." in item else item.encode()


def tpot_perl_scalar(name: str, names) -> bytes:
    if name == "0":
        return b"-e"
    if name not in names:
        raise TpotPerlUnknown
    return names[name]


def tpot_perl_interpolate(body: str, names) -> bytes:
    """A "..." string: escapes and $scalar; arrays, elements and unknown scalars are
    TpotPerlUnknown."""
    parts, pos = [], 0
    for match in TPOT_PERL_INTERPOLATION.finditer(body):
        parts.append(body[pos:match.start()].encode("utf-8"))
        pos = match.end()
        if match.group(1):
            parts.append(bytes((int(match.group(1), 16),)))
        elif match.group(2) is not None:
            parts.append(TPOT_PERL_ESCAPES.get(match.group(2), match.group(2)).encode("utf-8"))
        elif match.group(3) and not match.group(4):
            parts.append(tpot_perl_scalar(match.group(3).strip("{}"), names))
        else:
            raise TpotPerlUnknown
    parts.append(body[pos:].encode("utf-8"))
    if sum(map(len, parts)) > TPOT_OUTPUT_LIMIT:
        raise TpotPerlUnknown
    return b"".join(parts)
'''


def patch_perl(cowrie_root):
    """perl -v names the persona's Perl ([shell] perl_version, perl_archname,
    perl_copyright); upstream's 5.14.2 without the keys. perl -e / -E return with
    the output of their print / say / die / exit of literals (PERL_INLINE)."""
    path = cowrie_root / "src" / "cowrie" / "commands" / "perl.py"
    replace_once(path, *CONFIG_IMPORT)
    replace_once(path, "import getopt\n", "import getopt\nimport re\n")
    replace_once(
        path,
        """    def start(self) -> None:
        try:
            opts, args = getopt.gnu_getopt(
                self.args, "acfhnpsStTuUvwWXC:D:e:E:F:i:I:l:m:M:V:X:"
""",
        """    def start(self) -> None:
        if tpot_perl_inline(self):
            return
        try:
            opts, args = getopt.gnu_getopt(
                self.args, "acfhnpsStTuUvwWXC:D:e:E:F:i:I:l:m:M:V:X:"
""",
    )
    replace_once(
        path,
        """            "This is perl 5, version 14, subversion 2 (v5.14.2) built for x86_64-linux-thread-multi",
""",
        """            tpot_perl_banner(),
""",
    )
    replace_once(
        path,
        """            "Copyright 1987-2014, Larry Wall",
""",
        """            f"Copyright 1987-{CowrieConfig.get('shell', 'perl_copyright', fallback='2014')}, Larry Wall",
""",
    )
    replace_once(
        path,
        """commands = {}
""",
        """commands = {}


def tpot_perl_banner() -> str:
    \"\"\"T-Pot: the version line of perl -v for [shell] perl_version / perl_archname.\"\"\"
    version = CowrieConfig.get("shell", "perl_version", fallback="5.14.2")
    arch = CowrieConfig.get("shell", "perl_archname", fallback="x86_64-linux-thread-multi")
    parts = (version.split(".") + ["0", "0"])[:3]
    return f"This is perl {parts[0]}, version {parts[1]}, subversion {parts[2]} (v{version}) built for {arch}"
""" + PERL_INLINE,
    )


# module code of the patched python.py (tpot_python_inline and its helpers)
PYTHON_INLINE = r'''
# T-Pot: python -c CMD and -m MOD return as CPython does. The code is parsed with ast
# and never run: print() / sys.stdout.write() of literals (ast.literal_eval) and of
# names bound to literals print them, sys.exit() / exit() set the status, everything
# else is silent; a syntax error prints CPython's message of the persona's version.
TPOT_PY_FLAGS = "bBdEiIOPqRsStuvx3"
TPOT_PY_VALUE_FLAGS = "WXQ"
TPOT_CODE_LIMIT = 65536
TPOT_OUTPUT_LIMIT = 1 << 20
TPOT_UNKNOWN = object()
TPOT_PY2_PRINT = re.compile(r"(^|[;\n])([ \t]*+)print[ \t]++(?![(=])([^;\n]*)")
# the name a Python 2 print statement is rewritten to (print(a, b) there prints the tuple)
TPOT_PY2_STATEMENT = "tpot_print_statement"


class TpotOutputFull(Exception):
    pass


def tpot_python_version() -> str:
    return CowrieConfig.get("shell", "python_version", fallback="2.7.11+")


def tpot_version_tuple(version: str) -> tuple[int, int]:
    match = re.match(r"(\d+)\.(\d+)", version)
    return (int(match.group(1)), int(match.group(2))) if match else (2, 7)


def tpot_python_inline(cmd) -> bool:
    """T-Pot: -c CMD / -m MOD end the option list as in CPython (the rest is sys.argv).
    True when handled (the command has exited); False leaves -V, -h, --long options,
    a script file, - and unknown options to upstream."""
    args = cmd.args
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in ("-", "--") or not arg.startswith("-") or arg.startswith("--"):
            return False
        for pos in range(1, len(arg)):
            flag = arg[pos]
            if flag in "cm" or flag in TPOT_PY_VALUE_FLAGS:
                value = arg[pos + 1:]
                if not value:
                    index += 1
                    if index == len(args):
                        if flag in "cm":
                            tpot_python_usage(cmd, flag)
                            return True
                        return False
                    value = args[index]
                if flag == "c":
                    tpot_python_run(cmd, value)
                    return True
                if flag == "m":
                    cmd.exit(0)
                    return True
                break
            if flag not in TPOT_PY_FLAGS:
                return False
        index += 1
    return False


def tpot_python_usage(cmd, flag: str) -> None:
    name = "python3" if tpot_version_tuple(tpot_python_version())[0] >= 3 else "python"
    cmd.errorWrite(
        f"Argument expected for the -{flag} option\n"
        f"usage: {name} [option] ... [-c cmd | -m mod | file | -] [arg] ...\n"
        "Try `python -h' for more information.\n"
    )
    cmd.exit(2)


def tpot_python_run(cmd, code: str) -> None:
    try:
        out, err, status = tpot_python_code(code, tpot_python_version())
    except Exception:  # noqa: BLE001 - code this does not follow is silent, the command always exits
        out, err, status = "", "", 0
    try:
        # stdout of a terminal is line buffered: its last line without newline comes at the exit, after
        # stderr; what UTF-8 cannot carry is an escape (stderr's backslashreplace, a Python 2 byte string)
        head, tail = out[:out.rfind("\n") + 1], out[out.rfind("\n") + 1:]
        if head:
            cmd.writeBytes(head.encode("utf-8", "backslashreplace"))
        if err:
            cmd.errorWrite(err.encode("utf-8", "backslashreplace").decode("utf-8"))
        if tail:
            cmd.writeBytes(tail.encode("utf-8", "backslashreplace"))
    except Exception:  # noqa: BLE001 - the command always exits
        pass
    finally:
        cmd.exit(status)


def tpot_python_code(code: str, version: str) -> tuple[str, str, int]:
    """(stdout, stderr, exit status) of python -c code, from its syntax tree alone."""
    if len(code) > TPOT_CODE_LIMIT:
        return "", "", 0
    python2 = tpot_version_tuple(version)[0] < 3
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                module = ast.parse(code, "<string>")
            except SyntaxError as error:
                module = None
                if python2:
                    # print statements; other Python 2 syntax stays silent unless the code is cut off
                    try:
                        module = ast.parse(TPOT_PY2_PRINT.sub(tpot_py2_print, code), "<string>")
                    except SyntaxError:
                        if not tpot_incomplete(error):
                            return "", "", 0
                if module is None:
                    return "", tpot_syntax_error(error, version), 1
    except Exception:  # noqa: BLE001 - nesting or memory limits of the parser: silent
        return "", "", 0
    future = any(isinstance(node, ast.ImportFrom) and node.module == "__future__"
                 and any(alias.name == "print_function" for alias in node.names) for node in module.body)
    return tpot_python_statements(module.body, python2 and not future, version, code)


def tpot_py2_print(match) -> str:
    return f"{match.group(1)}{match.group(2)}{TPOT_PY2_STATEMENT}({match.group(3).rstrip().rstrip(',')})"


def tpot_incomplete(error) -> bool:
    return any(text in (error.msg or "") for text in ("never closed", "unterminated", "EOF", "EOL", "indented block"))


def tpot_syntax_error(error, version: str) -> str:
    """CPython's report of a syntax error in -c code; Python < 3.10 (and 2.7) with
    their messages and one caret."""
    line = (error.text or "").split("\n")[0].rstrip("\r")
    stripped = line.lstrip()
    indent = len(line) - len(stripped)
    start = (error.offset or 1) - 1 - indent
    width = 1
    msg = error.msg or "invalid syntax"
    if tpot_version_tuple(version) < (3, 10):
        if "was never closed" in msg:
            msg, start = "unexpected EOF while parsing", len(stripped)
        elif msg.startswith("unterminated triple-quoted string literal"):
            msg, start = "EOF while scanning triple-quoted string literal", len(stripped)
        elif msg.startswith("unterminated string literal"):
            msg, start = "EOL while scanning string literal", len(stripped)
        elif msg.startswith("Missing parentheses in call to 'print'"):
            found = re.match(r"print\s+(.*)", stripped)
            if found:
                msg = f"Missing parentheses in call to 'print'. Did you mean print({found.group(1).strip()})?"
                start = found.start(1)
        elif msg.startswith("expected an indented block"):
            msg = "expected an indented block"
        elif "Perhaps you forgot a comma" in msg:
            msg = "invalid syntax"
            start = (error.end_offset or error.offset or 1) - 2 - indent
        elif msg.startswith(("invalid syntax", "expected ':'")):
            msg = "invalid syntax"
    elif error.end_offset and error.end_lineno == error.lineno and error.offset:
        width = max(1, min(error.end_offset - error.offset, len(stripped) - max(start, 0)))
    start = max(0, min(start, len(stripped)))
    return (
        f'  File "<string>", line {error.lineno or 1}\n'
        f"    {stripped}\n"
        f"    {' ' * start}{'^' * width}\n"
        f"{type(error).__name__}: {msg}\n"
    )


def tpot_dotted(node) -> str | None:
    parts = []
    while isinstance(node, ast.Attribute) and len(parts) < 8:
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def tpot_literal(node, names):
    if isinstance(node, ast.Name) and node.id in names:
        return names[node.id]
    try:
        return ast.literal_eval(node)
    except Exception:  # noqa: BLE001 - anything that is no literal
        return TPOT_UNKNOWN


def tpot_encode_error(error, call, version: str, code: str) -> str:
    """CPython's traceback of a write to stdout that UTF-8 cannot carry; 3.13 and later show the
    lines of the call, ~ under the function and ^ under its arguments."""
    lines = ["Traceback (most recent call last):\n", f'  File "<string>", line {call.lineno}, in <module>\n']
    source = re.split(r"\r\n|\r|\n", code)
    if tpot_version_tuple(version) >= (3, 13) and call.end_lineno <= len(source):
        rows = source[call.lineno - 1:call.end_lineno]

        def column(row, offset):
            # ast counts columns in UTF-8 bytes
            return len(row.encode("utf-8", "surrogatepass")[:offset].decode("utf-8", "surrogatepass"))

        start = (call.lineno, column(rows[0], call.col_offset))
        func_end = (call.func.end_lineno, column(source[call.func.end_lineno - 1], call.func.end_col_offset))
        end = (call.end_lineno, column(rows[-1], call.end_col_offset))
        indent = min(len(row) - len(row.lstrip()) for row in rows if row.strip())
        for number, row in enumerate(rows, call.lineno):
            first = start[1] if number == start[0] else len(row) - len(row.lstrip())
            last = end[1] if number == end[0] else len(row.rstrip())
            marks = "".join("~" if (number, pos) < func_end else "^" for pos in range(first, last))
            lines.append(f"    {row[indent:].rstrip()}\n    {' ' * (first - indent)}{marks}\n")
    return "".join(lines) + f"UnicodeEncodeError: {error}\n"


def tpot_python_statements(body, print_tuple=False, version="3.12.3", code="") -> tuple[str, str, int]:
    """print_tuple: print(a, b) prints the tuple (a, b), as a Python 2 print statement. Python 3
    ends at a write to stdout that UTF-8 cannot carry, as CPython's strict stdout does."""
    names: dict = {}
    out: list[str] = []
    err: list[str] = []
    budget = [TPOT_OUTPUT_LIMIT]
    strict = tpot_version_tuple(version)[0] >= 3

    def stdout(pieces, call):
        # each piece is one write of CPython (print writes every value, sep and end on their own)
        for piece in pieces:
            if strict:
                try:
                    piece.encode("utf-8")
                except UnicodeEncodeError as error:
                    err.append(tpot_encode_error(error, call, version, code))
                    return False
            out.append(piece)
        return True

    try:
        for node in body:
            if isinstance(node, ast.Assign):
                value = tpot_literal(node.value, names)
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        if value is TPOT_UNKNOWN:
                            names.pop(target.id, None)
                        else:
                            names[target.id] = value
                continue
            if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)):
                continue
            call = node.value
            func = tpot_dotted(call.func)
            if func in ("exit", "quit", "sys.exit", "os._exit"):
                status = tpot_literal(call.args[0], names) if call.args else None
                if status is None or status is TPOT_UNKNOWN:
                    return "".join(out), "".join(err), 0
                if isinstance(status, int):
                    return "".join(out), "".join(err), status & 0xFF
                err.append(tpot_text(status, budget) + "\n")
                return "".join(out), "".join(err), 1
            if func == "print" and print_tuple and (call.keywords or len(call.args) != 1):
                printed = None if call.keywords else tpot_print_tuple(call, names, budget)
                if printed is not None:
                    out.append(printed)
            elif func in ("print", TPOT_PY2_STATEMENT):
                printed = tpot_print(call, names, budget)
                if printed is not None:
                    if printed[1]:
                        err.append("".join(printed[0]))
                    elif not stdout(printed[0], call):
                        return "".join(out), "".join(err), 1
            elif func in ("sys.stdout.write", "sys.stderr.write") and len(call.args) == 1 and not call.keywords:
                value = tpot_literal(call.args[0], names)
                if isinstance(value, str):
                    if func == "sys.stderr.write":
                        err.append(tpot_text(value, budget))
                    elif not stdout([tpot_text(value, budget)], call):
                        return "".join(out), "".join(err), 1
    except TpotOutputFull:
        pass
    return "".join(out), "".join(err), 0


def tpot_print_tuple(call, names, budget):
    """The line of a Python 2 print statement of the tuple in print(a, b), None if it is no literal."""
    values = [tpot_literal(arg, names) for arg in call.args]
    if any(value is TPOT_UNKNOWN for value in values) or any(isinstance(arg, ast.Starred) for arg in call.args):
        return None
    try:
        # each value charged on its own, so the output limit stops before the whole text is built
        parts = [tpot_text(repr(value), budget) for value in values]
    except TpotOutputFull:
        raise
    except Exception:  # noqa: BLE001 - repr() of a literal CPython would print (digits limit)
        return None
    tpot_text(" " * (2 * len(parts) + 1), budget)
    return "(" + ", ".join(parts) + ")\n"


def tpot_text(value, budget) -> str:
    text = str(value)
    budget[0] -= len(text)
    if budget[0] < 0:
        raise TpotOutputFull
    return text


def tpot_print(call, names, budget):
    """(the writes of the text, to stderr) of a print() call with literal arguments, None otherwise."""
    values = []
    for arg in call.args:
        value = tpot_literal(arg, names)
        if value is TPOT_UNKNOWN or isinstance(arg, ast.Starred):
            return None
        values.append(value)
    sep, end, stderr = " ", "\n", False
    for keyword in call.keywords:
        if keyword.arg == "file":
            stream = tpot_dotted(keyword.value)
            if stream not in ("sys.stdout", "sys.stderr"):
                return None
            stderr = stream == "sys.stderr"
            continue
        if keyword.arg == "flush":
            continue
        value = tpot_literal(keyword.value, names)
        if keyword.arg not in ("sep", "end") or not (value is None or isinstance(value, str)):
            return None
        if value is not None:
            if keyword.arg == "sep":
                sep = value
            else:
                end = value
    try:
        parts = [tpot_text(value, budget) for value in values]
    except TpotOutputFull:
        raise
    except Exception:  # noqa: BLE001 - str() of a literal CPython would print (digits limit)
        return None
    budget[0] -= len(sep) * max(0, len(parts) - 1) + len(end)
    if budget[0] < 0:
        raise TpotOutputFull
    writes = [piece for index, part in enumerate(parts) for piece in ((sep, part) if index else (part,))]
    return writes + [end], stderr
'''


def patch_python(cowrie_root):
    """python -V prints [shell] python_version (Python 2 on stderr, as Python 2 does);
    python3 and /usr/bin/python3 are the same command (skip_python_commands keeps the
    one the persona does not have away). python -c / -m return with the output of
    their literal print() calls or the syntax error of that Python (PYTHON_INLINE)."""
    path = cowrie_root / "src" / "cowrie" / "commands" / "python.py"
    replace_once(path, *CONFIG_IMPORT)
    replace_once(path, "import getopt\n", "import ast\nimport getopt\nimport re\nimport warnings\n")
    replace_once(path, "commands = {}\n", "commands = {}\n\n" + PYTHON_INLINE)
    replace_once(
        path,
        """    def start(self) -> None:
        try:
            opts, args = getopt.gnu_getopt(
                self.args, "BdEhiORsStuvVx3c:m:Q:W:", ["help", "version"]
""",
        """    def start(self) -> None:
        if tpot_python_inline(self):
            return
        try:
            opts, args = getopt.gnu_getopt(
                self.args, "BdEhiORsStuvVx3c:m:Q:W:", ["help", "version"]
""",
    )
    replace_once(
        path,
        """        ver = "Python 2.7.11+"
        self.write(ver + "\\n")
""",
        """        ver = "Python " + CowrieConfig.get("shell", "python_version", fallback="2.7.11+")
        if ver.startswith("Python 2"):
            self.errorWrite(ver + "\\n")
        else:
            self.write(ver + "\\n")
""",
    )
    replace_once(
        path,
        """commands["/usr/bin/python"] = Command_python
commands["python"] = Command_python
""",
        """commands["/usr/bin/python"] = Command_python
commands["python"] = Command_python
commands["/usr/bin/python3"] = Command_python
commands["python3"] = Command_python
""",
    )


def patch_git(cowrie_root):
    """git --version names [shell] git_version, else that of a git the session installed
    (upstream's 2.34.1 without both)."""
    path = cowrie_root / "src" / "cowrie" / "commands" / "git.py"
    replace_once(path, *CONFIG_IMPORT)
    replace_once(
        path,
        """            self.write("git version 2.34.1\\n")
""",
        """            version = (CowrieConfig.get("shell", "git_version", fallback="")
                       or (getattr(self.protocol, "tpot_installed", None) or {}).get("git") or "2.34.1")
            self.write(f"git version {version}\\n")
""",
    )


def patch_which(cowrie_root):
    """which prints the first match on PATH (/usr/bin and /bin are one directory of a merged
    /usr tree), with -a every match, with -s nothing; a name with a slash is itself. It fails
    with 1 when a name is not found, as `which wget || which curl` of a bot expects."""
    path = cowrie_root / "src" / "cowrie" / "commands" / "which.py"
    replace_once(
        path,
        """        # Look up each file
        for f in self.args:
            for path in self.environ["PATH"].split(":"):
                resolved = self.fs.resolve_path(f, path)

                if self.fs.exists(resolved):
                    self.write(f"{path}/{f}\\n")
""",
        """        # Look up each file (T-Pot: -a every match on PATH, -s only the status)
        every = silent = False
        names = list(self.args)
        while names and names[0].startswith("-") and len(names[0]) > 1:
            option = names.pop(0)
            if option == "--":
                break
            every = every or "a" in option
            silent = silent or "s" in option
        missing = not names
        for f in names:
            found = []
            if "/" in f:
                found = [f] if self.fs.exists(self.fs.resolve_path(f, self.cwd)) else []
            else:
                for path in self.environ["PATH"].split(":"):
                    resolved = self.fs.resolve_path(f, path)

                    if self.fs.exists(resolved):
                        found.append(f"{path}/{f}")
                        if not every:
                            break
            if not silent:
                for match in found:
                    self.write(f"{match}\\n")
            missing = missing or not found
        if missing:
            self.exit_code = 1
""",
    )


def patch_ssh(cowrie_root):
    path = cowrie_root / "src" / "cowrie" / "commands" / "ssh.py"
    replace_once(
        path,
        """        self.write(
            f"Linux {self.protocol.hostname} 2.6.26-2-686 #1 SMP Wed Nov 4 20:45:37 \\
            UTC 2009 i686\\n"
        )
""",
        """        kernel_version = CowrieConfig.get("shell", "kernel_version", fallback="5.10.0")
        kernel_build = CowrieConfig.get("shell", "kernel_build_string", fallback="#1 SMP")
        hardware = CowrieConfig.get("shell", "hardware_platform", fallback="x86_64")
        operating_system = CowrieConfig.get("shell", "operating_system", fallback="GNU/Linux")
        self.write(
            f"Linux {self.protocol.hostname} {kernel_version} {kernel_build} "
            f"{hardware} {operating_system}\\n"
        )
""",
    )


def patch_ssh_factory(cowrie_root):
    """The server host key algorithms are those of the emulated server ([ssh] host_key_algorithms,
    in its order), not one per loaded key: OpenSSH 8.8 offers its RSA key as rsa-sha2-512,
    rsa-sha2-256, OpenSSH 7.4 as ssh-rsa,rsa-sha2-512,rsa-sha2-256. Twisted signs rsa-sha2-* with
    the ssh-rsa key (SSHServerTransport._getHostKeys); a name without a loaded key or unknown to
    Twisted is left out."""
    path = cowrie_root / "src" / "cowrie" / "ssh" / "factory.py"
    replace_once(
        path,
        """

class CowrieSSHFactory(factory.SSHFactory):
""",
        """

# T-Pot: the key an rsa-sha2 signature algorithm signs with
TPOT_KEY_TYPES = {b"rsa-sha2-256": b"ssh-rsa", b"rsa-sha2-512": b"ssh-rsa"}


def tpot_host_key_algorithms(private_keys) -> list[bytes]:
    \"\"\"T-Pot: the host key algorithms of [ssh] host_key_algorithms that Twisted knows and a loaded
    key signs, in their order; without the key, one per loaded key (upstream).\"\"\"
    names = [
        name.strip().encode("utf-8")
        for name in CowrieConfig.get("ssh", "host_key_algorithms", fallback="").split(",")
        if name.strip()
    ]
    known = set(transport.SSHTransportBase.supportedPublicKeys)
    offered = [
        name
        for name in dict.fromkeys(names)
        if name in known and TPOT_KEY_TYPES.get(name, name) in private_keys
    ]
    return offered or list(private_keys.keys())


class CowrieSSHFactory(factory.SSHFactory):
""",
    )
    replace_once(
        path,
        "        t.supportedPublicKeys = list(self.privateKeys.keys())\n",
        "        t.supportedPublicKeys = tpot_host_key_algorithms(self.privateKeys)\n",
    )


def patch_netstat(cowrie_root):
    path = cowrie_root / "src" / "cowrie" / "commands" / "netstat.py"
    replace_all(path, "@/com/ubuntu/upstart", "/run/systemd/private")
    replace_once(
        path,
        "Fred Baumgarten, Alan Cox, Bernd Eckenfels, Phil Blundell, Tuan Hoang and others\\n",
        "Fred Baumgarten, Alan Cox, Bernd Eckenfels, Tuan Hoang and others\\n",
    )


# module code of the patched commands/netstat.py after its commands: ip, ss, netstat (and ifconfig,
# which the patched ifconfig.py takes from here) of the persona
NET_COMMAND = r'''

# T-Pot: ip, ss, netstat and ifconfig of the persona. [shell] network is a JSON file of the runtime
# folder (tpot_personas.network): the interfaces with their MAC and address, the routes, the
# sockets of the daemons of ps and the style of each program (iproute2, net-tools or BusyBox and
# its version). The session's own connection is the one of the protocol. Nothing of the host is read.
import functools
import ipaddress
import json
import time
from pathlib import Path

from twisted.python import log

from cowrie.core.config import CowrieConfig

TPOT_NET_SERVICES = {22: "ssh", 23: "telnet", 25: "smtp", 53: "domain", 67: "bootps", 68: "bootpc", 80: "http",
                     137: "netbios-ns", 138: "netbios-dgm", 139: "netbios-ssn", 443: "https", 445: "microsoft-ds",
                     547: "dhcpv6-server", 554: "rtsp", 5353: "mdns", 8080: "http-alt"}
TPOT_NET_OBJECTS = ("address", "addrlabel", "maddress", "route", "rule", "neighbor", "neighbour", "ntable", "ntbl",
                    "link", "l2tp", "fou", "ila", "macsec", "tunnel", "tunl", "tuntap", "tap", "token",
                    "tcpmetrics", "tcp_metrics", "monitor", "xfrm", "mroute", "mrule", "netns", "netconf", "vrf",
                    "sr", "nexthop", "mptcp", "ioam", "help", "stats")
TPOT_IP_USAGE = {
    '4.11': 'Usage: ip [ OPTIONS ] OBJECT { COMMAND | help }\n       ip [ -force ] -batch filename\nwhere  OBJECT := { link | address | addrlabel | route | rule | neigh | ntable |\n                   tunnel | tuntap | maddress | mroute | mrule | monitor | xfrm |\n                   netns | l2tp | fou | macsec | tcp_metrics | token | netconf | ila |\n                   vrf }\n       OPTIONS := { -V[ersion] | -s[tatistics] | -d[etails] | -r[esolve] |\n                    -h[uman-readable] | -iec |\n                    -f[amily] { inet | inet6 | ipx | dnet | mpls | bridge | link } |\n                    -4 | -6 | -I | -D | -B | -0 |\n                    -l[oops] { maximum-addr-flush-attempts } | -br[ief] |\n                    -o[neline] | -t[imestamp] | -ts[hort] | -b[atch] [filename] |\n                    -rc[vbuf] [size] | -n[etns] name | -a[ll] |\xa0-c[olor]}\n',
    '5.15': 'Usage: ip [ OPTIONS ] OBJECT { COMMAND | help }\n       ip [ -force ] -batch filename\nwhere  OBJECT := { address | addrlabel | fou | help | ila | ioam | l2tp | link |\n                   macsec | maddress | monitor | mptcp | mroute | mrule |\n                   neighbor | neighbour | netconf | netns | nexthop | ntable |\n                   ntbl | route | rule | sr | tap | tcpmetrics |\n                   token | tunnel | tuntap | vrf | xfrm }\n       OPTIONS := { -V[ersion] | -s[tatistics] | -d[etails] | -r[esolve] |\n                    -h[uman-readable] | -iec | -j[son] | -p[retty] |\n                    -f[amily] { inet | inet6 | mpls | bridge | link } |\n                    -4 | -6 | -M | -B | -0 |\n                    -l[oops] { maximum-addr-flush-attempts } | -br[ief] |\n                    -o[neline] | -t[imestamp] | -ts[hort] | -b[atch] [filename] |\n                    -rc[vbuf] [size] | -n[etns] name | -N[umeric] | -a[ll] |\n                    -c[olor]}\n',
    '6.1': 'Usage: ip [ OPTIONS ] OBJECT { COMMAND | help }\n       ip [ -force ] -batch filename\nwhere  OBJECT := { address | addrlabel | amt | fou | help | ila | ioam | l2tp |\n                   link | macsec | maddress | monitor | mptcp | mroute | mrule |\n                   neighbor | neighbour | netconf | netns | nexthop | ntable |\n                   ntbl | route | rule | sr | tap | tcpmetrics |\n                   token | tunnel | tuntap | vrf | xfrm }\n       OPTIONS := { -V[ersion] | -s[tatistics] | -d[etails] | -r[esolve] |\n                    -h[uman-readable] | -iec | -j[son] | -p[retty] |\n                    -f[amily] { inet | inet6 | mpls | bridge | link } |\n                    -4 | -6 | -M | -B | -0 |\n                    -l[oops] { maximum-addr-flush-attempts } | -br[ief] |\n                    -o[neline] | -t[imestamp] | -ts[hort] | -b[atch] [filename] |\n                    -rc[vbuf] [size] | -n[etns] name | -N[umeric] | -a[ll] |\n                    -c[olor]}\n',
    '6.15': 'Usage: ip [ OPTIONS ] OBJECT { COMMAND | help }\n       ip [ -force ] -batch filename\nwhere  OBJECT := { address | addrlabel | fou | help | ila | ioam | l2tp | link |\n                   macsec | maddress | monitor | mptcp | mroute | mrule |\n                   neighbor | neighbour | netconf | netns | nexthop | ntable |\n                   ntbl | route | rule | sr | stats | tap | tcpmetrics |\n                   token | tunnel | tuntap | vrf | xfrm }\n       OPTIONS := { -V[ersion] | -s[tatistics] | -d[etails] | -r[esolve] |\n                    -h[uman-readable] | -iec | -j[son] | -p[retty] |\n                    -f[amily] { inet | inet6 | mpls | bridge | link } |\n                    -4 | -6 | -M | -B | -0 |\n                    -l[oops] { maximum-addr-flush-attempts } | -echo | -br[ief] |\n                    -o[neline] | -t[imestamp] | -ts[hort] | -b[atch] [filename] |\n                    -rc[vbuf] [size] | -n[etns] name | -N[umeric] | -a[ll] |\n                    -c[olor]}\n',
}

TPOT_SS_HELP = {
    '4.11': "Usage: ss [ OPTIONS ]\n       ss [ OPTIONS ] [ FILTER ]\n   -h, --help          this message\n   -V, --version       output version information\n   -n, --numeric       don't resolve service names\n   -r, --resolve       resolve host names\n   -a, --all           display all sockets\n   -l, --listening     display listening sockets\n   -o, --options       show timer information\n   -e, --extended      show detailed socket information\n   -m, --memory        show socket memory usage\n   -p, --processes     show process using socket\n   -i, --info          show internal TCP information\n   -s, --summary       show socket usage summary\n   -b, --bpf           show bpf filter socket information\n   -E, --events        continually display sockets as they are destroyed\n   -Z, --context       display process SELinux security contexts\n   -z, --contexts      display process and socket SELinux security contexts\n   -N, --net           switch to the specified network namespace name\n\n   -4, --ipv4          display only IP version 4 sockets\n   -6, --ipv6          display only IP version 6 sockets\n   -0, --packet        display PACKET sockets\n   -t, --tcp           display only TCP sockets\n   -S, --sctp          display only SCTP sockets\n   -u, --udp           display only UDP sockets\n   -d, --dccp          display only DCCP sockets\n   -w, --raw           display only RAW sockets\n   -x, --unix          display only Unix domain sockets\n       --vsock         display only vsock sockets\n   -f, --family=FAMILY display sockets of type FAMILY\n       FAMILY := {inet|inet6|link|unix|netlink|vsock|help}\n\n   -K, --kill          forcibly close sockets, display what was closed\n   -H, --no-header     Suppress header line\n\n   -A, --query=QUERY, --socket=QUERY\n       QUERY := {all|inet|tcp|udp|raw|unix|unix_dgram|unix_stream|unix_seqpacket|packet|netlink|vsock_stream|vsock_dgram}[,QUERY]\n\n   -D, --diag=FILE     Dump raw information about TCP sockets to FILE\n   -F, --filter=FILE   read filter information from FILE\n       FILTER := [ state STATE-FILTER ] [ EXPRESSION ]\n       STATE-FILTER := {all|connected|synchronized|bucket|big|TCP-STATES}\n         TCP-STATES := {established|syn-sent|syn-recv|fin-wait-{1,2}|time-wait|closed|close-wait|last-ack|listen|closing}\n          connected := {established|syn-sent|syn-recv|fin-wait-{1,2}|time-wait|close-wait|last-ack|closing}\n       synchronized := {established|syn-recv|fin-wait-{1,2}|time-wait|close-wait|last-ack|closing}\n             bucket := {syn-recv|time-wait}\n                big := {established|syn-sent|fin-wait-{1,2}|closed|close-wait|last-ack|listen|closing}\n",
    '5.15': "Usage: ss [ OPTIONS ]\n       ss [ OPTIONS ] [ FILTER ]\n   -h, --help          this message\n   -V, --version       output version information\n   -n, --numeric       don't resolve service names\n   -r, --resolve       resolve host names\n   -a, --all           display all sockets\n   -l, --listening     display listening sockets\n   -o, --options       show timer information\n   -e, --extended      show detailed socket information\n   -m, --memory        show socket memory usage\n   -p, --processes     show process using socket\n   -i, --info          show internal TCP information\n       --tipcinfo      show internal tipc socket information\n   -s, --summary       show socket usage summary\n       --tos           show tos and priority information\n       --cgroup        show cgroup information\n   -b, --bpf           show bpf filter socket information\n   -E, --events        continually display sockets as they are destroyed\n   -Z, --context       display process SELinux security contexts\n   -z, --contexts      display process and socket SELinux security contexts\n   -N, --net           switch to the specified network namespace name\n\n   -4, --ipv4          display only IP version 4 sockets\n   -6, --ipv6          display only IP version 6 sockets\n   -0, --packet        display PACKET sockets\n   -t, --tcp           display only TCP sockets\n   -M, --mptcp         display only MPTCP sockets\n   -S, --sctp          display only SCTP sockets\n   -u, --udp           display only UDP sockets\n   -d, --dccp          display only DCCP sockets\n   -w, --raw           display only RAW sockets\n   -x, --unix          display only Unix domain sockets\n       --tipc          display only TIPC sockets\n       --vsock         display only vsock sockets\n   -f, --family=FAMILY display sockets of type FAMILY\n       FAMILY := {inet|inet6|link|unix|netlink|vsock|tipc|xdp|help}\n\n   -K, --kill          forcibly close sockets, display what was closed\n   -H, --no-header     Suppress header line\n   -O, --oneline       socket's data printed on a single line\n       --inet-sockopt  show various inet socket options\n\n   -A, --query=QUERY, --socket=QUERY\n       QUERY := {all|inet|tcp|mptcp|udp|raw|unix|unix_dgram|unix_stream|unix_seqpacket|packet|netlink|vsock_stream|vsock_dgram|tipc}[,QUERY]\n\n   -D, --diag=FILE     Dump raw information about TCP sockets to FILE\n   -F, --filter=FILE   read filter information from FILE\n       FILTER := [ state STATE-FILTER ] [ EXPRESSION ]\n       STATE-FILTER := {all|connected|synchronized|bucket|big|TCP-STATES}\n         TCP-STATES := {established|syn-sent|syn-recv|fin-wait-{1,2}|time-wait|closed|close-wait|last-ack|listening|closing}\n          connected := {established|syn-sent|syn-recv|fin-wait-{1,2}|time-wait|close-wait|last-ack|closing}\n       synchronized := {established|syn-recv|fin-wait-{1,2}|time-wait|close-wait|last-ack|closing}\n             bucket := {syn-recv|time-wait}\n                big := {established|syn-sent|fin-wait-{1,2}|closed|close-wait|last-ack|listening|closing}\n",
    '6.1': "Usage: ss [ OPTIONS ]\n       ss [ OPTIONS ] [ FILTER ]\n   -h, --help          this message\n   -V, --version       output version information\n   -n, --numeric       don't resolve service names\n   -r, --resolve       resolve host names\n   -a, --all           display all sockets\n   -l, --listening     display listening sockets\n   -o, --options       show timer information\n   -e, --extended      show detailed socket information\n   -m, --memory        show socket memory usage\n   -p, --processes     show process using socket\n   -T, --threads       show thread using socket\n   -i, --info          show internal TCP information\n       --tipcinfo      show internal tipc socket information\n   -s, --summary       show socket usage summary\n       --tos           show tos and priority information\n       --cgroup        show cgroup information\n   -b, --bpf           show bpf filter socket information\n   -E, --events        continually display sockets as they are destroyed\n   -Z, --context       display task SELinux security contexts\n   -z, --contexts      display task and socket SELinux security contexts\n   -N, --net           switch to the specified network namespace name\n\n   -4, --ipv4          display only IP version 4 sockets\n   -6, --ipv6          display only IP version 6 sockets\n   -0, --packet        display PACKET sockets\n   -t, --tcp           display only TCP sockets\n   -M, --mptcp         display only MPTCP sockets\n   -S, --sctp          display only SCTP sockets\n   -u, --udp           display only UDP sockets\n   -d, --dccp          display only DCCP sockets\n   -w, --raw           display only RAW sockets\n   -x, --unix          display only Unix domain sockets\n       --tipc          display only TIPC sockets\n       --vsock         display only vsock sockets\n       --xdp           display only XDP sockets\n   -f, --family=FAMILY display sockets of type FAMILY\n       FAMILY := {inet|inet6|link|unix|netlink|vsock|tipc|xdp|help}\n\n   -K, --kill          forcibly close sockets, display what was closed\n   -H, --no-header     Suppress header line\n   -O, --oneline       socket's data printed on a single line\n       --inet-sockopt  show various inet socket options\n\n   -A, --query=QUERY, --socket=QUERY\n       QUERY := {all|inet|tcp|mptcp|udp|raw|unix|unix_dgram|unix_stream|unix_seqpacket|packet|packet_raw|packet_dgram|netlink|dccp|sctp|vsock_stream|vsock_dgram|tipc|xdp}[,QUERY]\n\n   -D, --diag=FILE     Dump raw information about TCP sockets to FILE\n   -F, --filter=FILE   read filter information from FILE\n       FILTER := [ state STATE-FILTER ] [ EXPRESSION ]\n       STATE-FILTER := {all|connected|synchronized|bucket|big|TCP-STATES}\n         TCP-STATES := {established|syn-sent|syn-recv|fin-wait-{1,2}|time-wait|closed|close-wait|last-ack|listening|closing}\n          connected := {established|syn-sent|syn-recv|fin-wait-{1,2}|time-wait|close-wait|last-ack|closing}\n       synchronized := {established|syn-recv|fin-wait-{1,2}|time-wait|close-wait|last-ack|closing}\n             bucket := {syn-recv|time-wait}\n                big := {established|syn-sent|fin-wait-{1,2}|closed|close-wait|last-ack|listening|closing}\n",
    '6.15': "Usage: ss [ OPTIONS ]\n       ss [ OPTIONS ] [ FILTER ]\n   -h, --help          this message\n   -V, --version       output version information\n   -n, --numeric       don't resolve service names\n   -r, --resolve       resolve host names\n   -a, --all           display all sockets\n   -l, --listening     display listening sockets\n   -B, --bound-inactive display TCP bound but inactive sockets\n   -o, --options       show timer information\n   -e, --extended      show detailed socket information\n   -m, --memory        show socket memory usage\n   -p, --processes     show process using socket\n   -T, --threads       show thread using socket\n   -i, --info          show internal TCP information\n       --tipcinfo      show internal tipc socket information\n   -s, --summary       show socket usage summary\n       --tos           show tos and priority information\n       --cgroup        show cgroup information\n   -b, --bpf           show bpf filter socket information\n       --bpf-maps      show all BPF socket-local storage maps\n       --bpf-map-id=MAP-ID    show a BPF socket-local storage map\n   -E, --events        continually display sockets as they are destroyed\n   -Z, --context       display task SELinux security contexts\n   -z, --contexts      display task and socket SELinux security contexts\n   -N, --net           switch to the specified network namespace name\n\n   -4, --ipv4          display only IP version 4 sockets\n   -6, --ipv6          display only IP version 6 sockets\n   -0, --packet        display PACKET sockets\n   -t, --tcp           display only TCP sockets\n   -M, --mptcp         display only MPTCP sockets\n   -S, --sctp          display only SCTP sockets\n   -u, --udp           display only UDP sockets\n   -d, --dccp          display only DCCP sockets\n   -w, --raw           display only RAW sockets\n   -x, --unix          display only Unix domain sockets\n       --tipc          display only TIPC sockets\n       --vsock         display only vsock sockets\n       --xdp           display only XDP sockets\n   -f, --family=FAMILY display sockets of type FAMILY\n       FAMILY := {inet|inet6|link|unix|netlink|vsock|tipc|xdp|help}\n\n   -K, --kill          forcibly close sockets, display what was closed\n   -H, --no-header     Suppress header line\n   -Q, --no-queues     Suppress sending and receiving queue columns\n   -O, --oneline       socket's data printed on a single line\n       --inet-sockopt  show various inet socket options\n\n   -A, --query=QUERY, --socket=QUERY\n       QUERY := {all|inet|tcp|mptcp|udp|raw|unix|unix_dgram|unix_stream|unix_seqpacket|packet|packet_raw|packet_dgram|netlink|dccp|sctp|vsock_stream|vsock_dgram|tipc|xdp}[,QUERY]\n\n   -D, --diag=FILE     Dump raw information about TCP sockets to FILE\n   -F, --filter=FILE   read filter information from FILE\n       FILTER := [ state STATE-FILTER ] [ EXPRESSION ]\n       STATE-FILTER := {all|connected|synchronized|bucket|big|TCP-STATES}\n         TCP-STATES := {established|syn-sent|syn-recv|fin-wait-{1,2}|time-wait|closed|close-wait|last-ack|listening|closing}\n          connected := {established|syn-sent|syn-recv|fin-wait-{1,2}|time-wait|close-wait|last-ack|closing}\n       synchronized := {established|syn-recv|fin-wait-{1,2}|time-wait|close-wait|last-ack|closing}\n             bucket := {syn-recv|time-wait}\n                big := {established|syn-sent|fin-wait-{1,2}|closed|close-wait|last-ack|listening|closing}\n",
}

TPOT_NETSTAT_HELP = {
    '2.0': ('', "usage: netstat [-vWeenNcCF] [<Af>] -r         netstat {-V|--version|-h|--help}\n       netstat [-vWnNcaeol] [<Socket> ...]\n       netstat { [-vWeenNac] -I[<Iface>] | [-veenNac] -i | [-cnNe] -M | -s [-6tuw] } [delay]\n\n        -r, --route              display routing table\n        -I, --interfaces=<Iface> display interface table for <Iface>\n        -i, --interfaces         display interface table\n        -g, --groups             display multicast group memberships\n        -s, --statistics         display networking statistics (like SNMP)\n        -M, --masquerade         display masqueraded connections\n\n        -v, --verbose            be verbose\n        -W, --wide               don't truncate IP addresses\n        -n, --numeric            don't resolve names\n        --numeric-hosts          don't resolve host names\n        --numeric-ports          don't resolve port names\n        --numeric-users          don't resolve user names\n        -N, --symbolic           resolve hardware names\n        -e, --extend             display other/more information\n        -p, --programs           display PID/Program name for sockets\n        -o, --timers             display timers\n        -c, --continuous         continuous listing\n\n        -l, --listening          display listening server sockets\n        -a, --all                display all sockets (default: connected)\n        -F, --fib                display Forwarding Information Base (default)\n        -C, --cache              display routing cache instead of FIB\n        -Z, --context            display SELinux security context for sockets\n\n  <Socket>={-t|--tcp} {-u|--udp} {-U|--udplite} {-S|--sctp} {-w|--raw}\n           {-x|--unix} --ax25 --ipx --netrom\n  <AF>=Use '-6|-4' or '-A <af>' or '--<af>'; default: inet\n  List of possible address families (which support routing):\n    inet (DARPA Internet) inet6 (IPv6) ax25 (AMPR AX.25) \n    netrom (AMPR NET/ROM) ipx (Novell IPX) ddp (Appletalk DDP) \n    x25 (CCITT X.25) \n"),
    '1.60': ("usage: netstat [-vWeenNcCF] [<Af>] -r         netstat {-V|--version|-h|--help}\n       netstat [-vWnNcaeol] [<Socket> ...]\n       netstat { [-vWeenNac] -i | [-cnNe] -M | -s [-6tuw] }\n\n        -r, --route              display routing table\n        -i, --interfaces         display interface table\n        -g, --groups             display multicast group memberships\n        -s, --statistics         display networking statistics (like SNMP)\n        -M, --masquerade         display masqueraded connections\n\n        -v, --verbose            be verbose\n        -W, --wide               don't truncate IP addresses\n        -n, --numeric            don't resolve names\n        --numeric-hosts          don't resolve host names\n        --numeric-ports          don't resolve port names\n        --numeric-users          don't resolve user names\n        -N, --symbolic           resolve hardware names\n        -e, --extend             display other/more information\n        -p, --programs           display PID/Program name for sockets\n        -o, --timers             display timers\n        -c, --continuous         continuous listing\n\n        -l, --listening          display listening server sockets\n        -a, --all                display all sockets (default: connected)\n        -F, --fib                display Forwarding Information Base (default)\n        -C, --cache              display routing cache instead of FIB\n        -Z, --context            display SELinux security context for sockets\n\n  <Socket>={-t|--tcp} {-u|--udp} {-U|--udplite} {-S|--sctp} {-w|--raw}\n           {-x|--unix} --ax25 --ipx --netrom\n  <AF>=Use '-6|-4' or '-A <af>' or '--<af>'; default: inet\n  List of possible address families (which support routing):\n", '    inet (DARPA Internet) inet6 (IPv6) ax25 (AMPR AX.25) \n    netrom (AMPR NET/ROM) ipx (Novell IPX) ddp (Appletalk DDP) \n    x25 (CCITT X.25) \n'),
    '2.10': ("usage: netstat [-vWeenNcCF] [<Af>] -r         netstat {-V|--version|-h|--help}\n       netstat [-vWnNcaeol] [<Socket> ...]\n       netstat { [-vWeenNac] -i | [-cnNe] -M | -s [-6tuw] }\n\n        -r, --route              display routing table\n        -i, --interfaces         display interface table\n        -g, --groups             display multicast group memberships\n        -s, --statistics         display networking statistics (like SNMP)\n        -M, --masquerade         display masqueraded connections\n\n        -v, --verbose            be verbose\n        -W, --wide               don't truncate IP addresses\n        -n, --numeric            don't resolve names\n        --numeric-hosts          don't resolve host names\n        --numeric-ports          don't resolve port names\n        --numeric-users          don't resolve user names\n        -N, --symbolic           resolve hardware names\n        -e, --extend             display other/more information\n        -p, --programs           display PID/Program name for sockets\n        -o, --timers             display timers\n        -c, --continuous         continuous listing\n\n        -l, --listening          display listening server sockets\n        -a, --all                display all sockets (default: connected)\n        -F, --fib                display Forwarding Information Base (default)\n        -C, --cache              display routing cache instead of FIB\n        -Z, --context            display SELinux security context for sockets\n\n  <Socket>={-t|--tcp} {-u|--udp} {-U|--udplite} {-S|--sctp} {-w|--raw}\n           {-x|--unix} --ax25 --ipx --netrom\n  <AF>=Use '-6|-4' or '-A <af>' or '--<af>'; default: inet\n  List of possible address families (which support routing):\n", '    inet (DARPA Internet) inet6 (IPv6) ax25 (AMPR AX.25) \n    netrom (AMPR NET/ROM) rose (AMPR ROSE) ipx (Novell IPX) \n    ddp (Appletalk DDP) x25 (CCITT X.25) \n'),
}

TPOT_IFCONFIG_HELP = {
    '2.0': ('', 'Usage:\n  ifconfig [-a] [-v] [-s] <interface> [[<AF>] <address>]\n  [add <address>[/<prefixlen>]]\n  [del <address>[/<prefixlen>]]\n  [[-]broadcast [<address>]]  [[-]pointopoint [<address>]]\n  [netmask <address>]  [dstaddr <address>]  [tunnel <address>]\n  [outfill <NN>] [keepalive <NN>]\n  [hw <HW> <address>]  [mtu <NN>]\n  [[-]trailers]  [[-]arp]  [[-]allmulti]\n  [multicast]  [[-]promisc]\n  [mem_start <NN>]  [io_addr <NN>]  [irq <NN>]  [media <type>]\n  [txqueuelen <NN>]\n  [[-]dynamic]\n  [up|down] ...\n\n  <HW>=Hardware Type.\n  List of possible hardware types:\n    loop (Local Loopback) slip (Serial Line IP) cslip (VJ Serial Line IP) \n    slip6 (6-bit Serial Line IP) cslip6 (VJ 6-bit Serial Line IP) adaptive (Adaptive Serial Line IP) \n    ash (Ash) ether (Ethernet) ax25 (AMPR AX.25) \n    netrom (AMPR NET/ROM) rose (AMPR ROSE) tunnel (IPIP Tunnel) \n    ppp (Point-to-Point Protocol) hdlc ((Cisco)-HDLC) lapb (LAPB) \n    arcnet (ARCnet) dlci (Frame Relay DLCI) frad (Frame Relay Access Device) \n    sit (IPv6-in-IPv4) fddi (Fiber Distributed Data Interface) hippi (HIPPI) \n    irda (IrLAP) ec (Econet) x25 (generic X.25) \n    infiniband (InfiniBand) eui64 (Generic EUI-64) \n  <AF>=Address family. Default: inet\n  List of possible address families:\n    unix (UNIX Domain) inet (DARPA Internet) inet6 (IPv6) \n    ax25 (AMPR AX.25) netrom (AMPR NET/ROM) rose (AMPR ROSE) \n    ipx (Novell IPX) ddp (Appletalk DDP) ec (Econet) \n    ash (Ash) x25 (CCITT X.25) \n'),
    '1.60': ('Usage:\n  ifconfig [-a] [-v] [-s] <interface> [[<AF>] <address>]\n  [add <address>[/<prefixlen>]]\n  [del <address>[/<prefixlen>]]\n  [[-]broadcast [<address>]]  [[-]pointopoint [<address>]]\n  [netmask <address>]  [dstaddr <address>]  [tunnel <address>]\n  [outfill <NN>] [keepalive <NN>]\n  [hw <HW> <address>]  [mtu <NN>]\n  [[-]trailers]  [[-]arp]  [[-]allmulti]\n  [multicast]  [[-]promisc]\n  [mem_start <NN>]  [io_addr <NN>]  [irq <NN>]  [media <type>]\n  [txqueuelen <NN>]\n  [[-]dynamic]\n  [up|down] ...\n\n  <HW>=Hardware Type.\n  List of possible hardware types:\n  <AF>=Address family. Default: inet\n  List of possible address families:\n', '    loop (Local Loopback) slip (Serial Line IP) cslip (VJ Serial Line IP) \n    slip6 (6-bit Serial Line IP) cslip6 (VJ 6-bit Serial Line IP) adaptive (Adaptive Serial Line IP) \n    ash (Ash) ether (Ethernet) ax25 (AMPR AX.25) \n    netrom (AMPR NET/ROM) rose (AMPR ROSE) tunnel (IPIP Tunnel) \n    ppp (Point-to-Point Protocol) hdlc ((Cisco)-HDLC) lapb (LAPB) \n    arcnet (ARCnet) dlci (Frame Relay DLCI) frad (Frame Relay Access Device) \n    sit (IPv6-in-IPv4) fddi (Fiber Distributed Data Interface) hippi (HIPPI) \n    irda (IrLAP) ec (Econet) x25 (generic X.25) \n    eui64 (Generic EUI-64) \n    unix (UNIX Domain) inet (DARPA Internet) inet6 (IPv6) \n    ax25 (AMPR AX.25) netrom (AMPR NET/ROM) rose (AMPR ROSE) \n    ipx (Novell IPX) ddp (Appletalk DDP) ec (Econet) \n    ash (Ash) x25 (CCITT X.25) \n'),
    '2.10': ('Usage:\n  ifconfig [-a] [-v] [-s] <interface> [[<AF>] <address>]\n  [add <address>[/<prefixlen>]]\n  [del <address>[/<prefixlen>]]\n  [[-]broadcast [<address>]]  [[-]pointopoint [<address>]]\n  [netmask <address>]  [dstaddr <address>]  [tunnel <address>]\n  [outfill <NN>] [keepalive <NN>]\n  [hw <HW> <address>]  [mtu <NN>]\n  [[-]trailers]  [[-]arp]  [[-]allmulti]\n  [multicast]  [[-]promisc]\n  [mem_start <NN>]  [io_addr <NN>]  [irq <NN>]  [media <type>]\n  [txqueuelen <NN>]\n  [name <newname>]\n  [[-]dynamic]\n  [up|down] ...\n\n  <HW>=Hardware Type.\n  List of possible hardware types:\n  <AF>=Address family. Default: inet\n  List of possible address families:\n', '    loop (Local Loopback) slip (Serial Line IP) cslip (VJ Serial Line IP) \n    slip6 (6-bit Serial Line IP) cslip6 (VJ 6-bit Serial Line IP) adaptive (Adaptive Serial Line IP) \n    ash (Ash) ether (Ethernet) ax25 (AMPR AX.25) \n    netrom (AMPR NET/ROM) rose (AMPR ROSE) tunnel (IPIP Tunnel) \n    ppp (Point-to-Point Protocol) hdlc ((Cisco)-HDLC) lapb (LAPB) \n    arcnet (ARCnet) dlci (Frame Relay DLCI) frad (Frame Relay Access Device) \n    sit (IPv6-in-IPv4) fddi (Fiber Distributed Data Interface) hippi (HIPPI) \n    irda (IrLAP) ec (Econet) x25 (generic X.25) \n    eui64 (Generic EUI-64) \n    unix (UNIX Domain) inet (DARPA Internet) inet6 (IPv6) \n    ax25 (AMPR AX.25) netrom (AMPR NET/ROM) rose (AMPR ROSE) \n    ipx (Novell IPX) ddp (Appletalk DDP) ec (Econet) \n    ash (Ash) x25 (CCITT X.25) \n'),
}

# (the name list without the one the earlier rounds keep out of netstat's texts)
TPOT_NETSTAT_VERSION = {
    '2.10': 'net-tools 2.10\nFred Baumgarten, Alan Cox, Bernd Eckenfels, Tuan Hoang, Brian Micek and others\n+NEW_ADDRT +RTF_IRTT +RTF_REJECT +FW_MASQUERADE +I18N +SELINUX\nAF: (inet) +UNIX +INET +INET6 +IPX +AX25 +NETROM +X25 +ATALK +ECONET +ROSE -BLUETOOTH\nHW:  +ETHER +ARC +SLIP +PPP +TUNNEL -TR +AX25 +NETROM +X25 +FR +ROSE +ASH +SIT +FDDI +HIPPI +HDLC/LAPB +EUI64 \n',
    '2.10-alpha': 'net-tools 2.10-alpha\nFred Baumgarten, Alan Cox, Bernd Eckenfels, Tuan Hoang, Brian Micek and others\n+NEW_ADDRT +RTF_IRTT +RTF_REJECT +FW_MASQUERADE +I18N +SELINUX\nAF: (inet) +UNIX +INET +INET6 +IPX +AX25 +NETROM +X25 +ATALK +ECONET +ROSE -BLUETOOTH\nHW:  +ETHER +ARC +SLIP +PPP +TUNNEL -TR +AX25 +NETROM +X25 +FR +ROSE +ASH +SIT +FDDI +HIPPI +HDLC/LAPB +EUI64 \n',
}
TPOT_NET_XFRM = "Usage: ip xfrm XFRM-OBJECT { COMMAND | help }\nwhere  XFRM-OBJECT := state | policy | monitor\n"
TPOT_NET_DEFAULT = {
    "tools": {"ip": {"style": "iproute2", "version": "6.1.0", "libbpf": ""},
              "ss": {"style": "iproute2", "version": "6.1.0", "libbpf": ""},
              "ifconfig": {"style": "net-tools", "version": "2.10", "si": False, "narrow": False, "basename": False},
              "netstat": {"style": "net-tools", "version": "2.10", "si": False, "narrow": False, "basename": False}},
    "interfaces": [{"name": "eth0", "mac": "52:54:00:4a:1c:7e", "inet": "10.0.2.15", "prefix": 24,
                    "broadcast": "10.0.2.255", "network": "10.0.2.0", "netmask": "255.255.255.0",
                    "inet6": "fe80::5054:ff:fe4a:1c7e", "mtu": 1500, "qdisc": "fq_codel", "state": "UP",
                    "dynamic": False, "rates": [1200, 400]}],
    "gateway": "10.0.2.2", "gateway_mac": "52:55:0a:00:02:02", "manager": "static", "kernel": [6, 1],
    "booted_at": 0, "sockets": [], "family": "debian", "resolved": False, "gateway_name": ""}


@functools.cache
def tpot_net_data(path: str) -> dict:
    """The network of [shell] network; without one a host with one interface."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8")) if path else None
    except (OSError, ValueError):
        data = None
    return data if isinstance(data, dict) and isinstance(data.get("interfaces"), list) else TPOT_NET_DEFAULT


def tpot_net_version(text: str) -> tuple:
    """(major, minor) of an iproute2 version: 6.1.0 -> (6, 1), ss170501 -> (4, 11), ss110629 -> (3, 0)."""
    snapshots = {"ss110629": (3, 0), "ss130716": (3, 10), "ss170501": (4, 11), "ss171113": (4, 14)}
    if text in snapshots:
        return snapshots[text]
    found = re.findall(r"\d+", text)
    return tuple(int(x) for x in found[:2]) if found else (6, 1)


class TpotNetTool(HoneyPotCommand):
    """What ip, ss, netstat and ifconfig share: the persona's network, its interfaces with their
    traffic since boot, the sockets and the session's connection."""

    tool = ""

    def start(self) -> None:
        self.net = tpot_net_data(CowrieConfig.get("shell", "network", fallback=""))
        self.style = self.net["tools"].get(self.tool) or {"style": "iproute2", "version": "6.1.0"}
        self.tpot_out, self.tpot_err = [], []
        try:
            code = self.tpot_main(list(self.args))
        except Exception:  # noqa: BLE001 - the command ends, the session goes on; the error goes to Cowrie's log
            log.err(None, f"{self.tool} of the persona ended on an internal error")
            code = 1
        if self.tpot_out:
            self.write("".join(self.tpot_out))
        if self.tpot_err:
            self.errorWrite("".join(self.tpot_err))
        self.exit_code = code
        self.exit()

    def tpot_main(self, args: list[str]) -> int:
        raise NotImplementedError

    def put(self, text: str) -> None:
        self.tpot_out.append(text)

    def warn(self, text: str) -> None:
        self.tpot_err.append(text)

    # --- the persona's network -------------------------------------------------------------

    def interfaces(self) -> list[dict]:
        """lo and the persona's interfaces, with their index and their traffic since boot."""
        up = max(60.0, time.time() - float(self.net.get("booted_at") or 0)) if self.net.get("booted_at") else 3600.0
        found = [{"name": "lo", "loopback": True, "mac": "00:00:00:00:00:00", "inet": "127.0.0.1", "prefix": 8,
                  "inet6": "::1", "mtu": 16436 if self.net["kernel"] < [3, 6] else 65536, "qdisc": "noqueue",
                  "state": "UNKNOWN", "network": "127.0.0.0", "netmask": "255.0.0.0", "dynamic": False}]
        found += [dict(iface) for iface in self.net["interfaces"]]
        total = [0, 0]
        for index, iface in enumerate(found, 1):
            iface["index"] = index
            rates = iface.get("rates")
            if rates:
                rx = int(rates[0] * up)
                rxp = rx // max(64, int(rates[1]))
                iface["rx"], iface["tx"] = (rx, rxp), (int(rx * 0.37), int(rxp * 0.71))
                total = [total[0] + rx // 50, total[1] + rxp // 60]
            else:
                iface["rx"] = iface["tx"] = (0, 0)
        found[0]["rx"] = found[0]["tx"] = (total[0], total[1])
        return found

    def find_iface(self, name: str):
        return next((i for i in self.interfaces() if i["name"] == name), None)

    def session(self):
        """(local port, peer address, peer port) of the session's connection."""
        local, peer, port = 22, "", 0
        try:
            peer = str(getattr(self.protocol, "clientIP", "") or getattr(self.protocol, "realClientIP", ""))
            port = int(getattr(self.protocol, "realClientPort", 0) or 0)
            transport = self.protocol.getProtoTransport()
            local = int(transport.transport.getHost().port)
        except Exception:  # noqa: BLE001 - a protocol without a transport (tests, exec): the SSH port
            pass
        if local not in (22, 23):
            local = 23 if "telnet" in type(self.protocol).__name__.lower() else 22
        return local, peer, port

    def sockets(self) -> list[dict]:
        """The daemons' sockets and the session's connection (tcp, established)."""
        found = [dict(s, state="LISTEN" if s["proto"] == "tcp" else "UNCONN") for s in self.net.get("sockets", [])]
        local, peer, port = self.session()
        if peer and port:
            try:
                family = ipaddress.ip_address(peer).version
            except ValueError:
                family = 4
            listener = next((s for s in found if s["proto"] == "tcp" and s["port"] == local), None)
            address = self.net["interfaces"][0]["inet"]
            if family == 6:
                address = "::ffff:" + address
                peer = peer if ":" in peer else "::ffff:" + peer
            found.append({"proto": "tcp", "family": family, "address": address, "port": local, "peer": peer,
                          "peer_port": port, "state": "ESTAB", "pid": listener["pid"] if listener else 0,
                          "comm": listener["comm"] if listener else "", "cmdline": listener["cmdline"] if listener
                          else "", "fd": 4, "backlog": 36})
        return found

    def routes(self) -> list[dict]:
        main = self.net["interfaces"][0]
        found = [{"dst": "0.0.0.0", "mask": "0.0.0.0", "gateway": self.net["gateway"], "iface": main["name"],
                  "flags": "UG"},
                 {"dst": main["network"], "mask": main["netmask"], "gateway": "0.0.0.0", "iface": main["name"],
                  "flags": "U"}]
        if self.net["manager"] == "networkd":
            found.append({"dst": self.net["gateway"], "mask": "255.255.255.255", "gateway": "0.0.0.0",
                          "iface": main["name"], "flags": "UH"})
        for iface in self.net["interfaces"][1:]:
            if iface.get("inet"):
                found.append({"dst": iface["network"], "mask": iface["netmask"], "gateway": "0.0.0.0",
                              "iface": iface["name"], "flags": "U"})
        if len(self.net["interfaces"]) > 1 and self.net["interfaces"][1].get("inet") and \
                self.net["gateway"].rsplit(".", 1)[0] == self.net["interfaces"][1]["inet"].rsplit(".", 1)[0]:
            # a router: the default route is the one of its WAN
            found[0]["iface"] = self.net["interfaces"][1]["name"]
            found[1], found[-1] = found[-1], found[1]
            found[1:] = sorted(found[1:], key=lambda r: r["iface"] != self.net["interfaces"][1]["name"])
        return found

    def lease(self, iface: dict) -> int:
        lease = int(iface.get("lease") or 86400)
        up = int(time.time() - float(self.net.get("booted_at") or time.time() - 3600))
        return lease - (up % max(1, lease // 2))


# --- ip ---------------------------------------------------------------------------------------

class Command_tpot_ip(TpotNetTool):
    """ip of iproute2 (its version) or of BusyBox."""

    tool = "ip"

    def tpot_main(self, args: list[str]) -> int:
        self.busybox = self.style["style"] == "busybox"
        self.version = tpot_net_version(self.style.get("version", "6.1.0"))
        self.family, self.brief, self.oneline, self.stats = 0, False, False, False
        while args and args[0].startswith("-") and args[0] != "-":
            opt = args.pop(0)
            word = opt.lstrip("-")
            if word in ("4",):
                self.family = 4
            elif word in ("6",):
                self.family = 6
            elif word in ("0",):
                self.family = -1
            elif not self.busybox and word and "brief".startswith(word) and len(word) >= 2:
                self.brief = True
            elif word and "oneline".startswith(word):
                self.oneline = True
            elif not self.busybox and word and "statistics".startswith(word):
                self.stats = True
            elif word and "family".startswith(word) and args:
                fam = args.pop(0)
                self.family = {"inet": 4, "inet6": 6, "link": -1}.get(fam, 0)
            elif not self.busybox and word and "Version".startswith(word) and word[0] == "V":
                libbpf = self.style.get("libbpf")
                self.put(f"ip utility, iproute2-{self.style.get('version')}" + (f", libbpf {libbpf}" if libbpf else "")
                         + "\n")
                return 0
            elif not self.busybox and word and ("details".startswith(word) or "resolve".startswith(word)
                                               or "human-readable".startswith(word) or "color".startswith(word)
                                               or "Numeric".startswith(word) or word in ("iec", "all")):
                continue
            else:
                if self.busybox:
                    return self.bb_usage()
                self.warn(f'Option "{opt}" is unknown, try "ip -help".\n')
                return 255
        if not args:
            return self.bb_usage() if self.busybox else self.usage()
        word = args.pop(0)
        if self.busybox:
            objects = {"address": self.address, "route": self.route, "link": self.link, "neigh": self.neigh,
                       "rule": self.rule, "tunnel": lambda a: 0}
            found = next((f for name, f in objects.items() if name.startswith(word)), None)
            return found(args) if found else self.bb_usage()
        name = next((n for n in TPOT_NET_OBJECTS if n.startswith(word)), None)
        if name is None:
            self.warn(f'Object "{word}" is unknown, try "ip help".\n')
            return 1
        handler = {"address": self.address, "route": self.route, "link": self.link, "neighbor": self.neigh,
                   "neighbour": self.neigh, "rule": self.rule, "help": lambda a: self.usage(),
                   "xfrm": lambda a: self.xfrm(a)}.get(name)
        return handler(args) if handler else 0

    def usage(self) -> int:
        v = self.version
        key = "4.11" if v < (5, 0) else "5.15" if v < (6, 0) else "6.1" if v < (6, 10) else "6.15"
        self.warn(TPOT_IP_USAGE[key])
        return 255

    def bb_usage(self) -> int:
        banner = CowrieConfig.get("shell", "text_banner", fallback="") or self.style.get("version", "")
        numbers = tuple(int(x) for x in re.findall(r"\d+", banner)[:2]) or (1, 33)
        if numbers < (1, 28):
            self.warn(f"{banner} multi-call binary.\n\nUsage: ip [OPTIONS] address|route|link|tunnel|neigh|rule [COMMAND]\n\n"
                      "OPTIONS := -f[amily] inet|inet6|link | -o[neline]\nCOMMAND :=\n"
                      "ip addr add|del IFADDR dev IFACE | show|flush [dev IFACE] [to PREFIX]\n"
                      "ip route list|flush|add|del|change|append|replace|test ROUTE\n"
                      "ip link set IFACE [up|down] [arp on|off] | show [IFACE]\n"
                      "ip tunnel add|change|del|show [NAME]\n\t[mode ipip|gre|sit]\n\t[remote ADDR] [local ADDR] [ttl TTL]\n"
                      "ip neigh show|flush [to PREFIX] [dev DEV] [nud STATE]\nip rule [list] | add|del SELECTOR ACTION\n")
            return 1
        if numbers >= (1, 36):
            link = ("ip link set IFACE [up|down] [arp on|off] [multicast on|off]\n\t[promisc on|off] [mtu NUM] "
                    "[name NAME] [qlen NUM] [address MAC]\n\t[master IFACE | nomaster] [netns PID]\n")
        else:
            link = ("ip link set IFACE [up|down] [arp on|off] [multicast on|off]\n\t[promisc on|off] [mtu NUM] "
                    "[name NAME] [qlen NUM] [address MAC]\n\t[master IFACE | nomaster]\n")
        self.warn(f"{banner} multi-call binary.\n\nUsage: ip [OPTIONS] address|route|link|tunnel|neigh|rule [ARGS]\n\n"
                 "OPTIONS := -f[amily] inet|inet6|link | -o[neline]\n\n"
                 "ip addr add|del IFADDR dev IFACE | show|flush [dev IFACE] [to PREFIX]\n"
                 "ip route list|flush|add|del|change|append|replace|test ROUTE\n" + link
                 + "ip tunnel add|change|del|show [NAME]\n\t[mode ipip|gre|sit] [remote ADDR] [local ADDR] [ttl TTL]\n"
                 "ip neigh show|flush [to PREFIX] [dev DEV] [nud STATE]\nip rule [list] | add|del SELECTOR ACTION\n")
        return 1

    def xfrm(self, args: list[str]) -> int:
        if not args:
            self.warn(TPOT_NET_XFRM)
            return 255
        return 0

    def command_and_device(self, args: list[str], object_name: str):
        """(command, device or None, error status or None) of `show [dev] IFACE`."""
        command = "show"
        if args and not args[0].startswith("dev"):
            word = args[0]
            if word in ("show", "list", "lst", "sh", "ls") or "show".startswith(word):
                args = args[1:]
            elif word in ("add", "del", "delete", "change", "replace", "flush", "set", "append", "test", "get",
                          "save", "restore"):
                return word, None, None
            elif not self.busybox and self.find_iface(word) is None:
                self.warn(f'Command "{word}" is unknown, try "ip {object_name} help".\n')
                return command, None, 255
        device = None
        if args:
            device = args[1] if args[0] == "dev" and len(args) > 1 else args[0]
            if self.find_iface(device) is None:
                if self.busybox:
                    self.warn(f"ip: can't find device '{device}'\n")
                else:
                    self.warn(f'Device "{device}" does not exist.\n')
                return command, None, 1
        return command, device, None

    def privileged(self) -> bool:
        try:
            return int(self.protocol.user.uid) == 0
        except Exception:  # noqa: BLE001
            return True

    def change(self, command: str) -> int:
        if command in ("save", "test"):
            return 0
        if not self.privileged():
            self.warn("RTNETLINK answers: Operation not permitted\n")
            return 2
        return 0

    def flags(self, iface: dict) -> str:
        if iface.get("loopback"):
            return "<LOOPBACK,UP,LOWER_UP>"
        if iface.get("nocarrier"):
            return "<NO-CARRIER,BROADCAST,MULTICAST,UP>"
        if iface.get("state") == "DOWN":
            return "<BROADCAST,MULTICAST>"
        return "<BROADCAST,MULTICAST,UP,LOWER_UP>"

    def header(self, iface: dict, link: bool = False) -> str:
        """The first line of an interface (ip link / ip address)."""
        qlen = 1000
        if self.busybox:
            # the BusyBox of a uClibc build prints no state (its headers have no IFF_DORMANT)
            libc = CowrieConfig.get("shell", "text_libc", fallback="glibc")
            state = "" if libc == "uclibc" else f"state {iface['state']} "
            return (f"{iface['index']}: {iface['name']}: {self.flags(iface)} mtu {iface['mtu']} qdisc {iface['qdisc']} "
                    f"{state}qlen {qlen}")
        if self.version < (3, 2):
            return (f"{iface['index']}: {iface['name']}: {self.flags(iface)} mtu {iface['mtu']} qdisc {iface['qdisc']} "
                    f"state {iface['state']} " + ("" if iface.get("loopback") else f"qlen {qlen}"))
        if self.family == 6 and not link:
            return f"{iface['index']}: {iface['name']}: {self.flags(iface)} mtu {iface['mtu']} state {iface['state']} qlen {qlen}"
        return (f"{iface['index']}: {iface['name']}: {self.flags(iface)} mtu {iface['mtu']} qdisc {iface['qdisc']} "
                f"state {iface['state']} " + ("mode DEFAULT " if link else "") + f"group default qlen {qlen}")

    def link_lines(self, iface: dict) -> list[str]:
        if iface.get("loopback"):
            lines = ["    link/loopback 00:00:00:00:00:00 brd 00:00:00:00:00:00"]
        else:
            lines = [f"    link/ether {iface['mac']} brd ff:ff:ff:ff:ff:ff"]
        if iface.get("altname") and not self.busybox and self.version >= (5, 4):
            lines.append(f"    altname {iface['altname']}")
        return lines

    def address_lines(self, iface: dict) -> list[tuple[int, str, str]]:
        """(family, the address line, the lifetime line) of an interface."""
        kernel = tuple(self.net.get("kernel", [6, 1]))
        proto = not self.busybox and self.version >= (6, 3) and kernel >= (6, 3)
        lines = []
        if iface.get("inet"):
            if iface.get("loopback"):
                text = "    inet 127.0.0.1/8 scope host lo"
                lft = "       valid_lft forever preferred_lft forever"
            else:
                metric = " metric 100" if (self.net["manager"] == "networkd" and iface.get("dynamic")
                                           and not self.busybox) else ""
                flags = ("dynamic " if iface.get("dynamic") else "") + ("noprefixroute " if iface.get("noprefixroute")
                                                                         else "")
                text = (f"    inet {iface['inet']}/{iface['prefix']}{metric} brd {iface['broadcast']} scope global "
                        f"{flags}{iface['name']}")
                if iface.get("dynamic"):
                    left = self.lease(iface)
                    lft = f"       valid_lft {left}sec preferred_lft {left}sec"
                else:
                    lft = "       valid_lft forever preferred_lft forever"
            lines.append((4, text, lft))
        if iface.get("inet6"):
            if iface.get("loopback"):
                text = "    inet6 ::1/128 scope host " + ("proto kernel_lo " if proto else "")
            else:
                text = (f"    inet6 {iface['inet6']}/64 scope link " + ("noprefixroute " if iface.get("noprefixroute")
                                                                         else "")
                        + ("proto kernel_ll " if proto else ""))
            lines.append((6, text, "       valid_lft forever preferred_lft forever"))
        return lines

    def address(self, args: list[str]) -> int:
        command, device, error = self.command_and_device(args, "address")
        if error is not None:
            return error
        if command != "show":
            return self.change(command)
        for iface in self.interfaces():
            if device and iface["name"] != device:
                continue
            addresses = [a for a in self.address_lines(iface) if not self.family or self.family == a[0]]
            if self.brief:
                state = iface["state"]
                shown = "".join(f"{iface['inet']}/{iface['prefix']} " if fam == 4 else
                                ("::1/128 " if iface.get("loopback") else f"{iface['inet6']}/64 ")
                                for fam, _, _ in addresses)
                self.put(f"{iface['name']:<16} {state:<14} {shown}\n")
                continue
            if self.oneline:
                for fam, text, lft in addresses:
                    self.put(f"{iface['index']}: {iface['name']}    {text.strip()}\\{lft}\n")
                continue
            if self.family and not addresses:
                continue
            self.put(self.header(iface) + "\n")
            if not self.family or self.family == -1:
                self.put("\n".join(self.link_lines(iface)) + "\n")
            for _, text, lft in addresses:
                self.put(f"{text}\n{lft}\n")
        return 0

    def link(self, args: list[str]) -> int:
        command, device, error = self.command_and_device(args, "link")
        if error is not None:
            return error
        if command != "show":
            return self.change(command)
        for iface in self.interfaces():
            if device and iface["name"] != device:
                continue
            if self.brief:
                self.put(f"{iface['name']:<16} {iface['state']:<14} {iface['mac']} {self.flags(iface)} \n")
                continue
            self.put(self.header(iface, link=True) + "\n" + "\n".join(self.link_lines(iface)) + "\n")
            if self.stats:
                self.put(self.stats_lines(iface))
        return 0

    def stats_lines(self, iface: dict) -> str:
        rx, tx = iface["rx"], iface["tx"]
        if self.version < (5, 0):
            return (f"    RX: bytes  packets  errors  dropped overrun mcast   \n    {rx[0]:<10} {rx[1]:<8} {0:<7} {0:<7} "
                    f"{0:<7} {0:<7} \n    TX: bytes  packets  errors  dropped carrier collsns \n    {tx[0]:<10} {tx[1]:<8} "
                    f"{0:<7} {0:<7} {0:<7} {0:<7} \n")
        return ("    RX:  bytes packets errors dropped  missed   mcast           \n"
                f"    {rx[0]:>10} {rx[1]:>7} {0:>6} {0:>7} {0:>7} {0:>7} \n"
                "    TX:  bytes packets errors dropped carrier collsns           \n"
                f"    {tx[0]:>10} {tx[1]:>7} {0:>6} {0:>7} {0:>7} {0:>7} \n")

    def route(self, args: list[str]) -> int:
        if args and args[0] not in ("show", "list", "ls", "lst") and not "show".startswith(args[0]):
            if args[0] == "get" and len(args) > 1:
                main = self.net["interfaces"][0]
                self.put(f"{args[1]} via {self.net['gateway']} dev {main['name']} src {main['inet']} uid 0 \n"
                         "    cache \n")
                return 0
            if self.busybox:
                return self.change(args[0])
            if args[0] in ("add", "del", "delete", "change", "replace", "append", "flush"):
                return self.change(args[0])
            self.warn(f'Command "{args[0]}" is unknown, try "ip route help".\n')
            return 255
        if self.family == 6:
            if not self.busybox:
                tail = " pref medium " if self.version >= (4, 0) else " "
                self.put(f"::1 dev lo proto kernel metric 256{tail}\n")
                for iface in self.net["interfaces"]:
                    if iface.get("inet6") and iface.get("state") == "UP":
                        self.put(f"fe80::/64 dev {iface['name']} proto kernel metric 256{tail}\n")
            return 0
        manager = self.net["manager"]
        main = self.net["interfaces"][0]
        for entry in self.routes():
            dst, iface = entry["dst"], entry["iface"]
            src = next((i["inet"] for i in self.net["interfaces"] if i["name"] == iface), main["inet"])
            prefix = ipaddress.IPv4Network(f"{dst}/{entry['mask']}").prefixlen
            if self.busybox or manager == "device":
                if entry["flags"] == "UG":
                    line = f"default via {entry['gateway']} dev {iface} "
                elif self.version < (3, 2) and not self.busybox:
                    line = f"{dst}/{prefix} dev {iface}  proto kernel  scope link  src {src} "
                elif self.busybox:
                    line = f"{dst}/{prefix} dev {iface} scope link  src {src} "
                else:
                    line = f"{dst}/{prefix} dev {iface} proto kernel scope link src {src} "
            elif manager == "networkd":
                if entry["flags"] == "UG":
                    line = f"default via {entry['gateway']} dev {iface} proto dhcp src {src} metric 100 "
                elif entry["flags"] == "UH":
                    line = f"{dst} dev {iface} proto dhcp scope link src {src} metric 100 "
                else:
                    line = f"{dst}/{prefix} dev {iface} proto kernel scope link src {src} metric 100 "
            elif manager in ("nm", "nm-old"):
                if entry["flags"] == "UG":
                    line = f"default via {entry['gateway']} dev {iface} proto dhcp " + \
                        ("" if manager == "nm-old" else f"src {src} ") + "metric 100 "
                else:
                    line = f"{dst}/{prefix} dev {iface} proto kernel scope link src {src} metric 100 "
            else:
                if entry["flags"] == "UG":
                    line = f"default via {entry['gateway']} dev {iface} proto static "
                else:
                    line = f"{dst}/{prefix} dev {iface} proto kernel scope link src {src} "
            self.put(line + "\n")
        return 0

    def neigh(self, args: list[str]) -> int:
        if args and args[0] not in ("show", "list", "ls") and not "show".startswith(args[0]):
            return self.change(args[0])
        iface = next((r["iface"] for r in self.routes() if r["flags"] == "UG"), self.net["interfaces"][0]["name"])
        tail = " " if (not self.busybox and self.version >= (6, 0)) else ""
        if self.family != 6:
            self.put(f"{self.net['gateway']} dev {iface} lladdr {self.net['gateway_mac']} REACHABLE{tail}\n")
        return 0

    def rule(self, args: list[str]) -> int:
        if args and args[0] not in ("show", "list", "ls"):
            return self.change(args[0])
        tail = " " if (not self.busybox and self.version < (5, 0)) else ""
        self.put(f"0:\tfrom all lookup local{tail}\n32766:\tfrom all lookup main{tail}\n"
                 f"32767:\tfrom all lookup default{tail}\n")
        return 0


# --- ss ---------------------------------------------------------------------------------------

class Command_tpot_ss(TpotNetTool):
    """ss of iproute2: the columns of 4.15 and later (widths of their content), the fixed widths
    of older releases."""

    tool = "ss"

    def tpot_main(self, args: list[str]) -> int:
        self.version = tpot_net_version(self.style.get("version", "6.1.0"))
        kinds, listen, every, numeric, processes, family, header, summary = set(), False, False, False, False, 0, True, False
        for arg in args:
            if arg.startswith("--"):
                word = arg[2:]
                table = {"tcp": "t", "udp": "u", "unix": "x", "raw": "w", "listening": "l", "all": "a",
                         "numeric": "n", "processes": "p", "ipv4": "4", "ipv6": "6", "no-header": "H",
                         "summary": "s", "version": "V", "help": "h", "extended": "e", "options": "o",
                         "memory": "m", "info": "i", "resolve": "r"}
                letter = table.get(word) or next((v for k, v in table.items() if k.startswith(word)), None)
                if letter is None:
                    self.warn(f"ss: unrecognized option '{arg}'\n" + self.help_text())
                    return 1
                letters = letter
            elif arg.startswith("-") and len(arg) > 1:
                letters = arg[1:]
            else:
                break  # a filter: shown as without it
            for letter in letters:
                if letter == "t":
                    kinds.add("tcp")
                elif letter == "u":
                    kinds.add("udp")
                elif letter == "x":
                    kinds.add("unix")
                elif letter == "w":
                    kinds.add("raw")
                elif letter == "l":
                    listen = True
                elif letter == "a":
                    every = True
                elif letter == "n":
                    numeric = True
                elif letter == "p":
                    processes = True
                elif letter == "4":
                    family = 4
                elif letter == "6":
                    family = 6
                elif letter == "H":
                    header = False
                elif letter == "s":
                    summary = True
                elif letter == "V":
                    self.put(f"ss utility, iproute2-{self.style.get('version')}\n")
                    return 0
                elif letter == "h":
                    self.put(self.help_text())
                    return 0
                elif letter == "Z" or letter == "z":
                    self.warn("ss: SELinux is not enabled.\n")
                    return 1
                elif letter in "eomirEKSdAfNbDFTMy0":
                    continue
                else:
                    self.warn(f"ss: invalid option -- '{letter}'\n" + self.help_text())
                    return 1
        sockets = self.sockets()
        if summary:
            return self.summary(sockets)
        many_kinds = len(kinds) != 1
        shown = []
        for s in sockets:
            if kinds and s["proto"] not in kinds:
                continue
            if family and s["family"] != family:
                continue
            listening = s["state"] in ("LISTEN", "UNCONN")
            if listen and not listening:
                continue
            if not listen and not every and listening:
                continue
            shown.append(s)
        shown.sort(key=lambda s: (s["proto"] != "udp", s["state"] != "LISTEN" and s["state"] != "UNCONN"))
        rows = [self.row(s, numeric, processes, many_kinds) for s in shown]
        if self.version < (4, 15):
            self.render_old(rows, header, many_kinds, len({"LISTEN"} if listen and not every else {1, 2}) > 1,
                            processes)
        else:
            self.render(rows, header, many_kinds, processes)
        return 0

    def help_text(self) -> str:
        v = self.version
        return TPOT_SS_HELP["4.11" if v < (5, 0) else "5.15" if v < (6, 0) else "6.1" if v < (6, 10) else "6.15"]

    def endpoint(self, address: str, port, numeric: bool, old: bool):
        host, _, ifname = address.partition("%")
        if ":" in host:
            shown = f"[{host}]" if (host != "::" or True) else "*"
        elif old and host == "0.0.0.0":
            shown = "*"
        else:
            shown = host
        if ifname:
            shown += "%" + ifname
        if port == "*":
            service = "*"
        else:
            service = str(port) if numeric else TPOT_NET_SERVICES.get(int(port), str(port))
        return shown, service

    def row(self, s: dict, numeric: bool, processes: bool, many: bool):
        old = self.version < (4, 15)
        local = self.endpoint(s["address"], s["port"], numeric, old)
        if s.get("peer"):
            peer = self.endpoint(s["peer"], s["peer_port"], True, old)
        else:
            peer = ("[::]" if s["family"] == 6 else ("*" if old else "0.0.0.0")), "*"
        state = {"ESTAB": "ESTAB", "LISTEN": "LISTEN", "UNCONN": "UNCONN"}[s["state"]]
        recv = 0
        send = s.get("backlog", 0) if s["state"] == "LISTEN" else (36 if s["state"] == "ESTAB" else 0)
        process = f' users:(("{s["comm"][:15]}",pid={s["pid"]},fd={s["fd"]}))' if processes and s.get("pid") else ""
        return {"netid": s["proto"], "state": state, "recv": str(recv), "send": str(send), "local": local,
                "peer": peer, "process": process}

    def render(self, rows: list[dict], header: bool, netid: bool, processes: bool) -> None:
        """The column renderer of ss (no terminal: every column as wide as its widest field)."""
        columns = [("netid", "Netid", "", "left")] if netid else []
        columns += [("state", "State", " ", "left"), ("recv", "Recv-Q", " ", "left"), ("send", "Send-Q", " ", "left"),
                    ("laddr", "Local Address:", " ", "right"), ("lport", "Port", "", "left"),
                    ("paddr", "Peer Address:", " ", "right"), ("pport", "Port", "", "left")]
        with_process = processes or self.version < (6, 10)
        if with_process:
            columns.append(("process", "Process", "", "left"))
        table = []
        for row in rows:
            fields = dict(row)
            fields["laddr"], fields["lport"] = row["local"][0] + ":", row["local"][1]
            fields["paddr"], fields["pport"] = row["peer"][0] + ":", row["peer"][1]
            table.append(fields)
        lines = ([{key: title for key, title, _, _ in columns}] if header else []) + table
        widths = {}
        for at, (key, title, delim, _) in enumerate(columns):
            longest = max([len(line.get(key, "")) for line in lines] + [len(title) if header else 0])
            widths[key] = longest + (len(delim) if at and longest else 0)
        for line in lines:
            out = []
            for at, (key, title, delim, align) in enumerate(columns):
                value = line.get(key, "")
                printed = delim if at else ""
                if align == "right":
                    out.append(printed + " " * max(0, widths[key] - len(printed) - len(value)) + value)
                else:
                    out.append(printed + value + " " * max(0, widths[key] - len(printed) - len(value)))
            self.put("".join(out) + "\n")

    def render_old(self, rows: list[dict], header: bool, netid: bool, states: bool, processes: bool) -> None:
        """The fixed widths of ss before 4.15 (screen width 80)."""
        netid_width = 5 if netid else 0
        state_width = 10
        width = 80
        if self.version < (4, 14):
            width -= netid_width + 1
            width -= state_width + 1
        else:
            if netid_width:
                width -= netid_width + 1
            width -= state_width + 1
        width -= 14
        if width & 1:
            if netid_width:
                netid_width += 1
            else:
                state_width += 1
        width = width // 2 - 1
        serv_width = 5
        if width < 15 + serv_width + 1:
            width = 15 + serv_width + 1
        addr_width = width - serv_width - 1 - 13
        serv_width += 13
        if header:
            text = (f"{'Netid':<{netid_width}} " if netid_width else "") + f"{'State':<{state_width}} "
            text += f"{'Recv-Q':<6} {'Send-Q':<6} "
            text += f"{'Local Address':>{addr_width}}:{'Port':<{serv_width}} {'Peer Address':>{addr_width}}:{'Port':<{serv_width}}\n"
            self.put(text)
        for row in rows:
            text = (f"{row['netid']:<{netid_width}} " if netid_width else "") + f"{row['state']:<{state_width}} "
            text += f"{row['recv']:<6} {row['send']:<6} "
            for host, service in (row["local"], row["peer"]):
                est = addr_width
                if ":" in host and len(host) > addr_width:
                    est = addr_width + ((len(host) - addr_width + 3) // 4) * 4
                text += f"{host:>{est}}:{service:<{serv_width}} "
            self.put(text + row["process"] + "\n")

    def summary(self, sockets: list[dict]) -> int:
        tcp = [s for s in sockets if s["proto"] == "tcp"]
        udp = [s for s in sockets if s["proto"] == "udp"]
        estab = sum(1 for s in tcp if s["state"] == "ESTAB")
        tcp4, tcp6 = sum(1 for s in tcp if s["family"] == 4), sum(1 for s in tcp if s["family"] == 6)
        udp4, udp6 = sum(1 for s in udp if s["family"] == 4), sum(1 for s in udp if s["family"] == 6)
        total = 120 + len(sockets)
        self.put(f"Total: {total}\nTCP:   {len(tcp)} (estab {estab}, closed 0, orphaned 0, timewait 0)\n\n"
                 "Transport Total     IP        IPv6\n"
                 f"RAW\t  {0:<9} {0:<9} {0:<9}\nUDP\t  {len(udp):<9} {udp4:<9} {udp6:<9}\n"
                 f"TCP\t  {len(tcp):<9} {tcp4:<9} {tcp6:<9}\nINET\t  {len(tcp) + len(udp):<9} {tcp4 + udp4:<9} "
                 f"{tcp6 + udp6:<9}\nFRAG\t  {0:<9} {0:<9} {0:<9}\n\n")
        return 0


# --- netstat ----------------------------------------------------------------------------------

class Command_tpot_netstat(TpotNetTool):
    """netstat of net-tools or of BusyBox."""

    tool = "netstat"

    def tpot_main(self, args: list[str]) -> int:
        busybox = self.style["style"] == "busybox"
        opts = set()
        longs = {"tcp": "t", "udp": "u", "raw": "w", "unix": "x", "listening": "l", "all": "a", "numeric": "n",
                 "programs": "p", "route": "r", "interfaces": "i", "version": "V", "help": "h", "wide": "W",
                 "extend": "e", "statistics": "s", "verbose": "v", "continuous": "c", "groups": "g",
                 "numeric-hosts": "n", "numeric-ports": "n", "numeric-users": "n", "timers": "o"}
        for arg in args:
            if arg.startswith("--") and len(arg) > 2:
                word = arg[2:]
                letter = longs.get(word) or next((v for k, v in longs.items() if k.startswith(word)), None)
                if letter is None or busybox:
                    return self.bad_option(arg, True, busybox)
                opts.add(letter)
            elif arg.startswith("-") and len(arg) > 1:
                for letter in arg[1:]:
                    allowed = "raltuwxenWp" if busybox else "AaCcdeFgGhiIlMnNoprsStTuUvVwWxZ46"
                    if letter not in allowed:
                        return self.bad_option(letter, False, busybox)
                    opts.add(letter)
            else:
                continue
        if "V" in opts:
            self.put(TPOT_NETSTAT_VERSION.get(self.style.get("version", "2.10"), TPOT_NETSTAT_VERSION["2.10"]))
            return 0
        if "h" in opts:
            out, err = self.help_text()
            self.put(out)
            self.warn(err)
            return 0
        if "r" in opts:
            return self.route_table("n" in opts)
        if "i" in opts:
            return self.interface_table()
        if "s" in opts:
            return 0
        return self.connections(opts, busybox)

    def help_text(self) -> str:
        if self.style.get("basename"):
            return TPOT_NETSTAT_HELP["2.0"]
        return TPOT_NETSTAT_HELP["1.60" if self.style.get("narrow") else "2.10"]

    def bad_option(self, opt: str, long: bool, busybox: bool) -> int:
        if busybox:
            self.warn(self.bb_usage(opt, long))
            return 1
        self.warn((f"netstat: unrecognized option '{opt}'\n" if long else f"netstat: invalid option -- '{opt}'\n")
                 + "".join(self.help_text()))
        return 3

    def bb_usage(self, opt: str, long: bool) -> str:
        banner = CowrieConfig.get("shell", "text_banner", fallback="") or self.style.get("version", "")
        libc = CowrieConfig.get("shell", "text_libc", fallback="glibc")
        word = opt[2:] if long else opt
        if libc == "musl":
            text = f"netstat: unrecognized option: {word}\n"
        elif libc == "uclibc":
            text = f"netstat: unrecognized option `{opt}'\n" if long else f"netstat: invalid option -- {opt}\n"
        else:
            text = f"netstat: unrecognized option '{opt}'\n" if long else f"netstat: invalid option -- '{opt}'\n"
        return (text + f"{banner} multi-call binary.\n\nUsage: netstat [-ral] [-tuwx] [-enWp]\n\n"
                "Display networking information\n\n\t-r\tRouting table\n\t-a\tAll sockets\n\t-l\tListening sockets\n"
                "\t\tElse: connected sockets\n\t-t\tTCP sockets\n\t-u\tUDP sockets\n\t-w\tRaw sockets\n\t-x\tUnix sockets\n"
                "\t\tElse: all socket types\n\t-e\tOther/more information\n\t-n\tDon't resolve names\n\t-W\tWide display\n"
                "\t-p\tShow PID/program name for sockets\n")

    def name_of(self, address: str, numeric: bool) -> str:
        host = address.partition("%")[0]
        if numeric:
            return host
        names = {"127.0.0.1": "localhost", "::1": "ip6-localhost" if self.net.get("family") not in ("rhel", "centos")
                 else "localhost", "127.0.0.53": "_localdnsstub" if self.net.get("resolved") else "127.0.0.53",
                 "0.0.0.0": "0.0.0.0", "::": "[::]"}
        if host == self.net["interfaces"][0].get("inet") and self.net.get("gateway_name"):
            try:
                return str(self.protocol.hostname)
            except Exception:  # noqa: BLE001
                return host
        return names.get(host, host)

    def endpoint(self, address: str, port, numeric: bool, wide: bool, v6: bool) -> str:
        if v6 and numeric:
            host = address.partition("%")[0]
            if host == "::":
                host = "::"
        else:
            host = self.name_of(address, numeric)
        service = "*" if port == "*" else (str(port) if numeric else TPOT_NET_SERVICES.get(int(port), str(port)))
        if v6 and numeric and host == "::":
            text = f":::{service}"
        elif v6 and numeric:
            text = f"{host}:{service}"
        else:
            text = f"{host}:{service}"
        if not wide and len(text) > 23:
            room = 22 - len(service)
            text = f"{host[:room]}:{service}"
        return text

    def connections(self, opts: set, busybox: bool) -> int:
        listen, every, numeric, programs, wide = "l" in opts, "a" in opts, "n" in opts, "p" in opts, "W" in opts
        kinds = {k for k, letter in (("tcp", "t"), ("udp", "u"), ("raw", "w"), ("unix", "x")) if letter in opts}
        if not kinds:
            kinds = {"tcp", "udp", "raw", "unix"}
        title = "(servers and established)" if every else "(only servers)" if listen else "(w/o servers)"
        inet = kinds & {"tcp", "udp", "raw"}
        privileged = True
        try:
            privileged = int(self.protocol.user.uid) == 0
        except Exception:  # noqa: BLE001
            pass
        if programs and not privileged and not busybox:
            self.warn("(Not all processes could be identified, non-owned process info\n"
                     " will not be shown, you would have to be root to see it all.)\n")
        if inet:
            self.put(f"Active Internet connections {title}\n")
            head = "Proto Recv-Q Send-Q Local Address           Foreign Address         State      "
            if busybox:
                self.put(head + " " + ("PID/Program name    " if programs else "") + "\n")
            else:
                self.put(head + (" PID/Program name    " if programs else "") + "\n")
            rows = []
            for s in self.sockets():
                if s["proto"] not in inet:
                    continue
                listening = s["state"] in ("LISTEN", "UNCONN")
                if (listen and not listening) or (not listen and not every and listening):
                    continue
                rows.append(s)
            rows.sort(key=lambda s: (s["proto"] != "tcp", s["family"] if not busybox else 0))
            for s in rows:
                v6 = s["family"] == 6
                proto = s["proto"] + ("6" if v6 and not busybox else "")
                local = self.endpoint(s["address"], s["port"], numeric, wide, v6)
                if s.get("peer"):
                    peer = self.endpoint(s["peer"], s["peer_port"], True if not numeric else numeric, wide, v6)
                else:
                    peer = ":::*" if v6 and (numeric or busybox) else ("[::]:*" if v6 else "0.0.0.0:*")
                state = {"LISTEN": "LISTEN", "ESTAB": "ESTABLISHED", "UNCONN": ""}[s["state"]]
                send = 0 if s["state"] != "ESTAB" else 36
                line = f"{proto:<5} {0:>6} {send:>6} {local:<23} {peer:<23} "
                if busybox:
                    line += f"{state:<12}"
                    if programs:
                        line += f"{s['pid']}/{s['comm']}" if s.get("pid") else "-"
                else:
                    line += f"{state:<11}"
                    if programs:
                        name = s.get("cmdline") or s["comm"]
                        if name.startswith("/") or self.style.get("basename"):
                            name = name.split()[0].rsplit("/", 1)[-1] if self.style.get("basename") else \
                                name.rsplit("/", 1)[-1]
                        prog = f"{s['pid']}/{name}"[:19] if (s.get("pid") and privileged) else "-"
                        line += f" {prog:<20}"
                self.put(line + "\n")
        if "unix" in kinds:
            self.put(f"Active UNIX domain sockets {title}\n")
            self.put("Proto RefCnt Flags       Type       State         I-Node" + (" Path\n" if busybox else "   Path\n"))
        return 0

    def route_table(self, numeric: bool) -> int:
        self.put("Kernel IP routing table\nDestination     Gateway         Genmask         Flags   MSS Window  irtt Iface\n")
        for entry in self.routes():
            dst, gateway = entry["dst"], entry["gateway"]
            if not numeric:
                dst = "default" if dst == "0.0.0.0" else dst
                if gateway != "0.0.0.0" and self.net.get("gateway_name"):
                    gateway = self.net["gateway_name"]
            self.put(f"{dst:<15} {gateway:<15} {entry['mask']:<15} {entry['flags']:<5} {0:>5} {0:<6} {0:>5} "
                     f"{entry['iface']}\n")
        return 0

    def interface_table(self) -> int:
        narrow = self.style.get("narrow")
        width = 8 if narrow else 15
        self.put("Kernel Interface table\n" + f"{'Iface':<{width}} {'MTU':>5} {'RX-OK':>8} RX-ERR RX-DRP RX-OVR "
                 f"{'TX-OK':>8} TX-ERR TX-DRP TX-OVR Flg\n")
        for iface in sorted(self.interfaces(), key=lambda i: i["name"]):
            if iface.get("state") == "DOWN":
                continue
            flags = "LRU" if iface.get("loopback") else "BMRU"
            self.put(f"{iface['name']:<{width}} {iface['mtu']:>5} {iface['rx'][1]:>8} {0:>6} {0:>6} {0:<6} "
                     f"{iface['tx'][1]:>8} {0:>6} {0:>6} {0:>6} {flags}\n")
        return 0


# --- ifconfig ---------------------------------------------------------------------------------

def tpot_net_scaled(value: int, si: bool, busybox: bool) -> str:
    """The size in brackets of ifconfig (one decimal, cut, as net-tools prints it)."""
    if busybox:
        units, base = ("B", "KiB", "MiB", "GiB", "TiB"), 1024
    elif si:
        units, base = ("B", "KB", "MB", "GB", "TB"), 1000
    else:
        units, base = ("B", "KiB", "MiB", "GiB", "TiB"), 1024
    unit = 0
    scaled = value
    while scaled >= base and unit < len(units) - 1:
        scaled /= base
        unit += 1
    whole = int(scaled)
    tenth = int((scaled - whole) * 10)
    return f"{whole}.{tenth} {units[unit]}"


class Command_tpot_ifconfig(TpotNetTool):
    """ifconfig of net-tools (the format of 2.10) or of BusyBox (the format of net-tools 1.60)."""

    tool = "ifconfig"

    def tpot_main(self, args: list[str]) -> int:
        busybox = self.style["style"] == "busybox"
        show_all, short = False, False
        names = []
        for arg in args:
            if arg in ("-a",):
                show_all = True
            elif arg == "-s" and not busybox:
                short = True
            elif arg in ("-v",):
                continue
            elif arg in ("-h", "--help"):
                if not busybox:
                    out, err = self.help_text()
                    self.put(out)
                    self.warn(err)
                    return 0
                if busybox:
                    self.warn(f"{CowrieConfig.get('shell', 'text_banner', fallback='BusyBox')} multi-call binary.\n\n"
                             "Usage: ifconfig [-a] interface [address]\n\nConfigure a network interface\n\n")
                    return 1
                self.warn(self.help_text())
                return 0
            elif arg.startswith("-"):
                if busybox:
                    names.append(arg)
                    continue
                self.warn(f"ifconfig: option `{arg}' not recognised.\nifconfig: `--help' gives usage information.\n")
                return 1
            else:
                names.append(arg)
        interfaces = sorted(self.interfaces(), key=lambda i: i["name"])
        if names:
            iface = next((i for i in interfaces if i["name"] == names[0]), None)
            if iface is None:
                self.warn(f"{'ifconfig: ' if busybox else ''}{names[0]}: error fetching interface information: "
                         "Device not found\n")
                return 1
            if len(names) > 1:
                # a change of the interface: root only
                try:
                    if int(self.protocol.user.uid) != 0:
                        self.warn("SIOCSIFFLAGS: Operation not permitted\n" if names[1] in ("up", "down") else
                                 "SIOCSIFADDR: Operation not permitted\n")
                        return 255 if not busybox else 1
                except Exception:  # noqa: BLE001
                    pass
                return 0
            interfaces = [iface]
        elif not show_all:
            interfaces = [i for i in interfaces if i.get("state") != "DOWN" or i.get("nocarrier")]
        if short:
            self.put("Iface      MTU    RX-OK RX-ERR RX-DRP RX-OVR    TX-OK TX-ERR TX-DRP TX-OVR Flg\n")
            for iface in interfaces:
                flags = "LRU" if iface.get("loopback") else "BMRU"
                self.put(f"{iface['name']:<15} {iface['mtu']:>5} {iface['rx'][1]:>8} {0:>6} {0:>6} {0:<6} "
                         f"{iface['tx'][1]:>8} {0:>6} {0:>6} {0:>6} {flags}\n")
            return 0
        for iface in interfaces:
            self.put(self.busybox_block(iface) if busybox else self.block(iface))
        return 0

    def help_text(self) -> str:
        if self.style.get("basename"):
            return TPOT_IFCONFIG_HELP["2.0"]
        return TPOT_IFCONFIG_HELP["1.60" if self.style.get("narrow") else "2.10"]

    def block(self, iface: dict) -> str:
        si = self.style.get("si")
        if iface.get("loopback"):
            text = (f"lo: flags=73<UP,LOOPBACK,RUNNING>  mtu {iface['mtu']}\n        inet 127.0.0.1  netmask 255.0.0.0\n"
                    "        inet6 ::1  prefixlen 128  scopeid 0x10<host>\n        loop  txqueuelen 1000  (Local Loopback)\n")
        else:
            flags = "4099<UP,BROADCAST,MULTICAST>" if iface.get("nocarrier") else \
                "4098<BROADCAST,MULTICAST>" if iface.get("state") == "DOWN" else "4163<UP,BROADCAST,RUNNING,MULTICAST>"
            text = f"{iface['name']}: flags={flags}  mtu {iface['mtu']}\n"
            if iface.get("inet"):
                text += f"        inet {iface['inet']}  netmask {iface['netmask']}  broadcast {iface['broadcast']}\n"
            if iface.get("inet6") and iface.get("state") == "UP":
                text += f"        inet6 {iface['inet6']}  prefixlen 64  scopeid 0x20<link>\n"
            text += f"        ether {iface['mac']}  txqueuelen 1000  (Ethernet)\n"
        rx, tx = iface["rx"], iface["tx"]
        return (text + f"        RX packets {rx[1]}  bytes {rx[0]} ({tpot_net_scaled(rx[0], si, False)})\n"
                "        RX errors 0  dropped 0  overruns 0  frame 0\n"
                f"        TX packets {tx[1]}  bytes {tx[0]} ({tpot_net_scaled(tx[0], si, False)})\n"
                "        TX errors 0  dropped 0 overruns 0  carrier 0  collisions 0\n\n")

    def busybox_block(self, iface: dict) -> str:
        rx, tx = iface["rx"], iface["tx"]
        if iface.get("loopback"):
            text = (f"{'lo':<10}Link encap:Local Loopback  \n          inet addr:127.0.0.1  Mask:255.0.0.0\n"
                    "          inet6 addr: ::1/128 Scope:Host\n"
                    f"          UP LOOPBACK RUNNING  MTU:{iface['mtu']}  Metric:1\n")
            queue = 1000
        else:
            text = f"{iface['name']:<10}Link encap:Ethernet  HWaddr {iface['mac'].upper()}  \n"
            if iface.get("inet"):
                text += f"          inet addr:{iface['inet']}  Bcast:{iface['broadcast']}  Mask:{iface['netmask']}\n"
            if iface.get("inet6"):
                text += f"          inet6 addr: {iface['inet6']}/64 Scope:Link\n"
            text += f"          UP BROADCAST RUNNING MULTICAST  MTU:{iface['mtu']}  Metric:1\n"
            queue = 1000
        return (text + f"          RX packets:{rx[1]} errors:0 dropped:0 overruns:0 frame:0\n"
                f"          TX packets:{tx[1]} errors:0 dropped:0 overruns:0 carrier:0\n"
                f"          collisions:0 txqueuelen:{queue} \n"
                f"          RX bytes:{rx[0]} ({tpot_net_scaled(rx[0], False, True)})  "
                f"TX bytes:{tx[0]} ({tpot_net_scaled(tx[0], False, True)})\n\n")


for _name in ("ip", "/sbin/ip", "/bin/ip", "/usr/sbin/ip", "/usr/bin/ip"):
    commands[_name] = Command_tpot_ip
for _name in ("ss", "/sbin/ss", "/bin/ss", "/usr/sbin/ss", "/usr/bin/ss"):
    commands[_name] = Command_tpot_ss
for _name in ("netstat", "/bin/netstat", "/usr/bin/netstat", "/sbin/netstat", "/usr/sbin/netstat"):
    commands[_name] = Command_tpot_netstat
'''


def patch_net_tools(cowrie_root):
    """ip, ss, netstat and ifconfig of the persona (NET_COMMAND after the commands of netstat.py; ifconfig.py
    registers its ifconfig): the interfaces, routes and sockets of [shell] network. Runs after patch_netstat."""
    path = cowrie_root / "src" / "cowrie" / "commands" / "netstat.py"
    replace_once(path, 'commands["/bin/netstat"] = Command_netstat\ncommands["netstat"] = Command_netstat\n',
                 'commands["/bin/netstat"] = Command_netstat\ncommands["netstat"] = Command_netstat\n' + NET_COMMAND)
    replace_once(path, "import ipaddress\nimport socket\n", "import ipaddress\nimport re\nimport socket\n")
    path = cowrie_root / "src" / "cowrie" / "commands" / "ifconfig.py"
    replace_once(path, 'commands["/sbin/ifconfig"] = Command_ifconfig\ncommands["ifconfig"] = Command_ifconfig\n',
                 'from cowrie.commands.netstat import Command_tpot_ifconfig  # noqa: E402\n\n'
                 'for _name in ("ifconfig", "/sbin/ifconfig", "/usr/sbin/ifconfig", "/bin/ifconfig"):\n'
                 '    commands[_name] = Command_tpot_ifconfig\n')


def patch_service(cowrie_root):
    """service --status-all lists the init scripts of the persona ([shell]
    service_status_all, a file of the runtime folder); without it a short
    systemd server list (upstream's names a desktop: lightdm, whoopsie, cups)."""
    path = cowrie_root / "src" / "cowrie" / "commands" / "service.py"
    text = path.read_text(encoding="utf-8")
    start = text.find("        output = (\n")
    end = text.find("        )\n        for line in output:", start)
    if start < 0 or end < 0:
        raise RuntimeError(f"Could not find expected text in {path}")
    end += len("        )\n")
    replacement = """        output: tuple[str, ...] = (
            " [ + ]  cron",
            " [ + ]  dbus",
            " [ - ]  hwclock.sh",
            " [ + ]  kmod",
            " [ + ]  procps",
            " [ + ]  ssh",
            " [ + ]  udev",
        )
        status_path = CowrieConfig.get("shell", "service_status_all", fallback="")
        if status_path:
            try:
                output = tuple(Path(status_path).read_text(encoding="utf-8").splitlines())
            except OSError:
                pass
"""
    path.write_text(text[:start] + replacement + text[end:], encoding="utf-8")
    replace_once(
        path,
        "from cowrie.shell.command import HoneyPotCommand\n",
        "from pathlib import Path\n\nfrom cowrie.core.config import CowrieConfig\n"
        "from cowrie.shell.command import HoneyPotCommand\n",
    )


# module code of the patched service.py after Command_service: systemctl of the persona
SYSTEMCTL_COMMAND = r"""

# T-Pot: systemctl of the persona: status, cat, is-active, is-enabled, is-failed, list-units,
# list-unit-files and the verbs that change units (silent as root or under sudo). The units,
# their processes and the version of systemd are those of [shell] systemctl_units (a JSON file
# of the runtime folder); nothing is started or stopped.
TPOT_SYSTEMCTL_CHANGES = ("start", "stop", "restart", "reload", "try-restart", "reload-or-restart",
                          "enable", "disable", "mask", "unmask", "kill", "reset-failed", "daemon-reload")
TPOT_SYSTEMCTL_FLAGS = ("--all", "-a", "--no-pager", "--no-legend", "--plain", "--full", "-l", "--quiet",
                        "-q", "--system", "--no-ask-password", "--now", "--failed", "--legend=false")
# options with a value (-n 20, --lines=20, -n20) and their long names
TPOT_SYSTEMCTL_VALUES = {"-n": "--lines", "-o": "--output", "-p": "--property", "-t": "--type", "-H": "--host",
                         "-M": "--machine", "-s": "--signal", "-P": "--value"}
TPOT_SYSTEMCTL_LONG_VALUES = frozenset({"--lines", "--output", "--property", "--type", "--host", "--machine",
                                        "--signal", "--state", "--kill-whom", "--job-mode", "--root", "--what",
                                        "--timestamp", "--message", "--legend", "--check-inhibitors"})
# the unit file states is-enabled answers 0 for (systemctl(1))
TPOT_SYSTEMCTL_ENABLED = frozenset({"enabled", "enabled-runtime", "static", "alias", "indirect", "generated",
                                    "transient"})


@functools.cache
def tpot_units() -> dict:
    path = CowrieConfig.get("shell", "systemctl_units", fallback="")
    if path:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        if isinstance(data, dict) and isinstance(data.get("units"), list):
            return data
    return {"version": 252, "unit_dir": "/usr/lib/systemd/system", "units": []}


def tpot_ago(seconds: float, version: int) -> str:
    # systemd's format_timestamp_relative; before 236 every unit in its plural
    s = max(0, int(seconds))
    old = version < 236

    def word(n, one, many):
        return many if old or n != 1 else one

    year, month, week, day, hour, minute = 31557600, 2629800, 604800, 86400, 3600, 60
    if s >= year:
        a, b = s // year, (s % year) // month
        return f"{a} {word(a, 'year', 'years')} {b} {word(b, 'month', 'months')} ago"
    if s >= month:
        a, b = s // month, (s % month) // day
        return f"{a} {word(a, 'month', 'months')} {b} {word(b, 'day', 'days')} ago"
    if s >= week:
        a, b = s // week, (s % week) // day
        return f"{a} {word(a, 'week', 'weeks')} {b} {word(b, 'day', 'days')} ago"
    if s >= 2 * day:
        return f"{s // day} days ago"
    if s >= 25 * hour:
        return f"1 day {(s - day) // hour}h ago"
    if s >= 6 * hour:
        return f"{s // hour}h ago"
    if s >= hour:
        return f"{s // hour}h {(s % hour) // minute}min ago"
    if s >= 5 * minute:
        return f"{s // minute}min ago"
    if s >= minute:
        return f"{s // minute}min {s % minute}s ago"
    return f"{s}s ago"


def tpot_bytes(value: int) -> str:
    # systemd's format_bytes: 1024 based, one decimal cut off, none for whole numbers
    for factor, suffix in ((1 << 30, "G"), (1 << 20, "M"), (1 << 10, "K")):
        if value >= factor:
            whole, tenth = value // factor, value * 10 // factor % 10
            return f"{whole}.{tenth}{suffix}" if tenth else f"{whole}{suffix}"
    return f"{value}B"


def tpot_timespan(ms: int) -> str:
    # systemd's format_timespan to the millisecond: 54ms, 1.204s, 2min 3.5s, 1h 2min 3s
    if ms < 1000:
        return f"{ms}ms"
    hours, rest = divmod(ms, 3600000)
    minutes, rest = divmod(rest, 60000)
    parts = [f"{hours}h"] if hours else []
    if minutes:
        parts.append(f"{minutes}min")
    if rest:
        seconds = f"{rest // 1000}.{rest % 1000:03d}".rstrip("0").rstrip(".")
        parts.append(f"{seconds}s")
    return " ".join(parts)


def tpot_unit(data: dict, name: str):
    # (the unit name systemd uses, its unit or None): a name without type is a service
    if "." not in name.rsplit("/", 1)[-1]:
        name += ".service"
    name = data.get("aliases", {}).get(name, name)
    return name, next((unit for unit in data["units"] if unit.get("name") == name), None)


def tpot_privileged(cmd) -> bool:
    # root, or the command sudo runs (patched sudo.py marks its pipe)
    return cmd.user.get("uid") == 0 or bool(getattr(cmd.pp, "tpot_sudo", False))


def tpot_command_line(command: str, version: int) -> str:
    # systemd 250 and later quote an argument with spaces (a process title "sshd: ...")
    return f'"{command}"' if version >= 250 and command.split(" ", 1)[0].endswith(":") else command


def tpot_status_unit(cmd, data: dict, unit: dict, boot: float, now: float, host: str) -> list[str]:
    version = int(data.get("version", 252))
    width = 11 if version >= 245 else 9

    def key(name):
        return f"{name:>{width}}: "

    since = boot + float(unit.get("start", 0))
    sub, state = unit.get("sub", "running"), unit.get("state", "enabled")
    preset = "preset" if version >= 252 else "vendor preset"
    loaded = (f"loaded ({unit.get('path')}; {state})" if state in ("static", "generated")
              else f"loaded ({unit.get('path')}; {state}; {preset}: {unit.get('preset', 'enabled')})")
    lines = [f"● {unit['name']} - {unit.get('description', '')}", key("Loaded") + loaded,
             key("Active") + f"active ({sub}) since {time.strftime('%a %Y-%m-%d %H:%M:%S UTC', time.gmtime(since))}; "
             f"{tpot_ago(now - since, version)}"]
    if unit.get("triggered_by"):
        lines.append(key("TriggeredBy") + f"● {unit['triggered_by']}")
    pid = unit.get("pid", 0)
    if unit["name"].endswith(".socket"):
        lines.append(key("Triggers") + f"\u25cf {unit.get('triggers', '')}")
        for index, entry in enumerate(unit.get("listen", [])):
            lines.append((key("Listen") if index == 0 else " " * (width + 2)) + entry)
        if version >= 227:
            lines.append(key("Tasks") + f"0 (limit: {data.get('tasks_max', 4096)})")
        if version >= 238:
            memory = key("Memory") + tpot_bytes(int(unit.get("memory", 0)))
            if version >= 254:
                memory += f" (peak: {tpot_bytes(int(unit.get('peak', 0)))})"
            lines.append(memory)
        if version >= 240:
            lines.append(key("CPU") + tpot_timespan(int(unit.get("cpu_ms", 0))))
        lines.append(key("CGroup") + f"/system.slice/{unit['name']}")
        stamp = time.strftime("%b %d %H:%M:%S", time.gmtime(since))
        label = f"{unit['name']} - {unit.get('description', '')}" if version >= 252 else unit.get("description", "")
        return lines + ["", f"{stamp} {host} systemd[1]: Listening on {label}."]
    if sub == "running":
        lines.append(key("Main PID") + f"{pid} ({unit.get('comm', '')})")
        if version >= 227:
            lines.append(key("Tasks") + f"{unit.get('tasks', 1)} (limit: {data.get('tasks_max', 4096)})")
        if version >= 238:
            memory = key("Memory") + tpot_bytes(int(unit.get("memory", 0)))
            if version >= 254:
                memory += f" (peak: {tpot_bytes(int(unit.get('peak', 0)))})"
            lines.append(memory)
        if version >= 240:
            lines.append(key("CPU") + tpot_timespan(int(unit.get("cpu_ms", 0))))
        lines.append(key("CGroup") + f"/system.slice/{unit['name']}")
        lines.append(" " * (width + 2) + f"└─{pid} {tpot_command_line(unit.get('command', ''), version)}")
    else:
        lines.append(key("Main PID") + f"{pid} (code=exited, status=0/SUCCESS)")
        if version >= 240:
            lines.append(key("CPU") + tpot_timespan(int(unit.get("cpu_ms", 0))))
    stamp = time.strftime("%b %d %H:%M:%S", time.gmtime(since))
    label = f"{unit['name']} - {unit.get('description', '')}" if version >= 252 else unit.get("description", "")
    done = "Finished" if sub == "exited" and version >= 246 else "Started"
    lines += ["", f"{stamp} {host} systemd[1]: Starting {label}..."]
    lines += [f"{stamp} {host} {unit.get('comm', '')}[{pid}]: {text}" for text in unit.get("log", [])]
    lines.append(f"{stamp} {host} systemd[1]: {done} {label}.")
    return lines


def tpot_status_system(data: dict, boot: float, now: float, host: str) -> list[str]:
    version = int(data.get("version", 252))
    width = 11 if version >= 245 else 9
    pad = " " * (width + 2)
    since = time.strftime("%a %Y-%m-%d %H:%M:%S UTC", time.gmtime(boot))
    lines = [f"● {host}", f"{'State':>{width}}: running", f"{'Jobs':>{width}}: 0 queued",
             f"{'Failed':>{width}}: 0 units", f"{'Since':>{width}}: {since}; {tpot_ago(now - boot, version)}",
             f"{'CGroup':>{width}}: /", pad + "├─init.scope",
             pad + f"│ └─1 {data.get('init', '/sbin/init')}", pad + "└─system.slice"]
    running = sorted((u for u in data["units"] if u.get("sub") == "running"), key=lambda u: u["name"].lower())
    for index, unit in enumerate(running):
        last = index == len(running) - 1
        lines.append(pad + "  " + ("└─" if last else "├─") + unit["name"])
        lines.append(pad + "  " + ("  " if last else "│ ") + f"└─{unit.get('pid', 0)} "
                     f"{tpot_command_line(unit.get('command', ''), version)}")
    return lines


def tpot_selected(unit: dict, kinds: set, states: set, file_states: bool = False) -> bool:
    # --type and --state: the type of the unit; its LOAD, ACTIVE or SUB state (the unit file state
    # for list-unit-files)
    if kinds and unit["name"].rsplit(".", 1)[-1] not in kinds:
        return False
    if not states:
        return True
    found = {unit.get("state", "enabled")} if file_states else {"loaded", "active", unit.get("sub", "")}
    return bool(found & states)


def tpot_list_units(cmd, data: dict, every: bool, legend: bool, kinds: set, states: set = frozenset()) -> None:
    version = int(data.get("version", 252))
    units = [u for u in data["units"] if tpot_selected(u, kinds, states)]
    if not units:
        cmd.write("0 loaded units listed.\n" if legend else "")
        return
    name_width = max(len("UNIT"), *(len(u["name"]) for u in units))
    sub_width = max(len("SUB"), *(len(u.get("sub", "")) for u in units))
    if legend:
        cmd.write(f"  {'UNIT':<{name_width}} LOAD   ACTIVE {'SUB':<{sub_width}} DESCRIPTION\n")
    for unit in units:
        cmd.write(f"  {unit['name']:<{name_width}} loaded active {unit.get('sub', ''):<{sub_width}} "
                  f"{unit.get('description', '')}\n")
    if not legend:
        return
    if version >= 251:
        cmd.write("\nLegend: LOAD   → Reflects whether the unit definition was properly loaded.\n"
                  "        ACTIVE → The high-level unit activation state, i.e. generalization of SUB.\n"
                  "        SUB    → The low-level unit activation state, values depend on unit type.\n\n")
    else:
        cmd.write("\nLOAD   = Reflects whether the unit definition was properly loaded.\n"
                  "ACTIVE = The high-level unit activation state, i.e. generalization of SUB.\n"
                  "SUB    = The low-level unit activation state, values depend on unit type.\n\n")
    hint = "" if every else " Pass --all to see loaded but inactive units, too."
    cmd.write(f"{len(units)} loaded units listed.{hint}\n"
              "To show all installed unit files use 'systemctl list-unit-files'.\n")


def tpot_list_unit_files(cmd, data: dict, legend: bool, kinds: set = frozenset(),
                         states: set = frozenset()) -> None:
    version = int(data.get("version", 252))
    units = sorted((u for u in data["units"] if tpot_selected(u, kinds, states, True)), key=lambda u: u["name"].lower())
    width = max([len("UNIT FILE")] + [len(u["name"]) for u in units])
    state_width = max([len("STATE")] + [len(u.get("state", "")) for u in units])
    preset = version >= 245
    title = "PRESET" if version >= 252 else "VENDOR PRESET"
    if legend:
        cmd.write(f"{'UNIT FILE':<{width}} " + (f"{'STATE':<{state_width}} {title}\n" if preset else "STATE\n"))
    for unit in units:
        state = unit.get("state", "enabled")
        cmd.write(f"{unit['name']:<{width}} " + (f"{state:<{state_width}} "
                                                 f"{'-' if state in ('static', 'generated') else unit.get('preset', 'enabled')}\n"
                                                 if preset else f"{state}\n"))
    if legend:
        cmd.write(f"\n{len(units)} unit files listed.\n")


def tpot_number(value: str, limit: int) -> int | None:
    # an unsigned number as strtoul of C reads it in these tools: ASCII digits only, at most limit
    if not (0 < len(value) <= len(str(limit)) and value.isascii() and value.isdigit()):
        return None
    number = int(value)
    return number if number <= limit else None


def tpot_systemctl(cmd, args: list[str]) -> int:
    # one systemctl call on cmd (a HoneyPotCommand); returns the exit status, 1 on an unexpected error
    try:
        return tpot_systemctl_run(cmd, args)
    except Exception:  # noqa: BLE001 - the command always exits
        return 1


def tpot_systemctl_run(cmd, args: list[str]) -> int:
    data = tpot_units()
    version = int(data.get("version", 252))
    words, every, legend, quiet = [], False, True, False
    values: dict[str, str] = {}
    items = list(args)
    while items:
        arg = items.pop(0)
        option = None
        if arg[:2] in TPOT_SYSTEMCTL_VALUES:
            option, value = TPOT_SYSTEMCTL_VALUES[arg[:2]], arg[2:]
            if not value:
                if not items:
                    cmd.errorWrite(f"systemctl: option requires an argument -- '{arg[1]}'\n")
                    return 1
                value = items.pop(0)
        elif arg.split("=", 1)[0] in TPOT_SYSTEMCTL_LONG_VALUES:
            option, _, value = arg.partition("=")
            if "=" not in arg:
                if not items:
                    cmd.errorWrite(f"systemctl: option '{arg}' requires an argument\n")
                    return 1
                value = items.pop(0)
        if option is not None:
            values[option] = value
            if option == "--legend":
                legend = legend and value not in ("false", "no", "0", "off")
            continue
        if arg == "--version":
            cmd.write(f"systemd {version}" + (f" ({data['full']})" if data.get("full") else "") + "\n"
                      + (data.get("features", "") + "\n" if data.get("features") else ""))
            return 0
        if arg in ("-h", "--help"):
            cmd.write("systemctl [OPTIONS...] COMMAND ...\n\nQuery or send control commands to the system manager.\n")
            return 0
        if arg in TPOT_SYSTEMCTL_FLAGS:
            every = every or arg in ("--all", "-a")
            legend = legend and arg not in ("--no-legend", "--legend=false")
            quiet = quiet or arg in ("-q", "--quiet")
        elif arg.startswith("--"):
            cmd.errorWrite(f"systemctl: unrecognized option '{arg}'\n")
            return 1
        elif arg.startswith("-") and len(arg) > 1:
            cmd.errorWrite(f"systemctl: invalid option -- '{arg[1]}'\n")
            return 1
        else:
            words.append(arg)
    lines = values.get("--lines")
    # safe_atou of systemd: 0 to 2^32-1
    if lines is not None and tpot_number(lines, 0xFFFFFFFF) is None:
        cmd.errorWrite(f"Failed to parse lines '{lines}'\n")
        return 1
    kinds = {kind for kind in values.get("--type", "").split(",") if kind}
    states = {state for state in values.get("--state", "").split(",") if state}
    verb, names = (words[0], words[1:]) if words else ("list-units", [])
    boot, now = cmd.protocol.boot_time(), time.time()
    host = cmd.protocol.hostname.split(".")[0]
    if verb == "list-units":
        tpot_list_units(cmd, data, every, legend, kinds, states)
        return 0
    if verb == "list-unit-files":
        tpot_list_unit_files(cmd, data, legend, kinds, states)
        return 0
    if verb == "status":
        if not names:
            cmd.write("".join(line + "\n" for line in tpot_status_system(data, boot, now, host)))
            return 0
        status, blocks = 0, []
        for name in names:
            name, unit = tpot_unit(data, name)
            if unit is None:
                cmd.errorWrite(f"Unit {name} could not be found.\n")
                status = 4
            else:
                block = tpot_status_unit(cmd, data, unit, boot, now, host)
                if lines is not None and "" in block:
                    # -n N: the last N lines of its journal
                    cut = block.index("")
                    journal = block[cut + 1:][-int(lines):] if int(lines) else []
                    block = block[:cut] + ([""] + journal if journal else [])
                blocks.append("".join(line + "\n" for line in block))
        cmd.write("\n".join(blocks))
        return status
    if verb == "cat":
        if not names:
            cmd.errorWrite("Too few arguments.\n")
            return 1
        # as systemd: a unit without a file is named on stderr, the others are still printed
        status, first = 0, True
        for name in names:
            name, unit = tpot_unit(data, name)
            try:
                text = cmd.fs.file_contents(unit["path"]) if unit else None
            except Exception:  # noqa: BLE001 - Cowrie's FileNotFound is no OSError; the command always exits
                text = None
            if not isinstance(text, bytes):
                cmd.errorWrite(f"No files found for {name}.\n")
                status = 1
                continue
            cmd.writeBytes((b"" if first else b"\n") + f"# {unit['path']}\n".encode() + text)
            first = False
        return status
    if verb in ("is-active", "is-enabled", "is-failed"):
        if not names:
            cmd.errorWrite("Too few arguments.\n")
            return 1
        # as systemd: 0 when one of the units is in the state asked for
        found, missing = False, False
        for name in names:
            name, unit = tpot_unit(data, name)
            if verb == "is-active":
                answer, ok = ("active", True) if unit else ("inactive", False)
            elif verb == "is-failed":
                answer, ok = ("active", False) if unit else ("inactive", False)
            elif unit is None:
                cmd.errorWrite(f"Failed to get unit file state for {name}: No such file or directory\n")
                missing = True
                continue
            else:
                answer = unit.get("state", "enabled")
                ok = answer in TPOT_SYSTEMCTL_ENABLED
            if not quiet:
                cmd.write(answer + "\n")
            found = found or ok
        if missing:
            return 1
        return 0 if found else (3 if verb == "is-active" else 1)
    if verb in TPOT_SYSTEMCTL_CHANGES:
        if verb == "daemon-reload":
            if tpot_privileged(cmd):
                return 0
            cmd.errorWrite("Failed to reload daemon: Interactive authentication required.\n")
            return 1
        if not names:
            cmd.errorWrite("Too few arguments.\n")
            return 1
        for name in names:
            name, unit = tpot_unit(data, name)
            if not tpot_privileged(cmd):
                cmd.errorWrite(f"Failed to {verb} {name}: Interactive authentication required.\n"
                               f"See system logs and 'systemctl status {name}' for details.\n")
                return 1
            if unit is None:
                cmd.errorWrite(f"Failed to {verb} {name}: Unit {name} not found.\n")
                return 5
        return 0
    cmd.errorWrite(f"Unknown command verb {verb}.\n" if version >= 240 else f"Unknown operation '{verb}'.\n")
    return 1


class Command_systemctl(HoneyPotCommand):
    def call(self) -> None:
        self.exit_code = tpot_systemctl(self, self.args)


commands["/usr/bin/systemctl"] = Command_systemctl
commands["/bin/systemctl"] = Command_systemctl
commands["systemctl"] = Command_systemctl
"""


def patch_systemctl(cowrie_root):
    """systemctl of the persona (SYSTEMCTL_COMMAND, in the module of service); `service X status`
    shows the status of X as systemctl does (Red Hat names the redirection first). Runs after
    patch_service."""
    path = cowrie_root / "src" / "cowrie" / "commands" / "service.py"
    replace_once(path, "import getopt\n", "import functools\nimport getopt\nimport json\nimport time\n")
    replace_once(
        path,
        """    def call(self) -> None:
        try:
            opts, args = getopt.gnu_getopt(
                self.args, "h", ["help", "status-all", "full-restart"]
            )
""",
        """    def call(self) -> None:
        # T-Pot: service NAME status is systemctl status NAME
        if len(self.args) >= 2 and self.args[1] == "status" and tpot_units().get("units"):
            if tpot_units().get("redirect"):
                self.errorWrite(f"Redirecting to /bin/systemctl status {tpot_unit(tpot_units(), self.args[0])[0]}\\n")
            self.exit_code = tpot_systemctl(self, ["status", self.args[0]])
            return
        try:
            opts, args = getopt.gnu_getopt(
                self.args, "h", ["help", "status-all", "full-restart"]
            )
""",
    )
    replace_once(path, 'commands["service"] = Command_service\n',
                 'commands["service"] = Command_service\n' + SYSTEMCTL_COMMAND)


def patch_sudo(cowrie_root):
    """sudo -V names [shell] sudo_version (1.9 adds the audit plugin); /usr/bin/sudo
    is the command too; sudo -l lists the persona's rules ([shell] sudo_list); the command
    sudo runs has pp.tpot_sudo (systemctl, apt update and yum update run as root then)."""
    path = cowrie_root / "src" / "cowrie" / "commands" / "sudo.py"
    replace_once(path, *CONFIG_IMPORT)
    replace_once(
        path,
        """        self.errorWrite(
            \"\"\"Sudo version 1.8.5p2
            Sudoers policy plugin version 1.8.5p2
            Sudoers file grammar version 41
            Sudoers I/O plugin version 1.8.5p2\\n\"\"\"
        )
""",
        """        version = CowrieConfig.get("shell", "sudo_version", fallback="1.8.5p2")
        grammar = CowrieConfig.get("shell", "sudo_grammar", fallback="41")
        lines = [f"Sudo version {version}", f"Sudoers policy plugin version {version}",
                 f"Sudoers file grammar version {grammar}", f"Sudoers I/O plugin version {version}"]
        if not version.startswith("1.8."):
            lines.append(f"Sudoers audit plugin version {version}")
        self.write("".join(line + "\\n" for line in lines))
""",
    )
    replace_once(
        path,
        """commands["sudo"] = Command_sudo
""",
        """commands["sudo"] = Command_sudo
commands["/usr/bin/sudo"] = Command_sudo
""",
    )
    # the command sudo runs knows it runs as root: sudo keeps the shell's user, so its pipe says so
    replace_once(
        path,
        """                pp.stdin_from_pipe = self.pp.stdin_from_pipe
                self.exec_command(pp, cmdclass, *parsed_arguments[1:])
""",
        """                pp.stdin_from_pipe = self.pp.stdin_from_pipe
                pp.tpot_sudo = True
                self.exec_command(pp, cmdclass, *parsed_arguments[1:])
""",
    )
    # sudo -l / -ll / --list: the rules of the persona's sudoers ([shell] sudo_list)
    replace_once(path, "import getopt\n", "import getopt\nimport json\nimport re\nfrom pathlib import Path\n")
    replace_once(
        path,
        """commands = {}
""",
        """commands = {}
# T-Pot: the options of sudo with a value (-u root, -uroot, --user root; --user=root is one word)
TPOT_SUDO_VALUES = "aCcDghpRrTtUu"
TPOT_SUDO_LONG_VALUES = frozenset({"--auth-type", "--close-from", "--login-class", "--chdir", "--group", "--host",
                                   "--prompt", "--chroot", "--role", "--command-timeout", "--type", "--other-user",
                                   "--user"})
""",
    )
    replace_once(
        path,
        """        try:
            optlist, _args = getopt.getopt(
                self.args[0:start_value], "bEeHhKknPSVva:C:g:i:l:p:r:s:t:U:u:"
            )
""",
        """        # T-Pot: sudo's options end at the first word that is no option (or after --); -l / --list
        # among them lists the rules, that word is the command (after VAR=value assignments)
        listing, index = False, 0
        while index < len(self.args):
            arg = self.args[index]
            if arg == "--":
                index += 1
                break
            if not arg.startswith("-") or arg == "-":
                break
            if arg.startswith("--"):
                listing = listing or arg == "--list"
                if arg in TPOT_SUDO_LONG_VALUES:
                    index += 1
            else:
                for pos, flag in enumerate(arg[1:], 1):
                    listing = listing or flag == "l"
                    if flag in TPOT_SUDO_VALUES:
                        if pos == len(arg) - 1:
                            index += 1
                        break
            index += 1
        while index < len(self.args) and re.match(r"[A-Za-z_][A-Za-z0-9_]*=", self.args[index]):
            index += 1
        if listing:
            self.tpot_list(parsed_arguments)
            return
        if index < len(self.args) and not self.protocol.getCommand(
                self.args[index], self.environ.get("PATH", "").split(":"), self.cwd):
            self.errorWrite(f"sudo: {self.args[index]}: command not found\\n")
            self.exit(1)
            return
        try:
            optlist, _args = getopt.getopt(
                self.args[0:start_value], "bEeHhKknPSVva:C:g:i:l:p:r:s:t:U:u:"
            )
""",
    )
    replace_once(
        path,
        """    def start(self) -> None:
        start_value = None
""",
        """    def tpot_list(self, command: list[str]) -> None:
        \"\"\"T-Pot: sudo -l as sudo prints it: the Defaults (wrapped as sudo's lbuf wraps them at
        80 columns) and the rules of the user; with a command its full path.\"\"\"
        path = CowrieConfig.get("shell", "sudo_list", fallback="")
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8")) if path else {}
        except (OSError, ValueError):
            data = {}
        name = self.user.get("username", "")
        name = name.decode("utf-8", "replace") if isinstance(name, bytes) else str(name)
        if self.user.get("uid") == 0:
            name = "root"
        host = self.protocol.hostname.split(".")[0]
        rules = data.get("rules", {}).get("root" if name == "root" else name)
        if rules is None:
            self.errorWrite(f"Sorry, user {name} may not run sudo on {host}.\\n")
            self.exit(1)
            return
        if command:
            found = self.protocol.getCommand(command[0], self.environ.get("PATH", "").split(":"), self.cwd)
            if not found:
                self.exit(1)
                return
            program = command[0] if "/" in command[0] else next(
                (f"{d}/{command[0]}" for d in self.environ.get("PATH", "").split(":")
                 if d and self.fs.exists(f"{d}/{command[0]}")), f"/usr/bin/{command[0]}")
            self.write(" ".join([program] + command[1:]) + "\\n")
            self.exit()
            return
        lines, line = [], "   "
        defaults = data.get("defaults", [])
        for index, entry in enumerate(defaults):
            text = entry + ("," if index < len(defaults) - 1 else "")
            if len(line) + 1 + len(text) > 80 and line.strip():
                lines.append(line)
                line = "   "
            line += " " + text
        lines.append(line)
        self.write(f"Matching Defaults entries for {name} on {host}:\\n" + "\\n".join(lines) + "\\n\\n"
                   f"User {name} may run the following commands on {host}:\\n"
                   + "".join(f"    {rule}\\n" for rule in rules))
        self.exit()

    def start(self) -> None:
        start_value = None
""",
    )


def patch_apt(cowrie_root):
    """apt / apt-get of the persona (and opkg of OpenWrt, OPKG_COMMAND): the version line ([shell] apt_version, deb_arch),
    `update` as root fetches the sources of the persona ([shell] apt_sources: "url suite"
    separated by commas), install takes them as its mirror and the arch of the persona,
    the versions of its packages ([shell] apt_packages) and for other packages one that
    fits the release ([shell] apt_fallback); options (-y, -q) are no packages. Without the
    keys upstream's text stays."""
    path = cowrie_root / "src" / "cowrie" / "commands" / "apt.py"
    replace_once(path, *CONFIG_IMPORT)
    replace_once(path, "import random\n",
                 "import fnmatch\nimport functools\nimport hashlib\nimport json\nimport random\n")
    replace_once(path, "from typing import TYPE_CHECKING, Any\n",
                 "from pathlib import Path\nfrom typing import TYPE_CHECKING, Any\n")
    replace_once(
        path,
        """commands = {}
""",
        """commands = {}


def tpot_apt(key: str, fallback: str) -> str:
    return CowrieConfig.get("shell", key, fallback=fallback)


def tpot_apt_banner() -> str:
    \"\"\"T-Pot: the version line of apt, "apt 2.8.3 (amd64)".\"\"\"
    version = tpot_apt("apt_version", "")
    if not version:
        return "apt 1.0.9.8.1 for amd64 compiled on Jun 10 2015 09:42:06"
    return f"apt {version} ({tpot_apt('deb_arch', 'amd64')})"


def tpot_apt_sources() -> list[tuple[str, str]]:
    \"\"\"T-Pot: (url, suite) of the persona's sources.\"\"\"
    sources = tpot_apt("apt_sources", "http://ftp.debian.org/debian stable")
    return [tuple(entry.split(" ", 1)) for entry in sources.split(",") if " " in entry]  # type: ignore[misc]


@functools.cache
def tpot_apt_data() -> dict[str, dict[str, str]]:
    \"\"\"T-Pot: what apt of the persona knows ([shell] apt_packages, a JSON file of the runtime
    folder): {"installed": {name: version}, "available": {name: version}}.\"\"\"
    path = tpot_apt("apt_packages", "")
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8")) if path else {}
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    found = {part: {str(k): str(v) for k, v in data.get(part, {}).items()} if isinstance(data.get(part), dict)
             else {} for part in ("installed", "available")}
    programs = data.get("programs") if isinstance(data.get("programs"), dict) else {}
    found["programs"] = {str(name): [str(path) for path in paths] for name, paths in programs.items()
                         if isinstance(paths, list)}
    unavailable = data.get("unavailable") if isinstance(data.get("unavailable"), list) else []
    found["unavailable"] = {str(name): "" for name in unavailable}
    answers = data.get("answers") if isinstance(data.get("answers"), list) else []
    found["answers"] = {str(name): "" for name in answers}
    return found


@functools.cache
def tpot_apt_lists() -> tuple[frozenset[str], tuple[str, ...], dict[str, list[str]]] | None:
    \"\"\"T-Pot: what apt finds on the release ([shell] package_names): the names of its packages (one
    per line), the same sorted for globs, and its virtual names with their providers ("name
    provider,..."); None without the key (every name is found then).\"\"\"
    path = tpot_apt("package_names", "")
    if not path:
        return None
    try:
        rows = [line.split(" ") for line in Path(path).read_text(encoding="utf-8").splitlines() if line]
    except (OSError, ValueError):
        return None
    names = frozenset(row[0] for row in rows if len(row) == 1)
    virtuals = {row[0]: row[1].split(",") for row in rows if len(row) == 2}
    return names, tuple(sorted(names)), virtuals


def tpot_apt_names() -> frozenset[str] | None:
    \"\"\"T-Pot: the names of the release's packages, None without [shell] package_names.\"\"\"
    lists = tpot_apt_lists()
    return lists[0] if lists else None


# T-Pot: the characters that make apt try a name as a glob and (apt-get) as a regular expression
TPOT_APT_GLOB = ".?*[]!"
TPOT_APT_REGEX = ".?+*|[^$"
TPOT_APT_GLOBS = 16
TPOT_APT_GLOB_MATCHES = 64
# the lock of dpkg for a user who is not root: the frontend lock of apt 1.7 and later (1.8 of Debian 10 to
# 3.0 checked), the lock of the administration directory before (1.4 of Debian 9)
TPOT_APT_LOCK = ("E: Could not open lock file /var/lib/dpkg/lock-frontend - open (13: Permission denied)\\n"
                 "E: Unable to acquire the dpkg frontend lock (/var/lib/dpkg/lock-frontend), are you root?\\n")
TPOT_APT_LOCK_OLD = ("E: Could not open lock file /var/lib/dpkg/lock - open (13: Permission denied)\\n"
                     "E: Unable to lock the administration directory (/var/lib/dpkg/), are you root?\\n")
TPOT_APT_WARNING = "\\nWARNING: apt does not have a stable CLI interface. Use with caution in scripts.\\n\\n"
# an install sleeps up to 6 s per package, all packages of one run together at most about 60 s
TPOT_APT_PACE = 10


def tpot_apt_glob(word: str) -> list[str]:
    \"\"\"T-Pot: the names of the release a glob (nginx*, ht?p) matches, at most TPOT_APT_GLOB_MATCHES.\"\"\"
    lists = tpot_apt_lists()
    if lists is None or not re.fullmatch(r"[a-z0-9+.*?\\[\\]-]{1,64}", word) or not set(word) & set("*?["):
        return []
    found = []
    for name in lists[1]:
        if fnmatch.fnmatchcase(name, word):
            found.append(name)
            if len(found) == TPOT_APT_GLOB_MATCHES:
                break
    return found


def tpot_apt_missing(word: str, prog: str) -> str:
    \"\"\"T-Pot: the errors of apt for a name the release does not have.\"\"\"
    text = f"E: Unable to locate package {word}\\n"
    if set(word) & set(TPOT_APT_GLOB):
        text += f"E: Couldn't find any package by glob '{word}'\\n"
    if prog == "apt-get" and set(word) & set(TPOT_APT_REGEX):
        text += f"E: Couldn't find any package by regex '{word}'\\n"
    return text


def tpot_apt_release() -> tuple[int, int]:
    \"\"\"T-Pot: major and minor of the persona's apt ([shell] apt_version), (0, 0) without it.\"\"\"
    version = re.match(r"(\\d+)\\.(\\d+)", tpot_apt("apt_version", ""))
    return (int(version.group(1)), int(version.group(2))) if version else (0, 0)


def tpot_apt_three() -> bool:
    \"\"\"T-Pot: apt 2.9 and later (Debian 13): the apt program writes Error: and its new layout.\"\"\"
    return tpot_apt_release() >= (2, 9)


def tpot_apt_lock() -> str:
    \"\"\"T-Pot: dpkg's lock as the persona's apt names it.\"\"\"
    return TPOT_APT_LOCK if tpot_apt_release() >= (1, 7) else TPOT_APT_LOCK_OLD


def tpot_apt_tty(cmd) -> bool:
    \"\"\"T-Pot: stdout of the command is the terminal of an interactive session.\"\"\"
    targets = getattr(cmd.pp, "targets", None) or {}
    return (targets.get(1) or ("",))[0] == "terminal" and not hasattr(cmd.protocol, "execcmd")


def tpot_apt_errors(cmd, text: str) -> str:
    \"\"\"T-Pot: the error lines (E: ...) as the program writes them: apt on a terminal in colour,
    apt 2.9 and later as Error:; apt-get as they are.\"\"\"
    if getattr(cmd, "tpot_prog", "apt-get") != "apt":
        return text
    three, tty = tpot_apt_three(), tpot_apt_tty(cmd)
    lines = []
    for line in text.splitlines():
        if not line.startswith("E: "):
            lines.append(line)
        elif three:
            lines.append(f"\\x1b[1;31mError: \\x1b[0m\\x1b[1m{line[3:]}\\x1b[0m" if tty else f"Error: {line[3:]}")
        else:
            # apt 1.x writes no colours (1.4 of Debian 9 and 1.8 of Debian 10 checked)
            lines.append(f"\\x1b[1;31mE: \\x1b[0m{line[3:]}\\x1b[0m" if tty and tpot_apt_release() >= (2, 0) else line)
    return "".join(line + "\\n" for line in lines)


def tpot_apt_head(cmd) -> str:
    \"\"\"T-Pot: the progress lines before apt's answer: "... Done" on a terminal (where apt 2.9 and
    later clears them), without "Done" elsewhere.\"\"\"
    if not tpot_apt("apt_version", ""):
        return "Reading package lists... Done\\nBuilding dependency tree\\nReading state information... Done\\n"
    if tpot_apt_tty(cmd):
        if getattr(cmd, "tpot_prog", "apt-get") == "apt" and tpot_apt_three():
            return ""
        if tpot_apt_release() < (2, 0):
            # apt 1.x pads its tree line (1.4 of Debian 9, 1.8 of Debian 10)
            return "Reading package lists... Done\\nBuilding dependency tree       \\nReading state information... Done\\n"
        return "Reading package lists... Done\\nBuilding dependency tree... Done\\nReading state information... Done\\n"
    return "Reading package lists...\\nBuilding dependency tree...\\nReading state information...\\n"


def tpot_apt_bytes(size: float) -> str:
    \"\"\"T-Pot: a size as apt writes it (SizeToStr: 57.9 kB, 173 kB, 210 GB).\"\"\"
    for unit in ("", "k", "M", "G", "T", "P"):
        if size < 100 and unit:
            return f"{size:.1f} {unit}B"
        if size < 10000:
            return f"{size:.0f} {unit}B"
        size /= 1000
    return f"{size:.0f} EB"


def tpot_apt_free() -> str:
    \"\"\"T-Pot: the space left on / of the persona ([shell] df_table, KiB).\"\"\"
    try:
        table = json.loads(Path(CowrieConfig.get("shell", "df_table", fallback="")).read_text(encoding="utf-8"))
        avail = next(row["avail"] for row in table["filesystems"] if row.get("target") == "/" and row.get("size"))
    except (OSError, ValueError, KeyError, TypeError, StopIteration):
        avail = 20 * 1024 * 1024
    return tpot_apt_bytes(avail * 1024)


def tpot_upstream(version: str) -> str:
    \"\"\"T-Pot: the upstream part of a package version (no epoch, no revision): 1:2.39.5-0+deb12u2 -> 2.39.5.\"\"\"
    plain = version.split(":", 1)[-1]
    return plain.rpartition("-")[0] or plain


def tpot_mark_installed(protocol, name: str, version: str) -> None:
    \"\"\"T-Pot: a program this session installed answers although the persona skips it.\"\"\"
    installed = getattr(protocol, "tpot_installed", None)
    if installed is None:
        installed = {}
        protocol.tpot_installed = installed
    installed[name] = tpot_upstream(version)


def tpot_apt_known() -> dict[str, str]:
    \"\"\"T-Pot: the installed packages of the persona and their versions.\"\"\"
    return tpot_apt_data()["installed"]


def tpot_apt_digest(name: str) -> bytes:
    return hashlib.sha256(f"{tpot_apt('apt_fallback', '')} {name}".encode()).digest()


def tpot_apt_version(name: str) -> str:
    \"\"\"T-Pot: the version of a package: the persona's (installed or available), else the same
    for a name on every call, a Debian revision ("-2", "-1+b1") or one of Ubuntu ("-1build2",
    "-2ubuntu1") after [shell] apt_fallback.\"\"\"
    data = tpot_apt_data()
    for part in ("installed", "available"):
        if name in data[part]:
            return data[part][name]
    d = tpot_apt_digest(name)
    upstream = f"{1 + d[0] % 4}.{d[1] % 24}" + (f".{d[2] % 12}" if d[3] % 3 else "")
    revision = 1 + d[4] % 3
    if tpot_apt("apt_fallback", "debian") == "ubuntu":
        return f"{upstream}-{revision}" + (f"build{1 + d[5] % 3}" if d[6] % 2 else f"ubuntu{1 + d[5] % 3}")
    return f"{upstream}-{revision}" + ("+b1" if d[6] % 4 == 0 else "")


def tpot_apt_size(name: str) -> int:
    d = tpot_apt_digest(name)
    return 40 + int.from_bytes(d[7:9], "big") % 900
""",
    )
    replace_all(
        path,
        '''"""apt 1.0.9.8.1 for amd64 compiled on Jun 10 2015 09:42:06
''',
        '''tpot_apt_banner() + """
''',
    )
    replace_once(
        path,
        """        self.packages = {}
        if len(self.args) == 0:
            self.do_help()
        elif self.args[0] == "-v":
            self.do_version()
        elif self.args[0] == "install":
            yield self.do_install()
        elif self.args[0] == "moo":
            self.do_moo()
        else:
            self.do_locked()
""",
        """        self.packages = {}
        # T-Pot: options (-y, -q, --no-install-recommends) and the values of -o, -c, -t, -a are no
        # command and no package
        self.tpot_words = []
        items = list(self.args)
        while items:
            arg = items.pop(0)
            if arg in ("-o", "-c", "-t", "-a", "--option", "--config-file", "--target-release",
                       "--default-release", "--host-architecture"):
                if items:
                    items.pop(0)
            elif not arg.startswith("-"):
                self.tpot_words.append(arg)
        if len(self.args) == 0:
            self.do_help()
        elif self.args[0] in ("-v", "--version"):
            self.do_version()
        elif self.tpot_words[:1] == ["install"] and not (self.user["uid"] == 0 or getattr(self.pp, "tpot_sudo", False)):
            # T-Pot: dpkg's lock is root's
            if getattr(self, "tpot_prog", "apt-get") == "apt" and not tpot_apt_tty(self) and tpot_apt("apt_version", ""):
                self.errorWrite(TPOT_APT_WARNING)
            self.errorWrite(tpot_apt_errors(self, tpot_apt_lock()))
            self.exit(100)
        elif self.tpot_words[:1] == ["install"]:
            yield self.do_install()
        elif self.tpot_words[:1] == ["update"] and (self.user["uid"] == 0 or getattr(self.pp, "tpot_sudo", False)):
            yield self.tpot_update()
        elif self.tpot_words[:1] == ["moo"]:
            self.do_moo()
        else:
            self.do_locked()

    @inlineCallbacks
    def tpot_update(self):
        \"\"\"T-Pot: apt-get update as root, the sources of the persona unchanged.\"\"\"
        for number, (url, suite) in enumerate(tpot_apt_sources(), 1):
            yield self.sleep(0.2, 0.6)
            self.write(f"Hit:{number} {url} {suite} InRelease\\n")
        self.write("Reading package lists... Done\\n")
        self.exit()
""",
    )
    replace_once(
        path,
        """        if len(self.args) <= 1:
""",
        """        if len(self.tpot_words) <= 1:
""",
    )
    # install: the versions of the persona, the output of apt (Selecting, Preparing to unpack, triggers last)
    replace_once(
        path,
        """        for y in [re.sub("[^A-Za-z0-9]", "", x) for x in self.args[1:]]:
            self.packages[y] = {
                "version": f"{random.choice([0, 1])}.{random.randint(1, 40)}-{random.randint(1, 10)}",
                "size": random.randint(100, 900),
            }
        totalsize: int = sum(self.packages[x]["size"] for x in self.packages)

        self.write("Reading package lists... Done\\n")
        self.write("Building dependency tree\\n")
        self.write("Reading state information... Done\\n")
        self.write("The following NEW packages will be installed:\\n")
        self.write(f"  {' '.join(self.packages)} \\n")
        self.write(
            f"0 upgraded, {len(self.packages)} newly installed, 0 to remove and 259 not upgraded.\\n"
        )
        self.write(f"Need to get {totalsize}.2kB of archives.\\n")
        self.write(
            f"After this operation, {totalsize * 2.2:.1f}kB of additional disk space will be used.\\n"
        )
        i = 1
        for p in self.packages:
            self.write(
                f"Get:{i} http://ftp.debian.org stable/main {p} {self.packages[p]['version']} [{self.packages[p]['size']}.2kB]\\n"
            )
            i += 1
            yield self.sleep(1, 2)
        self.write(f"Fetched {totalsize}.2kB in 1s (4493B/s)\\n")
        self.write("Reading package fields... Done\\n")
        yield self.sleep(1, 2)
        self.write("Reading package status... Done\\n")
        self.write(
            "(Reading database ... 177887 files and directories currently installed.)\\n"
        )
        yield self.sleep(1, 2)
        for p in self.packages:
            self.write(
                f"Unpacking {p} (from .../archives/{p}_{self.packages[p]['version']}_i386.deb) ...\\n"
            )
            yield self.sleep(1, 2)
        self.write("Processing triggers for man-db ...\\n")
        yield self.sleep(2)
        for p in self.packages:
            self.write(f"Setting up {p} ({self.packages[p]['version']}) ...\\n")
            self.fs.mkfile(
                f"/usr/bin/{p}",
                self.user["uid"],
                self.user["gid"],
                random.randint(10000, 90000),
                33188,
            )
            self.protocol.commands[f"/usr/bin/{p}"] = (
                Command_faked_package_class_factory.getCommand(p)
            )
            yield self.sleep(2)
        self.exit()
""",
        """        # T-Pot: package names as apt takes them (Debian's form, an architecture after the colon), a
        # path is a .deb file; with the names of the release ([shell] package_names) a name it does
        # not have is not found (nothing is installed then), a virtual name takes its only provider
        # or names its providers, and a glob takes the names it matches; an installed package of the
        # persona is already the newest version, another one gets a version that fits the release
        prog = getattr(self, "tpot_prog", "apt-get")
        release = tpot_apt_names()
        virtuals = (tpot_apt_lists() or (None, None, {}))[2]
        names, errors, notes = {}, [], []
        globs = 0
        for word in self.tpot_words[1:]:
            found = re.fullmatch(r"([a-z0-9][a-z0-9+.-]+)(?::[a-z0-9-]+)?", word)
            name = found.group(1) if found else ""
            matches = []
            if "/" not in word and set(word) & set("*?[") and globs < TPOT_APT_GLOBS:
                globs += 1
                matches = tpot_apt_glob(word)
            if "/" in word or word.endswith(".deb") or word in (".", ".."):
                errors.append(f"E: Unsupported file {word} given on commandline\\n")
            elif matches:
                names.update(dict.fromkeys(matches))
            elif name and name in tpot_apt_data()["unavailable"]:
                # a package of another family the release does not have (yum)
                notes.append(f"Package {name} is not available, but is referred to by another package.\\n"
                             "This may mean that the package is missing, has been obsoleted, or\\n"
                             "is only available from another source\\n\\n")
                errors.append(f"E: Package '{name}' has no installation candidate\\n")
            elif found and (release is None or name in release):
                names[name] = None
            elif name in virtuals and len(virtuals[name]) > 1:
                providers = "".join(f"  {p} {tpot_apt_version(p)}\\n" for p in virtuals[name])
                notes.append(f"Package {name} is a virtual package provided by:\\n{providers}"
                             "You should explicitly select one to install.\\n\\n")
                errors.append(f"E: Package '{name}' has no installation candidate\\n")
            elif name in virtuals:
                names[virtuals[name][0]] = None
            else:
                errors.append(tpot_apt_missing(word, prog))
        known = tpot_apt_known()
        for y in names:
            if y and y not in known:
                self.packages[y] = {"version": tpot_apt_version(y), "size": tpot_apt_size(y)}
        totalsize: int = sum(self.packages[x]["size"] for x in self.packages)
        three, tty = prog == "apt" and tpot_apt_three(), tpot_apt_tty(self)
        pace = min(1.0, TPOT_APT_PACE / max(1, len(self.packages)))

        if prog == "apt" and not tty and tpot_apt("apt_version", ""):
            self.errorWrite(TPOT_APT_WARNING)
        self.write(tpot_apt_head(self))
        if errors:
            for note in notes:
                self.write(note)
            self.errorWrite(tpot_apt_errors(self, "".join(errors)))
            self.exit(100)
            return
        for y in names:
            if y in known:
                self.write(f"{y} is already the newest version ({known[y]}).\\n")
        if not self.packages:
            if three:
                self.write("Summary:\\n  Upgrading: 0, Installing: 0, Removing: 0, Not Upgrading: 259\\n")
            else:
                self.write("0 upgraded, 0 newly installed, 0 to remove and 259 not upgraded.\\n")
            self.exit()
            return
        if three:
            listing = "  " + " ".join(self.packages)
            self.write("Installing:\\n" + (f"\\x1b[32m{listing}\\n\\x1b[0m\\n" if tty else f"{listing}\\n\\n"))
            self.write(f"Summary:\\n  Upgrading: 0, Installing: {len(self.packages)}, Removing: 0, "
                       "Not Upgrading: 259\\n"
                       f"  Download size: {tpot_apt_bytes(totalsize * 1000)}\\n"
                       f"  Space needed: {tpot_apt_bytes(int(totalsize * 2.2) * 1000)} / {tpot_apt_free()} "
                       "available\\n\\n")
        else:
            self.write("The following NEW packages will be installed:\\n")
            self.write(f"  {' '.join(self.packages)} \\n")
            self.write(
                f"0 upgraded, {len(self.packages)} newly installed, 0 to remove and 259 not upgraded.\\n"
            )
            self.write(f"Need to get {totalsize} kB of archives.\\n")
            self.write(f"After this operation, {int(totalsize * 2.2)} kB of additional disk space will be used.\\n")
        arch = tpot_apt("deb_arch", "i386")
        url, suite = tpot_apt_sources()[0]
        i = 1
        for p in self.packages:
            self.write(
                f"Get:{i} {url} {suite}/main {arch} {p} {arch} {self.packages[p]['version']} "
                f"[{self.packages[p]['size']} kB]\\n"
            )
            i += 1
            yield self.sleep(pace, 2 * pace)
        self.write(f"Fetched {totalsize} kB in 1s ({totalsize} kB/s)\\n")
        yield self.sleep(1, 2)
        for number, p in enumerate(self.packages):
            version = self.packages[p]["version"]
            self.write(f"Selecting previously unselected package {p}.\\n")
            if number == 0:
                self.write("(Reading database ... 177887 files and directories currently installed.)\\n")
            self.write(f"Preparing to unpack .../{p}_{version.replace(':', '%3a')}_{arch}.deb ...\\n")
            self.write(f"Unpacking {p} ({version}) ...\\n")
            yield self.sleep(pace, 2 * pace)
        for p in self.packages:
            self.write(f"Setting up {p} ({self.packages[p]['version']}) ...\\n")
            # the programs of the persona's data that answer as installed ([shell] apt_packages "answers")
            # do, with Cowrie's command of their name; another one is a faked program, also where Cowrie's
            # command of that name stays skipped (dnf, rpm, lspci, gcc on Debian); a package without data
            # leaves a program of the persona under its name as it is
            modelled = tpot_apt_data()["programs"].get(p)
            for program in modelled or [f"/usr/bin/{p}"]:
                name = program.rsplit("/", 1)[-1]
                if not modelled and self.fs.exists(program):
                    continue
                self.fs.mkfile(
                    program,
                    self.user["uid"],
                    self.user["gid"],
                    random.randint(10000, 90000),
                    33188,
                )
                faked = Command_faked_package_class_factory.getCommand(name)
                if modelled and name in tpot_apt_data()["answers"]:
                    tpot_mark_installed(self.protocol, name, self.packages[p]["version"])
                    if name not in self.protocol.commands:
                        self.protocol.commands[program] = faked
                elif name in self.protocol.commands or program in self.protocol.commands:
                    session = getattr(self.protocol, "tpot_faked", None)
                    if session is None:
                        session = {}
                        self.protocol.tpot_faked = session
                    session[program] = faked
                else:
                    self.protocol.commands[program] = faked
            yield self.sleep(2 * pace)
        self.write(f"Processing triggers for man-db ({tpot_apt_version('man-db')}) ...\\n")
        self.exit()""",
    )
    # apt and apt-get differ in their errors for a name that is no package (tpot_apt_missing)
    replace_once(
        path,
        """commands["/usr/bin/apt"] = Command_aptget
commands["/bin/apt"] = Command_aptget
commands["apt"] = Command_aptget
""",
        """

class Command_apt(Command_aptget):
    \"\"\"T-Pot: apt, apt-get under its other name.\"\"\"

    tpot_prog = "apt"


commands["/usr/bin/apt"] = Command_apt
commands["/bin/apt"] = Command_apt
commands["apt"] = Command_apt
""" + OPKG_COMMAND,
    )


def patch_yum(cowrie_root):
    """yum 3 of CentOS 7: the plugins of the persona ([shell] yum_plugins), without
    the lines of plugins it does not load (priorities, kernel-module); -y installs
    without asking, options are no packages; `update` as root finds nothing to do.
    install takes the names of the release ([shell] package_names): another name has
    no package, an installed package of the persona ([shell] yum_installed) is there."""
    path = cowrie_root / "src" / "cowrie" / "commands" / "yum.py"
    replace_once(path, *CONFIG_IMPORT)
    replace_once(
        path,
        """commands = {}
""",
        """commands = {}


def tpot_yum_plugins() -> str:
    return CowrieConfig.get(
        "shell", "yum_plugins", fallback="changelog, kernel-module, ovl, priorities, tsflags, versionlock"
    )


import functools as tpot_functools  # noqa: E402
import pathlib as tpot_pathlib  # noqa: E402


@tpot_functools.cache
def tpot_yum_lists() -> tuple[frozenset[str], dict[str, str]] | None:
    \"\"\"T-Pot: the names of the release's packages ([shell] package_names, one per line) and the
    names they provide ("name package"); None without the key (every name is found then).\"\"\"
    path = CowrieConfig.get("shell", "package_names", fallback="")
    if not path:
        return None
    try:
        rows = [line.split(" ") for line in tpot_pathlib.Path(path).read_text(encoding="utf-8").splitlines() if line]
    except (OSError, ValueError):
        return None
    return frozenset(row[0] for row in rows if len(row) == 1), {row[0]: row[1] for row in rows if len(row) == 2}


def tpot_yum_bold(cmd, text: str) -> str:
    \"\"\"T-Pot: yum marks a name bold where stdout is the terminal of an interactive session.\"\"\"
    targets = getattr(cmd.pp, "targets", None) or {}
    tty = (targets.get(1) or ("",))[0] == "terminal" and not hasattr(cmd.protocol, "execcmd")
    return f"\\x1b[1m{text}\\x1b(B\\x1b[m" if tty else text


# the installed packages of a CentOS 7 that are not built for x86_64
TPOT_YUM_NOARCH = frozenset({"yum"})


def tpot_yum_installed() -> dict[str, str]:
    \"\"\"T-Pot: the installed packages of the persona ([shell] yum_installed "name evr,..."): {name: evr}.\"\"\"
    entries = CowrieConfig.get("shell", "yum_installed", fallback="")
    return dict(entry.split(" ", 1) for entry in entries.split(",") if " " in entry)


def tpot_yum_present(name: str, evr: str) -> str:
    \"\"\"T-Pot: yum's line for a package that is installed: the epoch before the name.\"\"\"
    epoch, _, plain = evr.rpartition(":")
    arch = "noarch" if name in TPOT_YUM_NOARCH else "x86_64"
    return f"Package {epoch + ':' if epoch else ''}{name}-{plain}.{arch} already installed and latest version\\n"
""",
    )
    replace_all(
        path,
        '"Loaded plugins: changelog, kernel-module, ovl, priorities, tsflags, versionlock\\n"',
        'f"Loaded plugins: {tpot_yum_plugins()}\\n"',
    )
    replace_once(
        path,
        """            \"\"\"Loaded plugins: changelog, kernel-module, ovl, priorities, tsflags, versionlock
""",
        """            f\"\"\"Loaded plugins: {tpot_yum_plugins()}
""",
    )
    replace_once(
        path,
        """        self.packages = {}
        match self.args:
            case []:
                yield self.do_help()
            case ["version", *_]:
                yield self.do_version()
            case ["install", *_]:
                yield self.do_install()
            case _:
                self.do_locked()
""",
        """        self.packages = {}
        # T-Pot: options (-y, -q) are no command and no package
        self.tpot_words = [arg for arg in self.args if not arg.startswith("-")]
        self.tpot_assume_yes = any(arg in ("-y", "--assumeyes") for arg in self.args)
        match self.tpot_words:
            case [] if not self.args:
                yield self.do_help()
            case ["version", *_]:
                yield self.do_version()
            case ["install", *_] if self.user["uid"] != 0 and not getattr(self.pp, "tpot_sudo", False):
                self.do_locked()
            case ["install", *_]:
                yield self.do_install()
            case ["update" | "upgrade" | "check-update", *_] if self.user["uid"] == 0 or getattr(
                self.pp, "tpot_sudo", False
            ):
                yield self.sleep(1, 2)
                self.write(f"Loaded plugins: {tpot_yum_plugins()}\\n")
                self.write("Loading mirror speeds from cached hostfile\\n")
                self.write("No packages marked for update\\n")
                self.exit()
            case _:
                self.do_locked()
""",
    )
    replace_once(
        path,
        """        self.errorWrite(
            f"Loaded plugins: {tpot_yum_plugins()}\\n"
        )
        self.errorWrite("ovl: Error while doing RPMdb copy-up:\\n")
        self.errorWrite("[Errno 13] Permission denied: '/var/lib/rpm/.dbenv.lock' \\n")
        self.errorWrite("You need to be root to perform this command.\\n")
        self.exit()
""",
        """        # T-Pot: the plugins line goes to stdout (centos:7 as a user)
        self.write(f"Loaded plugins: {tpot_yum_plugins()}\\n")
        if "ovl" in tpot_yum_plugins():
            self.errorWrite("ovl: Error while doing RPMdb copy-up:\\n")
            self.errorWrite("[Errno 13] Permission denied: '/var/lib/rpm/.dbenv.lock' \\n")
        self.errorWrite("You need to be root to perform this command.\\n")
        self.exit(1)
""",
    )
    replace_once(
        path,
        """        if len(self.args) <= 1:
""",
        """        if len(self.tpot_words) <= 1:
""",
    )
    replace_once(
        path,
        """        for y in [re.sub("[^A-Za-z0-9]", "", x) for x in self.args[1:]]:
""",
        """        # T-Pot: names as yum takes them (an architecture after a dot); a name the release does not
        # have has no package, a name a package provides takes that package (nc: nmap-ncat), an
        # installed one of the persona is there; with nothing left yum has nothing to do, an error
        # only when no name was found at all
        lists, installed = tpot_yum_lists(), tpot_yum_installed()
        self.tpot_notes, wanted, found_any = [], {}, False
        for word in self.tpot_words[1:]:
            name = re.sub(r"\\.(x86_64|noarch|i686)$", "", word)
            if lists is not None and name not in lists[0] and name in lists[1]:
                name = lists[1][name]
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+._-]{0,99}", name) or (
                lists is not None and name not in lists[0]
            ):
                self.tpot_notes.append(f"No package {tpot_yum_bold(self, word)} available.\\n")
                if lists is not None and word.lower() in lists[0] and word.lower() != word:
                    self.tpot_notes.append(f"  * Maybe you meant: {word.lower()}\\n")
                continue
            found_any = True
            if name in installed:
                self.tpot_notes.append(tpot_yum_present(name, installed[name]))
            else:
                wanted[name] = None
        if not wanted:
            yield self.sleep(1)
            self.write(f"Loaded plugins: {tpot_yum_plugins()}\\n")
            if "fastestmirror" in tpot_yum_plugins():
                self.write("Loading mirror speeds from cached hostfile\\n")
            for note in self.tpot_notes:
                self.write(note)
            if found_any:
                self.write("Nothing to do\\n")
                self.exit()
            else:
                self.errorWrite("Error: Nothing to do\\n")
                self.exit(1)
            return
        for y in wanted:
""",
    )
    replace_once(
        path,
        """        self.write(
            f"{random.randint(200, 300)} packages excluded due to repository priority protections\\n"
        )
""",
        """        if "fastestmirror" in tpot_yum_plugins():
            self.write("Loading mirror speeds from cached hostfile\\n")
        if "priorities" in tpot_yum_plugins():
            self.write(
                f"{random.randint(200, 300)} packages excluded due to repository priority protections\\n"
            )
        for note in self.tpot_notes:
            self.write(note)
""",
    )
    replace_once(
        path,
        """        self.write("Beginning Kernel Module Plugin\\n")
        self.write("Finished Kernel Module Plugin\\n\\n")
""",
        """        if "kernel-module" in tpot_yum_plugins():
            self.write("Beginning Kernel Module Plugin\\n")
            self.write("Finished Kernel Module Plugin\\n\\n")
""",
    )
    replace_once(
        path,
        """        self.write("Is this ok [y/d/N]: ")
        # Assume 'yes'
""",
        """        if self.tpot_assume_yes:
            yield self.lineReceived("y")
            return
        self.write("Is this ok [y/d/N]: ")
        # Assume 'yes'
""",
    )
    # the version and release of a package and its lines as yum 3 writes them (name.arch epoch:version-release)
    replace_once(
        path,
        """                "version": f"{random.choice([0, 1])}.{random.randint(1, 40)}-{random.randint(1, 10)}",
""",
        """                "version": f"{random.choice([0, 1])}.{random.randint(1, 40)}.{random.randint(0, 9)}",
""",
    )
    replace_once(
        path,
        """                f"---> Package {p}.{self.packages[p]['version']} {arch}.{self.packages[p]['release']} will be installed\\n"
""",
        """                f"---> Package {p}.{arch} 0:{self.packages[p]['version']}-{self.packages[p]['release']} will be installed\\n"
""",
    )
    replace_once(
        path,
        """                f"  {p}.{arch} {random.randint(0, 2)}:{self.packages[p]['version']}-{self.packages[p]['release']} \\t\\t"
""",
        """                f"  {p}.{arch} 0:{self.packages[p]['version']}-{self.packages[p]['release']} \\t\\t"
""",
    )


# module code of the patched apt.py after its commands: opkg of OpenWrt
OPKG_COMMAND = r'''

# T-Pot: opkg of OpenWrt ([shell] opkg_release, opkg_target, opkg_arch, opkg_installed, opkg_help and
# package_names "name feed"). The lists of the feeds are empty after a boot: install knows the
# installed packages only until `opkg update` of the session fetched the lists. Nothing is
# downloaded or written; what the session fetched and installed ends with it.
TPOT_OPKG_FEEDS = ("core", "base", "luci", "packages", "routing", "telephony")
# options of opkg that take a value
TPOT_OPKG_VALUES = frozenset({"-d", "-f", "-o", "--dest", "--conf", "--offline-root", "--add-dest", "--add-arch",
                              "--cache", "--lists-dir", "--verify-program"})
# sub-commands that change or show nothing here
TPOT_OPKG_QUIET = frozenset({"upgrade", "configure", "flag", "list-upgradable", "info", "status", "download",
                             "files", "search", "find", "compare-versions", "depends", "whatdepends",
                             "whatdependsrec", "whatrecommends", "whatsuggests", "whatprovides", "whatconflicts",
                             "whatreplaces", "list-changed-conffiles"})


def tpot_opkg(key: str, fallback: str = "") -> str:
    return CowrieConfig.get("shell", key, fallback=fallback)


@functools.cache
def tpot_opkg_names() -> dict[str, str]:
    """T-Pot: {name: feed} of the release ([shell] package_names)."""
    path = tpot_opkg("package_names")
    try:
        rows = [line.split() for line in Path(path).read_text(encoding="utf-8").splitlines()] if path else []
    except (OSError, ValueError):
        rows = []
    return {row[0]: row[1] for row in rows if len(row) == 2}


def tpot_opkg_installed() -> dict[str, str]:
    """T-Pot: the installed packages of the persona ([shell] opkg_installed "name version,...")."""
    return dict(entry.split(" ", 1) for entry in tpot_opkg("opkg_installed").split(",") if " " in entry)


def tpot_opkg_url(feed: str) -> str:
    base = f"https://downloads.openwrt.org/releases/{tpot_opkg('opkg_release')}"
    if feed == "core":
        return f"{base}/targets/{tpot_opkg('opkg_target')}/packages"
    return f"{base}/packages/{tpot_opkg('opkg_arch')}/{feed}"


def tpot_opkg_version(name: str) -> str:
    """T-Pot: the version of a package of the lists, the same for a name on every call."""
    d = hashlib.sha256(f"{tpot_opkg('opkg_release')} {name}".encode()).digest()
    return f"{1 + d[0] % 9}.{d[1] % 20}" + (f".{d[2] % 10}" if d[3] % 2 else "") + f"-{1 + d[4] % 4}"


class Command_opkg(HoneyPotCommand):
    """T-Pot: opkg of OpenWrt."""

    sleep = Command_aptget.sleep

    def tpot_usage(self, first: str) -> None:
        try:
            text = Path(tpot_opkg("opkg_help")).read_text(encoding="utf-8")
        except (OSError, ValueError):
            text = "opkg must have one sub-command argument\n"
        self.errorWrite(first)
        self.write(text.split("\n", 1)[-1])
        self.exit(1)

    @inlineCallbacks
    def start(self):
        # options are no sub-command and no package, nor the values of those that take one
        words, items = [], list(self.args)
        while items:
            arg = items.pop(0)
            if arg in TPOT_OPKG_VALUES:
                if items:
                    items.pop(0)
            elif not arg.startswith("-"):
                words.append(arg)
        added = getattr(self.protocol, "tpot_opkg_added", None)
        if added is None:
            added = {}
            self.protocol.tpot_opkg_added = added
        installed = {**tpot_opkg_installed(), **added}
        if not words:
            self.tpot_usage("opkg must have one sub-command argument\n")
        elif words[0] == "update":
            for feed in TPOT_OPKG_FEEDS:
                url = tpot_opkg_url(feed)
                self.write(f"Downloading {url}/Packages.gz\n")
                yield self.sleep(0.3, 0.8)
                self.write(f"Updated list of available packages in /var/opkg-lists/openwrt_{feed}\n"
                           f"Downloading {url}/Packages.sig\nSignature check passed.\n")
            self.protocol.tpot_opkg_lists = True
            self.exit()
        elif words[0] == "install" and len(words) == 1:
            self.tpot_usage("opkg: the ``install'' command requires at least one argument\n")
        elif words[0] == "install":
            names = tpot_opkg_names() if getattr(self.protocol, "tpot_opkg_lists", False) else {}
            arch, errors, new = tpot_opkg("opkg_arch"), {}, {}
            for name in words[1:]:
                if name in installed:
                    self.write(f"Package {name} ({installed[name]}) installed in root is up to date.\n")
                elif name in names and name not in new:
                    version = tpot_opkg_version(name)
                    self.write(f"Installing {name} ({version}) to root...\n"
                               f"Downloading {tpot_opkg_url(names[name])}/{name}_{version}_{arch}.ipk\n")
                    yield self.sleep(0.3, 0.8)
                    new[name] = None
                elif name not in new and name not in errors:
                    self.write(f"Unknown package '{name}'.\n")
                    errors[name] = None
            for name in new:
                added[name] = tpot_opkg_version(name)
                self.write(f"Configuring {name}.\n")
            if errors:
                self.errorWrite("Collected errors:\n" + "".join(
                    f" * opkg_install_cmd: Cannot install package {name}.\n" for name in errors))
                self.exit(255)
            else:
                self.exit()
        elif words[0] in ("list", "list-installed"):
            self.write("".join(f"{name} - {version}\n" for name, version in sorted(installed.items())))
            self.exit()
        elif words[0] == "remove":
            gone = [name for name in dict.fromkeys(words[1:]) if name in added]
            for name in gone:
                del added[name]
                self.write(f"Removing package {name} from root...\n")
            if not gone:
                self.write("No packages removed.\n")
            self.exit()
        elif words[0] == "print-architecture":
            self.write(f"arch all 1\narch noarch 1\narch {tpot_opkg('opkg_arch')} 10\n")
            self.exit()
        elif words[0] in TPOT_OPKG_QUIET:
            self.exit()
        else:
            self.tpot_usage(f"opkg: unknown sub-command {words[0]}\n")


commands["/bin/opkg"] = Command_opkg
commands["opkg"] = Command_opkg
'''


# module code of the patched yum.py after Command_yum: dnf 4 and rpm of RHEL 9
DNF_COMMAND = r'''

# T-Pot: dnf 4 of RHEL 9 (also its yum) and rpm. The packages, their versions and the repositories
# are those of [shell] dnf_packages (a JSON file of the runtime folder). Nothing is downloaded or
# installed: a package the session installs is a name of the session, its programs answer as
# those of Cowrie's apt do. Without the key yum stays yum 3 (the persona skips dnf and rpm).
TPOT_DNF_WIDTH = 80
TPOT_DNF_VALUE_OPTIONS = frozenset({
    "-c", "--config", "-d", "--debuglevel", "-e", "--errorlevel", "--installroot", "--releasever", "--setopt",
    "-x", "--exclude", "--enablerepo", "--disablerepo", "--repo", "--repoid", "--rpmverbosity", "--color",
    "--destdir", "--downloaddir", "--forcearch", "--comment", "--advisory", "--advisories", "--bz", "--bzs",
    "--cve", "--cves", "--sec-severity", "--secseverity", "--enableplugin", "--disableplugin", "-R",
    "--randomwait"})
TPOT_DNF_ALIASES = {"in": "install", "rei": "reinstall", "rm": "remove", "erase": "remove", "up": "upgrade",
                    "update": "upgrade", "check-upgrade": "check-update", "ls": "list", "se": "search",
                    "mc": "makecache", "repoinfo": "repolist", "upgrade-minimal": "upgrade",
                    "update-minimal": "upgrade", "up-min": "upgrade", "dsync": "distro-sync",
                    "distribution-synchronization": "distro-sync", "dg": "downgrade", "autoremove": "autoremove"}
# commands of dnf 4 and its plugins this emulation answers with the metadata only (and root ones: nothing to do)
TPOT_DNF_ROOT_ONLY = frozenset({"autoremove", "distro-sync", "downgrade", "swap"})
TPOT_DNF_ROOT = frozenset({"install", "reinstall", "remove", "upgrade"}) | TPOT_DNF_ROOT_ONLY
TPOT_DNF_OTHER = frozenset({
    "alias", "check", "deplist", "group", "grp", "groups", "grouplist", "groupinstall", "history", "hist", "info",
    "if", "mark", "module", "provides", "whatprovides", "prov", "wp", "repoquery", "rq", "repository-packages",
    "repo-pkgs", "shell", "sh", "updateinfo", "upif", "builddep", "changelog", "config-manager", "copr",
    "debug-dump", "debug-restore", "debuginfo-install", "download", "needs-restarting", "repoclosure", "repodiff",
    "repograph", "repomanage", "reposync", "groups-manager"})
TPOT_DNF_COMMANDS = frozenset({"install", "reinstall", "remove", "upgrade", "check-update", "list", "search",
                               "repolist", "makecache", "clean", "help"}) | TPOT_DNF_ROOT_ONLY | TPOT_DNF_OTHER
TPOT_DNF_HELP = """usage: {prog} [options] COMMAND

List of Main Commands:

alias                     List or create command aliases
autoremove                remove all unneeded packages that were originally installed as dependencies
check                     check for problems in the packagedb
check-update              check for available package upgrades
clean                     remove cached data
deplist                   [deprecated, use repoquery --deplist] List package's dependencies and what packages provide them
distro-sync               synchronize installed packages to the latest available versions
downgrade                 Downgrade a package
group                     display, or use, the groups information
help                      display a helpful usage message
history                   display, or use, the transaction history
info                      display details about a package or group of packages
install                   install a package or packages on your system
list                      list a package or groups of packages
makecache                 generate the metadata cache
mark                      mark or unmark installed packages as installed by user.
module                    Interact with Modules.
provides                  find what package provides the given value
reinstall                 reinstall a package
remove                    remove a package or packages from your system
repolist                  display the configured software repositories
repoquery                 search for packages matching keyword
repository-packages       run commands on top of all packages in given repository
search                    search package details for the given string
shell                     run an interactive {upper} shell
swap                      run an interactive {upper} mod for remove and install one spec
updateinfo                display advisories about packages
upgrade                   upgrade a package or packages on your system
upgrade-minimal           upgrade, but only 'newest' package match which fixes a problem that affects your system

List of Plugin Commands:

builddep                  Install build dependencies for package or spec file
changelog                 Show changelog data of packages
config-manager            manage {prog} configuration options and repositories
copr                      Interact with Copr repositories.
debug-dump                dump information about installed rpm packages to file
debug-restore             restore packages recorded in debug-dump file
debuginfo-install         install debuginfo packages
download                  Download package to current directory
needs-restarting          determine updated binaries that need restarting
repoclosure               Display a list of unresolved dependencies for repositories
repodiff                  List differences between two sets of repositories
repograph                 Output a full package dependency graph in dot format
repomanage                Manage a directory of rpm packages
reposync                  download all packages from remote repo

Optional arguments:
  -c [config file], --config [config file]
                        config file location
  -q, --quiet           quiet operation
  -v, --verbose         verbose operation
  --version             show {upper} version and exit
  --installroot [path]  set install root
  --nodocs              do not install documentations
  --noplugins           disable all plugins
  --enableplugin [plugin]
                        enable plugins by name
  --disableplugin [plugin]
                        disable plugins by name
  --releasever RELEASEVER
                        override the value of $releasever in config and repo
                        files
  --setopt SETOPTS      set arbitrary config and repo options
  --skip-broken         resolve depsolve problems by skipping packages
  -h, --help, --help-cmd
                        show command help
  --allowerasing        allow erasing of installed packages to resolve
                        dependencies
  -b, --best            try the best available package versions in
                        transactions.
  --nobest              do not limit the transaction to the best candidate
  -C, --cacheonly       run entirely from system cache, don't update cache
  -R [minutes], --randomwait [minutes]
                        maximum command wait time
  -d [debug level], --debuglevel [debug level]
                        debugging output level
  -y, --assumeyes       automatically answer yes for all questions
  --assumeno            automatically answer no for all questions
  --enablerepo [repo]   Temporarily enable repositories for the purpose of the
                        current dnf command. Accepts an id, a comma-separated
                        list of ids, or a glob of ids. This option can be
                        specified multiple times.
  --disablerepo [repo]  Temporarily disable active repositories for the
                        purpose of the current dnf command. Accepts an id, a
                        comma-separated list of ids, or a glob of ids. This
                        option can be specified multiple times, but is
                        mutually exclusive with `--repo`.
  -x [package], --exclude [package], --excludepkgs [package]
                        exclude packages by name or glob
  --nogpgcheck          disable gpg signature checking (if RPM policy allows)
"""
TPOT_DNF_SUPERUSER = "Error: This command has to be run with superuser privileges (under the root user on most systems).\n"


@functools.cache
def tpot_dnf_data() -> dict:
    path = CowrieConfig.get("shell", "dnf_packages", fallback="")
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8")) if path else {}
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    for part in ("installed", "available", "provides", "owners"):
        if not isinstance(data.get(part), dict):
            data[part] = {}
    for part in ("repos", "answers"):
        if not isinstance(data.get(part), list):
            data[part] = []
    return data


def tpot_dnf_session(protocol) -> dict:
    """What this session installed and removed (on the protocol: one per session)."""
    session = getattr(protocol, "tpot_dnf_session", None)
    if session is None:
        session = {"installed": {}, "removed": set()}
        try:
            protocol.tpot_dnf_session = session
        except AttributeError:
            pass
    return session


def tpot_dnf_mark(protocol, name: str, evr: str) -> None:
    """A program this session installed answers although the persona skips it ({name: version})."""
    installed = getattr(protocol, "tpot_installed", None)
    if installed is None:
        installed = {}
        try:
            protocol.tpot_installed = installed
        except AttributeError:
            return
    plain = evr.split(":", 1)[-1]
    installed[name] = plain.rpartition("-")[0] or plain


def tpot_dnf_installed(protocol) -> dict:
    session = tpot_dnf_session(protocol)
    found = {name: entry for name, entry in tpot_dnf_data()["installed"].items() if name not in session["removed"]}
    found.update(session["installed"])
    return found


def tpot_dnf_available(protocol) -> dict:
    installed = tpot_dnf_installed(protocol)
    return {name: entry for name, entry in tpot_dnf_data()["available"].items() if name not in installed}


def tpot_dnf_repo(entry: dict) -> str:
    """The repository of an installed package: where dnf installed it from, else the first enabled one."""
    repo = entry.get("from", "@System").lstrip("@")
    if repo != "System":
        return repo
    return next((r["id"] for r in tpot_dnf_data()["repos"] if r.get("enabled")), "System")


def tpot_dnf_tty(cmd) -> bool:
    """stdout of the command is the terminal of an interactive session (dnf marks only there)."""
    targets = getattr(cmd.pp, "targets", None) or {}
    return (targets.get(1) or ("",))[0] == "terminal" and not hasattr(cmd.protocol, "execcmd")


def tpot_dnf_privileged(cmd) -> bool:
    return cmd.user["uid"] == 0 or getattr(cmd.pp, "tpot_sudo", False)


def tpot_dnf_nevra(name: str, entry: dict) -> str:
    return f"{name}-{entry['evr']}.{entry['arch']}"


def tpot_rpm_nvra(name: str, entry: dict) -> str:
    return f"{name}-{entry['evr'].split(':', 1)[-1]}.{entry['arch']}"


def tpot_dnf_number(number: float) -> str:
    """format_number of dnf: 1024 steps, one decimal below 9.95."""
    symbols = (" ", "k", "M", "G", "T")
    depth = 0
    while number > 999 and depth < len(symbols) - 1:
        depth += 1
        number = number / 1024
    if isinstance(number, int):
        return f"{number} {symbols[depth]}"
    if number < 9.95:
        return f"{number:.1f} {symbols[depth]}"
    return f"{number:.0f} {symbols[depth]}"


def tpot_dnf_time(ts: float, zone: str = "UTC") -> str:
    """%c of en_US: Tue 25 Aug 2026 01:23:45 PM UTC."""
    return time.strftime("%a %d %b %Y %I:%M:%S %p ", time.gmtime(ts)) + zone


def tpot_dnf_spaces_help(current: int, data_tups: list, left: int) -> int:
    ret = 0
    for length, count in data_tups:
        if left < (length - current):
            break
        if current >= length:
            continue
        ret += count
    return ret


def tpot_dnf_columns(data: list, columns: list | None = None, remainder_column: int = 0,
                     total_width: int = TPOT_DNF_WIDTH, indent: str = "") -> list:
    """calcColumns of dnf: the widths of the columns of data ({field length: count} per column)."""
    cols = len(data)
    data = [sorted(d.items()) for d in data]
    if columns is None:
        columns = [1] * (cols - 1) + [0]
    columns = list(columns)
    total_width -= sum(columns) + (cols - 1) + len(indent)
    if not columns[-1]:
        total_width += 1
    while total_width > 0:
        helps, val = 0, 0
        for d in range(cols):
            thelps = tpot_dnf_spaces_help(columns[d], data[d], total_width)
            if not thelps:
                continue
            if helps and d == cols - 1 and (thelps / 2) < helps:
                continue
            if thelps < helps:
                continue
            helps, val = thelps, d
        if helps:
            diff = data[val].pop(0)[0] - columns[val]
            if not columns[val] and val == cols - 1:
                total_width -= 1
            columns[val] += diff
            total_width -= diff
            continue
        overflowed = sum(1 for d in range(cols) if data[d])
        if overflowed:
            norm = total_width // overflowed
            for d in range(cols):
                if data[d]:
                    columns[d] += norm
                    total_width -= norm
        cols -= 1
        norm = total_width // cols
        for d in range(cols):
            columns[d] += norm
        columns[remainder_column] += total_width - cols * norm
        total_width = 0
    return columns


def tpot_dnf_fmt(columns, msg: str = "", end: str = "") -> str:
    """fmtColumns of dnf: (value, width) with negative widths left aligned; a value too wide for its
    column goes on, the next column starts on a new line."""
    columns = list(columns)
    total_width = len(msg)
    parts = [msg]
    for val, width in columns[:-1]:
        left, width = width < 0, abs(width)
        if len(val) <= width:
            pad = " " * (width - len(val))
            parts.append((val + pad if left else pad + val) + " ")
        else:
            parts.append(val + "\n" + " " * (total_width + width + 1))
        total_width += width + 1
    val, width = columns[-1]
    left, width = width < 0, abs(width)
    pad = " " * max(0, width - len(val))
    parts.append((val + pad if left else pad + val) + end)
    return "".join(parts)


def tpot_dnf_lengths(values) -> dict:
    found: dict[int, int] = {}
    for value in values:
        found[len(value)] = found.get(len(value), 0) + 1
    return found


def tpot_dnf_short_long(width: int, short: str, long: str) -> str:
    return long if len(long) <= width else short


def tpot_dnf_pto(action: str, names: list) -> list:
    """_pto_callback of dnf: the packages after the transaction in as many columns as fit."""
    def fits(msgs, num):
        if len(msgs) < num:
            return []
        left = TPOT_DNF_WIDTH - ((num - 1) + 2)
        if left <= 0:
            return []
        col_lens = [0] * num
        col = 0
        for msg in msgs:
            if len(msg) > col_lens[col]:
                diff = len(msg) - col_lens[col]
                if left <= diff:
                    return []
                left -= diff
                col_lens[col] = len(msg)
            col = (col + 1) % num
        return [-(length + left // num) for length in col_lens]

    out = [f"{action}:"]
    cols: list = []
    for num in (8, 7, 6, 5, 4, 3, 2):
        cols = fits(names, num)
        if cols:
            break
    if not cols:
        cols = [-(TPOT_DNF_WIDTH - 2)]
    while names:
        out.append("  " + tpot_dnf_fmt(zip(names[:len(cols)], cols)))
        names = names[len(cols):]
    return out


def tpot_dnf_section(name: str) -> str:
    """fmtSection of dnf: the name centred in a line of = ."""
    cols = TPOT_DNF_WIDTH - 2
    if len(name) >= cols - 4:
        beg = end = "=="
    else:
        beg = "=" * ((cols - len(name)) // 2)
        end = "=" * (cols - len(name) - len(beg))
    return f"{beg} {name} {end}"


def tpot_dnf_digest(name: str) -> bytes:
    return hashlib.sha256(f"tpot-dnf {name}".encode()).digest()


def tpot_dnf_speed(name: str, size: int) -> float:
    """Bytes per second of a download: small packages are slower."""
    d = tpot_dnf_digest(name)
    top = 4_000_000 + int.from_bytes(d[:3], "big") % 20_000_000
    return max(150_000.0, min(float(top), size * (2 + d[3] % 6)))


def tpot_dnf_clock(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


class Command_dnf(HoneyPotCommand):
    """dnf 4 of the persona: install, reinstall, remove, upgrade, check-update, list, search,
    repolist, makecache, clean, --version and the help; -y / --assumeno and the [y/N] question."""

    tpot_prog = "dnf"
    # the question reads stdin: a pipe in front of dnf feeds its answers
    consumes_stdin = True
    _log = Logger()

    def sleep(self, time: float, time2: float | None = None) -> defer.Deferred:
        d: defer.Deferred = defer.Deferred()
        if time2:
            time = random.randint(int(time * 100), int(time2 * 100)) / 100.0
        reactor.callLater(time, d.callback, None)
        return d

    def tpot_out(self, text: str) -> None:
        if not self.tpot_quiet:
            self.write(text)

    def tpot_bold(self, text: str) -> str:
        return f"\x1b[1m{text}\x1b(B\x1b[m" if text and tpot_dnf_tty(self) else text

    def tpot_parse(self) -> list:
        words: list[str] = []
        self.tpot_opts: set[str] = set()
        items = list(self.args)
        while items:
            arg = items.pop(0)
            if arg == "--":
                words += items
                break
            if arg.startswith("--"):
                name = arg.split("=", 1)[0]
                if name in TPOT_DNF_VALUE_OPTIONS and "=" not in arg and items:
                    items.pop(0)
                self.tpot_opts.add(name)
            elif arg.startswith("-") and len(arg) > 1:
                for index, char in enumerate(arg[1:]):
                    if "-" + char in TPOT_DNF_VALUE_OPTIONS:
                        if index == len(arg) - 2 and items:
                            items.pop(0)
                        break
                    self.tpot_opts.add("-" + char)
            else:
                words.append(arg)
        return words

    @inlineCallbacks
    def start(self):
        try:
            yield self.tpot_start()
        except Exception:  # noqa: BLE001 - the command always exits
            self.exit(1)

    @inlineCallbacks
    def tpot_start(self):
        words = self.tpot_parse()
        opts = self.tpot_opts
        self.tpot_quiet = bool(opts & {"-q", "--quiet"})
        self.tpot_yes = bool(opts & {"-y", "--assumeyes"})
        self.tpot_no = "--assumeno" in opts
        self.tpot_pending = None
        if "--version" in opts:
            self.tpot_version()
            self.exit(0)
            return
        if opts & {"-h", "--help", "--help-cmd"} or words[:1] == ["help"]:
            self.write(TPOT_DNF_HELP.format(prog=self.tpot_prog, upper=self.tpot_prog.upper()))
            self.exit(0)
            return
        if not words:
            self.write(TPOT_DNF_HELP.format(prog=self.tpot_prog, upper=self.tpot_prog.upper()))
            self.exit(1)
            return
        command = TPOT_DNF_ALIASES.get(words[0], words[0])
        names = words[1:]
        if command not in TPOT_DNF_COMMANDS:
            self.errorWrite(f"No such command: {words[0]}. Please use /usr/bin/{self.tpot_prog} --help\n"
                            f"It could be a {self.tpot_prog.upper()} plugin command, try: "
                            f"\"{self.tpot_prog} install 'dnf-command({words[0]})'\"\n")
            self.exit(1)
            return
        required = {"install": "PACKAGE", "reinstall": "PACKAGE", "remove": "PACKAGE", "search": "KEYWORD",
                    "clean": "type"}
        if command in required and not names:
            self.errorWrite(f"usage: {self.tpot_prog} {command} [-c [config file]] [-q] [-v] [--version] ... "
                            f"{required[command]} [{required[command]} ...]\n"
                            f"{self.tpot_prog} {command}: error: the following arguments are required: "
                            f"{required[command]}\n")
            self.exit(2)
            return
        root = tpot_dnf_privileged(self)
        self.tpot_out("Updating Subscription Management repositories.\n" if root
                      else "Not root, Subscription Management repositories not updated\n")
        if command in TPOT_DNF_ROOT and not root:
            self.errorWrite(TPOT_DNF_SUPERUSER)
            self.exit(1)
            return
        yield self.sleep(0.4, 1.2)
        if command == "install":
            yield self.tpot_install(names)
        elif command == "reinstall":
            yield self.tpot_install(names, reinstall=True)
        elif command == "remove":
            yield self.tpot_remove(names)
        elif command == "upgrade":
            self.tpot_upgrade(names)
        elif command == "list":
            self.tpot_list(names)
        elif command == "search":
            self.tpot_search(names)
        elif command == "repolist":
            self.tpot_repolist(names)
        elif command == "clean":
            self.tpot_out(f"{21 if root else 0} files removed\n")
            self.exit(0)
        elif command == "makecache":
            self.tpot_metadata()
            self.tpot_out("Metadata cache created.\n")
            self.exit(0)
        elif command in TPOT_DNF_ROOT_ONLY:
            self.tpot_metadata()
            self.tpot_out("Dependencies resolved.\nNothing to do.\nComplete!\n")
            self.exit(0)
        else:
            # check-update (the system is up to date) and the commands without emulation
            self.tpot_metadata()
            self.exit(0)

    def tpot_version(self) -> None:
        data = tpot_dnf_data()
        self.write(f"{data.get('version', '4.14.0')}\n")
        blocks = []
        for name in ("dnf", "rpm"):
            info = data.get(name)
            if not isinstance(info, dict):
                continue
            package, _, version = info.get("nevra", "").partition("-")
            blocks.append(f"  Installed: {self.tpot_bold(package)}-{version} at "
                          f"{tpot_dnf_time(info.get('installed', 0), 'GMT')}\n"
                          f"  Built    : {data.get('packager', '')} at {tpot_dnf_time(info.get('built', 0), 'GMT')}\n")
        self.write("\n".join(blocks))

    def tpot_metadata(self) -> None:
        """The last refresh of the metadata: dnf-makecache.timer 10 minutes after the boot, then when
        it expired (a day)."""
        boot, now = self.protocol.boot_time(), time.time()
        first = boot + 600
        last = first + int((now - first) // 86400) * 86400 if now > first else boot
        self.tpot_out(f"Last metadata expiration check: {datetime.timedelta(seconds=int(now - last))} ago on "
                      f"{tpot_dnf_time(last)}.\n")

    def tpot_find(self, arg: str):
        """(kind, name, entry) of a package argument: installed / available, None without a match."""
        name = arg
        for arch in (".x86_64", ".noarch"):
            if name.endswith(arch):
                name = name[: -len(arch)]
        name = tpot_dnf_data()["provides"].get(name, name)
        installed, available = tpot_dnf_installed(self.protocol), tpot_dnf_available(self.protocol)
        if name in installed:
            return "installed", name, installed[name]
        if name in available:
            return "available", name, available[name]
        return None

    @inlineCallbacks
    def tpot_install(self, names: list, reinstall: bool = False):
        self.tpot_metadata()
        missing, wanted, again = [], {}, {}
        for arg in names:
            found = self.tpot_find(arg)
            if found is None:
                missing.append(arg)
                self.tpot_out(f"No match for argument: {self.tpot_bold(arg)}\n")
            elif found[0] == "installed" and not reinstall:
                self.tpot_out(f"Package {tpot_dnf_nevra(found[1], found[2])} is already installed.\n")
            elif found[0] == "installed":
                again[found[1]] = dict(found[2], repo=tpot_dnf_repo(found[2]),
                                       download=found[2].get("size", 0) // 3)
            elif reinstall:
                self.tpot_out(f"Package {arg} available, but not installed.\n"
                              f"No match for argument: {self.tpot_bold(arg)}\n")
                missing.append(arg)
            else:
                wanted[found[1]] = found[2]
        if missing:
            self.errorWrite("Error: No packages marked for reinstall.\n" if reinstall
                            else f"Error: Unable to find a match: {' '.join(missing)}\n")
            self.exit(1)
            return
        if not wanted and not again:
            self.tpot_out("Dependencies resolved.\nNothing to do.\nComplete!\n")
            self.exit(0)
            return
        available = tpot_dnf_available(self.protocol)
        deps: dict = {}
        todo = [dep for entry in wanted.values() for dep in entry.get("requires", [])]
        while todo:
            dep = todo.pop(0)
            if dep in available and dep not in wanted and dep not in deps:
                deps[dep] = available[dep]
                todo += available[dep].get("requires", [])
        sections = [("Reinstalling", sorted(again.items()))] if again else []
        sections += [("Installing", sorted(wanted.items())), ("Installing dependencies", sorted(deps.items()))]
        self.tpot_pending = ("install" if wanted else "reinstall", sections)
        self.tpot_table(sections, "download")
        counts = [("Install", len(wanted) + len(deps)), ("Reinstall", len(again))]
        self.tpot_summary(counts)
        packages = [entry for _, rows in sections for _, entry in rows]
        self.tpot_out(f"Total download size: {tpot_dnf_number(sum(e.get('download', e.get('size', 0)) for e in packages))}\n")
        if wanted or deps:
            self.tpot_out(f"Installed size: {tpot_dnf_number(sum(e.get('size', 0) for _, e in list(wanted.items()) + list(deps.items())))}\n")
        yield self.tpot_ask()

    @inlineCallbacks
    def tpot_remove(self, names: list):
        installed = tpot_dnf_installed(self.protocol)
        wanted = {}
        for arg in names:
            found = self.tpot_find(arg)
            if found is None or found[0] != "installed":
                self.tpot_out(f"No match for argument: {self.tpot_bold(arg)}\n")
            else:
                wanted[found[1]] = dict(installed[found[1]], repo=installed[found[1]].get("from", "@System"))
        if not wanted:
            self.errorWrite("No packages marked for removal.\n")
            self.tpot_out("Dependencies resolved.\nNothing to do.\nComplete!\n")
            self.exit(0)
            return
        # /etc/dnf/protected.d of RHEL 9
        protected = sorted(name for name in wanted if name in ("dnf", "yum", "systemd", "sudo"))
        if protected:
            self.errorWrite("Error: \n Problem: The operation would result in removing the following protected "
                            f"packages: {', '.join(protected)}\n")
            self.exit(1)
            return
        sections = [("Removing", sorted(wanted.items()))]
        self.tpot_pending = ("remove", sections)
        self.tpot_table(sections, "size")
        self.tpot_summary([("Remove", len(wanted))])
        self.tpot_out(f"Freed space: {tpot_dnf_number(sum(e.get('size', 0) for e in wanted.values()))}\n")
        yield self.tpot_ask()

    def tpot_upgrade(self, names: list) -> None:
        self.tpot_metadata()
        missing = []
        for arg in names:
            found = self.tpot_find(arg)
            if found is None or found[0] != "installed":
                if found is not None:
                    self.tpot_out(f"Package {found[1]} available, but not installed.\n")
                self.tpot_out(f"No match for argument: {self.tpot_bold(arg)}\n")
                missing.append(arg)
        if missing:
            self.errorWrite("Error: No packages marked for upgrade.\n")
            self.exit(1)
            return
        self.tpot_out("Dependencies resolved.\nNothing to do.\nComplete!\n")
        self.exit(0)

    def tpot_table(self, sections: list, size_key: str) -> None:
        rows = [(name, entry) for _, part in sections for name, entry in part]
        a_wid = max(len(entry["arch"]) for _, entry in rows)
        columns = tpot_dnf_columns([tpot_dnf_lengths(name for name, _ in rows), {},
                                    tpot_dnf_lengths(entry["evr"] for _, entry in rows),
                                    tpot_dnf_lengths(entry["repo"] for _, entry in rows), {}],
                                   [1, a_wid, 1, 1, 5], remainder_column=2, indent="  ")
        n_wid, a_wid, v_wid, r_wid, s_wid = columns
        header = tpot_dnf_fmt(((tpot_dnf_short_long(n_wid, "Package", "Package"), -n_wid),
                               (tpot_dnf_short_long(a_wid, "Arch", "Architecture"), -a_wid),
                               (tpot_dnf_short_long(v_wid, "Version", "Version"), -v_wid),
                               (tpot_dnf_short_long(r_wid, "Repo", "Repository"), -r_wid),
                               (tpot_dnf_short_long(s_wid, "Size", "Size"), s_wid)), " ")
        lines = ["Dependencies resolved.\n", "=" * TPOT_DNF_WIDTH + "\n", header + "\n", "=" * TPOT_DNF_WIDTH + "\n"]
        for title, part in sections:
            if not part:
                continue
            lines.append(f"{title}:\n")
            for name, entry in part:
                lines.append(tpot_dnf_fmt(((name, -n_wid), (entry["arch"], -a_wid), (entry["evr"], -v_wid),
                                           (entry["repo"], -r_wid),
                                           (tpot_dnf_number(entry.get(size_key, entry.get("size", 0))), s_wid)),
                                          " ", "\n"))
        self.tpot_out("".join(lines))

    def tpot_summary(self, counts: list) -> None:
        counts = [(action, count) for action, count in counts if count]
        width = max(len(action) for action, _ in counts)
        digits = max(len(str(count)) for _, count in counts)
        self.tpot_out("\nTransaction Summary\n" + "=" * TPOT_DNF_WIDTH + "\n"
                      + "".join(f"{action:<{width}}  {count:>{digits}} {'Package' if count == 1 else 'Packages'}\n"
                                for action, count in counts) + "\n")

    @inlineCallbacks
    def tpot_ask(self):
        if self.tpot_no:
            self.errorWrite("Operation aborted.\n")
            self.exit(1)
            return
        if self.tpot_yes:
            yield self.tpot_run()
            return
        self.write("Is this ok [y/N]: ")
        if self.input_data is not None:
            # piped stdin: its lines answer in turn, its end is EOF (no)
            data, self.input_data = self.input_data, None
            lines = data.decode("utf-8", "replace").splitlines()
            while self.tpot_pending is not None:
                yield self.tpot_answer(lines.pop(0) if lines else None)

    @inlineCallbacks
    def tpot_answer(self, line):
        answer = None if line is None else line.strip().lower()
        if answer in ("y", "yes"):
            yield self.tpot_run()
        elif answer is None or answer in ("", "n", "no"):
            self.tpot_pending = None
            self.errorWrite("Operation aborted.\n")
            self.exit(1)
        else:
            self.write("Is this ok [y/N]: ")

    def lineReceived(self, line: str) -> None:
        self._log.info("INPUT (dnf): {line}", line=line)
        if self.tpot_pending is None:
            return
        self.tpot_answer(line)

    def eofReceived(self) -> None:
        if self.tpot_pending is not None:
            self.tpot_answer(None)

    @inlineCallbacks
    def tpot_run(self):
        try:
            yield self.tpot_transaction()
        except Exception:  # noqa: BLE001 - the command always exits
            self.exit(1)

    @inlineCallbacks
    def tpot_transaction(self):
        kind, sections = self.tpot_pending
        self.tpot_pending = None
        rows = [(title, name, entry) for title, part in sections for name, entry in part]
        if kind != "remove":
            self.tpot_out("Downloading Packages:\n")
            total, seconds = 0, 0.0
            for number, (_, name, entry) in enumerate(rows, 1):
                size = entry.get("download", entry.get("size", 0))
                speed = tpot_dnf_speed(name, size)
                took = size / speed
                yield self.sleep(min(2.0, max(0.2, took)))
                text = f"{name}-{entry['evr'].split(':', 1)[-1]}.{entry['arch']}.rpm"
                if len(rows) > 1:
                    text = f"({number:>{len(str(len(rows)))}}/{len(rows)}): {text}"
                self.tpot_out(f"{text[:48]:<48} {tpot_dnf_number(speed):>5}B/s | {tpot_dnf_number(size):>5}B "
                              f"{tpot_dnf_clock(took):>9}    \n")
                total += size
                seconds += took
            if len(rows) > 1:
                self.tpot_out("-" * TPOT_DNF_WIDTH + "\n")
                self.tpot_out(f"{'Total':<48} {tpot_dnf_number(total / max(seconds, 0.5)):>5}B/s | "
                              f"{tpot_dnf_number(total):>5}B {tpot_dnf_clock(seconds + 1):>9}    \n")
        self.tpot_out("Running transaction check\nTransaction check succeeded.\nRunning transaction test\n")
        # the deps first, as rpm orders them
        order = [row for row in rows if row[0] == "Installing dependencies"] + \
                [row for row in rows if row[0] != "Installing dependencies"]
        # the programs of the new packages; a tree that takes no more files fails as a full disk
        made = []
        for title, name, entry in order:
            if kind == "remove" or title == "Reinstalling":
                continue
            for program in entry.get("programs", []):
                try:
                    ok = self.fs.mkfile(program, 0, 0, random.randint(10000, 90000), 33261)
                except Exception:  # noqa: BLE001 - a file the session cannot create is a full disk
                    ok = False
                if not ok:
                    need = max(1, sum(e.get("size", 0) for t, _, e in order if t != "Reinstalling") // 1_000_000)
                    self.tpot_out("The downloaded packages were saved in cache until the next successful "
                                  f"transaction.\nYou can remove cached packages by executing '{self.tpot_prog} "
                                  "clean packages'.\n")
                    self.errorWrite(f"Error: Transaction test error:\n  installing package "
                                    f"{tpot_dnf_nevra(name, entry)} needs {need}MB more space on the / filesystem\n\n"
                                    "Error Summary\n-------------\nDisk Requirements:\n"
                                    f"   At least {need}MB more space needed on the / filesystem.\n\n")
                    self.exit(1)
                    return
                made.append(program)
        self.tpot_out("Transaction test succeeded.\nRunning transaction\n")
        yield self.sleep(0.5, 1)
        digits = len(str(len(order)))
        done = f"{{:>{digits}}}/{len(order)}"
        wid2 = 52 - 2 * (digits - 1)
        action = {"install": "Installing", "reinstall": "Reinstalling", "remove": "Erasing"}
        lines = [f"  {'Preparing':<17}: {'':<52}   1/1\n"]
        for number, (title, name, entry) in enumerate(order, 1):
            verb = "Reinstalling" if title == "Reinstalling" else action[kind]
            lines.append(f"  {verb:<17}: {tpot_dnf_nevra(name, entry):<{wid2}}   {done.format(number)}\n")
        last = order[-1]
        lines.append(f"  {'Running scriptlet':<17}: {tpot_dnf_nevra(last[1], last[2]):<{wid2}}   "
                     f"{done.format(len(order))}\n")
        for number, (_, name, entry) in enumerate(order, 1):
            lines.append(f"  {'Verifying':<17}: {tpot_dnf_nevra(name, entry):<{wid2}}   {done.format(number)}\n")
        for line in lines:
            self.tpot_out(line)
            yield self.sleep(0.05, 0.25)
        self.tpot_out("Installed products updated.\n\n")
        session = tpot_dnf_session(self.protocol)
        marked = getattr(self.protocol, "tpot_installed", None)
        for title, name, entry in order:
            if kind == "remove":
                for program in session["installed"].get(name, {}).get("programs", []):
                    if marked is not None:
                        marked.pop(program.rsplit("/", 1)[-1], None)
                session["installed"].pop(name, None)
                session["removed"].add(name)
            elif title != "Reinstalling":
                # the programs that answer as installed (dnf_packages "answers"); the others are faked
                for program in entry.get("programs", []):
                    if program.rsplit("/", 1)[-1] in tpot_dnf_data()["answers"]:
                        tpot_dnf_mark(self.protocol, program.rsplit("/", 1)[-1], entry["evr"])
                session["installed"][name] = {"evr": entry["evr"], "arch": entry["arch"],
                                              "from": "@" + entry["repo"], "summary": entry.get("summary", ""),
                                              "size": entry.get("size", 0), "programs": entry.get("programs", [])}
                session["removed"].discard(name)
        for program in made:
            name = program.rsplit("/", 1)[-1]
            faked = Command_faked_package_class_factory.getCommand(name)
            if name in tpot_dnf_data()["answers"]:
                # Cowrie's command of its name answers for it
                if name not in self.protocol.commands:
                    self.protocol.commands[program] = faked
            elif name in self.protocol.commands or program in self.protocol.commands:
                # a faked program of the session: Cowrie's command of that name stays skipped
                session_faked = getattr(self.protocol, "tpot_faked", None)
                if session_faked is None:
                    session_faked = {}
                    self.protocol.tpot_faked = session_faked
                session_faked[program] = faked
            else:
                self.protocol.commands[program] = faked
        names = sorted(tpot_dnf_nevra(name, entry) for _, name, entry in order)
        title = {"install": "Installed", "reinstall": "Reinstalled", "remove": "Removed"}[kind]
        self.tpot_out("\n".join(tpot_dnf_pto(title, names)) + "\n\nComplete!\n")
        self.exit(0)

    def tpot_list(self, names: list) -> None:
        narrow = "all"
        if names and names[0] in ("all", "installed", "available", "updates", "upgrades", "extras", "obsoletes",
                                  "recent", "autoremove"):
            narrow, names = names[0], names[1:]
        for opt in ("installed", "available", "updates", "upgrades", "extras"):
            if f"--{opt}" in self.tpot_opts:
                narrow = opt
        if narrow != "installed":
            self.tpot_metadata()
        installed, available = tpot_dnf_installed(self.protocol), tpot_dnf_available(self.protocol)

        def match(name, entry):
            if not names:
                return True
            full = f"{name}.{entry['arch']}"
            return any(fnmatch.fnmatchcase(name, pattern) or fnmatch.fnmatchcase(full, pattern) for pattern in names)

        parts = []
        if narrow in ("all", "installed"):
            parts.append(("Installed Packages", [(n, e, e.get("from", "@System")) for n, e in installed.items()
                                                 if match(n, e)]))
        if narrow in ("all", "available"):
            parts.append(("Available Packages", [(n, e, e["repo"]) for n, e in available.items() if match(n, e)]))
        parts = [(title, sorted(rows, key=lambda row: (row[0], row[1]["arch"]))) for title, rows in parts if rows]
        if not parts:
            self.errorWrite("Error: No matching Packages to list\n")
            self.exit(1)
            return
        rows = [row for _, part in parts for row in part]
        columns = tpot_dnf_columns([tpot_dnf_lengths(f"{n}.{e['arch']}" for n, e, _ in rows),
                                    tpot_dnf_lengths(e["evr"] for _, e, _ in rows),
                                    tpot_dnf_lengths(r for _, _, r in rows)], remainder_column=1)
        out = []
        for title, part in parts:
            out.append(title + "\n")
            for name, entry, repo in part:
                out.append(tpot_dnf_fmt(((f"{name}.{entry['arch']}", -columns[0]), (entry["evr"], -columns[1]),
                                         (repo, -columns[2]))) + "\n")
        self.tpot_out("".join(out))
        self.exit(0)

    def tpot_search(self, terms: list) -> None:
        if not terms:
            self.errorWrite(f"usage: {self.tpot_prog} search [-c [config file]] [-q] [-v] [--version] ... "
                            "[--all] KEYWORD [KEYWORD ...]\n"
                            f"{self.tpot_prog} search: error: the following arguments are required: KEYWORD\n")
            self.exit(2)
            return
        self.tpot_metadata()
        packages = {**tpot_dnf_available(self.protocol), **tpot_dnf_installed(self.protocol)}
        matches = []
        for name, entry in packages.items():
            keys, needles, haystacks, weight = [], set(), set(), 0
            for key, value in (("name", name), ("summary", entry.get("summary", ""))):
                hit = [term for term in terms if term.lower() in value.lower()]
                if hit:
                    keys.append(key)
                    needles.update(hit)
                    haystacks.add(value)
                    weight += sum(14 if key == "name" and value == term else 7 if key == "name" else 4 for term in hit)
            # every keyword (AND), with --all any of them (OR)
            if keys and ("--all" in self.tpot_opts or needles == set(terms)):
                matches.append((-weight, name, entry, tuple(keys), tuple(sorted(needles)), haystacks))
        if not matches:
            self.tpot_out("No matches found.\n")
            self.exit(0)
            return
        out, last = [], None
        for _, name, entry, keys, needles, haystacks in sorted(matches, key=lambda m: (m[0], m[1])):
            exact = haystacks == set(needles)
            header = (keys, needles, exact)
            if header != last:
                corpus = " & ".join(key.capitalize() for key in keys)
                out.append(tpot_dnf_section(f"{corpus} {'Exactly ' if exact else ''}Matched: {', '.join(needles)}")
                           + "\n")
                last = header
            key = f"{name}.{entry['arch']} : "
            text = textwrap.fill(entry.get("summary", ""), width=TPOT_DNF_WIDTH, initial_indent=key,
                                 subsequent_indent=" " * (len(key) - 2) + ": ")
            marked = [re.escape(term) for term in sorted(terms, key=len, reverse=True) if term]
            if marked:
                text = re.sub("|".join(marked), lambda hit: self.tpot_bold(hit.group(0)), text)
            out.append(text + "\n")
        self.tpot_out("".join(out))
        self.exit(0)

    def tpot_repolist(self, names: list) -> None:
        which = names[0] if names and names[0] in ("all", "enabled", "disabled") else "enabled"
        for opt in ("all", "enabled", "disabled"):
            if f"--{opt}" in self.tpot_opts:
                which = opt
        repos = sorted((repo for repo in tpot_dnf_data()["repos"]
                        if which == "all" or bool(repo.get("enabled")) == (which == "enabled")),
                       key=lambda repo: repo["id"])
        if not repos:
            self.errorWrite("No repositories available\n")
            self.exit(0)
            return
        status = which == "all"
        id_len = max([len("repo id")] + [len(repo["id"]) for repo in repos])
        nm_len = max(len(repo["name"]) for repo in repos)
        st_len = len("disabled") if any(not repo.get("enabled") for repo in repos) else len("enabled")
        left = TPOT_DNF_WIDTH - (id_len + max(len("status"), st_len) + 2) if status else TPOT_DNF_WIDTH - (id_len + 1)
        if left < nm_len:
            nm_len = left
        else:
            left -= nm_len
            id_len += left // 2
            nm_len += left - left // 2
        out = []
        if status:
            out.append(f"{'repo id':<{id_len}} {'repo name':<{nm_len}} status\n")
            for repo in repos:
                out.append(f"{repo['id']:<{id_len}} {repo['name'][:nm_len]:<{nm_len}} "
                           f"{'enabled' if repo.get('enabled') else 'disabled'}\n")
        else:
            out.append(f"{'repo id':<{id_len}} repo name\n")
            for repo in repos:
                out.append(f"{repo['id']:<{id_len}} {repo['name']}\n")
        self.tpot_out("".join(out))
        self.exit(0)


class Command_dnf_yum(Command_dnf):
    """yum of RHEL 9: dnf under its other name."""

    tpot_prog = "yum"


class Command_rpm(HoneyPotCommand):
    """rpm of the persona: --version, -q / -qa / -qf (and query options as -q), -i / -U / -e."""

    def call(self) -> None:
        try:
            self.exit_code = self.tpot_rpm()
        except Exception:  # noqa: BLE001 - the command always exits
            self.exit_code = 1

    def tpot_rpm(self) -> int:
        data = tpot_dnf_data()
        installed = tpot_dnf_installed(self.protocol)
        args = list(self.args)
        flags = set()
        words = []
        for arg in args:
            if arg in ("--version",):
                self.write(f"RPM version {data.get('rpm_version', '4.16.1.3')}\n")
                return 0
            if arg in ("--query", "--all", "--file", "--erase", "--install", "--upgrade", "--freshen", "--import",
                       "--checksig", "--verify", "--rebuilddb", "--initdb", "--nodeps", "--force", "--quiet"):
                flags.add({"--query": "q", "--all": "a", "--file": "f", "--erase": "e", "--install": "i",
                           "--upgrade": "U", "--freshen": "F", "--import": "I", "--checksig": "K", "--verify": "V",
                           "--rebuilddb": "R", "--initdb": "R"}.get(arg, ""))
            elif arg.startswith("--"):
                continue
            elif arg.startswith("-") and len(arg) > 1:
                flags.update(arg[1:])
            else:
                words.append(arg)
        flags.discard("")
        if not flags & set("qeiUFIKVR"):
            self.errorWrite("RPM version " + data.get("rpm_version", "4.16.1.3") + "\n"
                            "Copyright (C) 1998-2002 - Red Hat, Inc.\n"
                            "This program may be freely redistributed under the terms of the GNU GPL\n\n"
                            "Usage: rpm [-afgpcdLAlsiv?] [-a|--all] [-f|--file] [-g|--group] [-p|--package] "
                            "[--pkgid] [--hdrid] [--triggeredby] [--whatconflicts] [--whatrequires] "
                            "[--whatobsoletes] [--whatprovides] [--whatrecommends] [--whatsuggests] "
                            "[--whatsupplements] [--whatenhances] [--nomanifest] [-c|--configfiles] "
                            "[-d|--docfiles] [-L|--licensefiles] [-A|--artifactfiles] [--dump] [-l|--list]\n")
            return 1
        if "q" in flags:
            if "a" in flags:
                rows = [tpot_rpm_nvra(name, entry) for name, entry in installed.items()
                        if not words or any(fnmatch.fnmatchcase(name, w) or fnmatch.fnmatchcase(tpot_rpm_nvra(name, entry), w)
                                            for w in words)]
                for row in sorted(rows, key=lambda row: tpot_dnf_digest(row)):
                    self.write(row + "\n")
                return 0 if rows or not words else 1
            if not words:
                self.errorWrite("rpm: no arguments given for query\n")
                return 1
            failed = 0
            owners = {}
            if "f" in flags:
                owners = dict(data["owners"])
                for name, entry in installed.items():
                    for program in entry.get("programs", []):
                        owners[program] = name
            for word in words:
                if "f" in flags:
                    path = self.fs.resolve_path(word, self.cwd)
                    for top in ("/bin/", "/sbin/"):
                        if path.startswith(top):
                            path = "/usr" + path
                    if not self.fs.exists(path):
                        self.errorWrite(f"error: file {path}: No such file or directory\n")
                        failed += 1
                    elif path in owners and owners[path] in installed:
                        self.write(tpot_rpm_nvra(owners[path], installed[owners[path]]) + "\n")
                    else:
                        self.write(f"file {path} is not owned by any package\n")
                        failed += 1
                elif word in installed:
                    self.write(tpot_rpm_nvra(word, installed[word]) + "\n")
                else:
                    self.write(f"package {word} is not installed\n")
                    failed += 1
            return min(failed, 255)
        if "V" in flags:
            failed = [word for word in words if word not in installed]
            for word in failed:
                self.write(f"package {word} is not installed\n")
            return min(len(failed), 255)
        if not tpot_dnf_privileged(self):
            self.errorWrite("error: can't create transaction lock on /var/lib/rpm/.rpm.lock (Permission denied)\n")
            return 1
        if "e" in flags:
            failed = 0
            for word in words:
                if word in installed:
                    session = tpot_dnf_session(self.protocol)
                    session["installed"].pop(word, None)
                    session["removed"].add(word)
                else:
                    self.errorWrite(f"error: package {word} is not installed\n")
                    failed += 1
            return min(failed, 255)
        if flags & set("iUF"):
            if not words:
                self.errorWrite("rpm: no packages given for install\n")
                return 1
            for word in words:
                if "://" in word:
                    self.write(f"Retrieving {word}\n")
                    self.errorWrite(f"error: skipping {word} - transfer failed\n")
                elif self.fs.exists(self.fs.resolve_path(word, self.cwd)):
                    self.errorWrite(f"error: {word}: not an rpm package (or package manifest)\n")
                else:
                    self.errorWrite(f"error: open of {word} failed: No such file or directory\n")
            return 1
        return 0


if CowrieConfig.get("shell", "dnf_packages", fallback=""):
    commands["/usr/bin/yum"] = Command_dnf_yum
    commands["yum"] = Command_dnf_yum
commands["/usr/bin/dnf"] = Command_dnf
commands["dnf"] = Command_dnf
commands["/usr/bin/dnf-3"] = Command_dnf
commands["dnf-3"] = Command_dnf
commands["/usr/bin/rpm"] = Command_rpm
commands["rpm"] = Command_rpm
'''


def patch_dnf(cowrie_root):
    """dnf 4 and rpm of RHEL 9 (DNF_COMMAND, in the module of yum): yum of a persona with [shell]
    dnf_packages is dnf; dnf and rpm answer only where the persona does not skip them. Runs after
    patch_yum."""
    path = cowrie_root / "src" / "cowrie" / "commands" / "yum.py"
    replace_once(path, "import hashlib\nimport random\nimport re\n",
                 "import datetime\nimport fnmatch\nimport functools\nimport hashlib\nimport json\nimport random\n"
                 "import re\nimport textwrap\nimport time\nfrom pathlib import Path\n")
    replace_once(path, 'commands["/usr/bin/yum"] = Command_yum\ncommands["yum"] = Command_yum\n',
                 'commands["/usr/bin/yum"] = Command_yum\ncommands["yum"] = Command_yum\n' + DNF_COMMAND)


def patch_free(cowrie_root):
    """free reads the emulated /proc/meminfo of the persona (honeyfs / fs.pickle),
    not the container's real one."""
    path = cowrie_root / "src" / "cowrie" / "commands" / "free.py"
    replace_once(
        path,
        """        try:
            with open("/proc/meminfo") as proc_file:
                for line in proc_file:
                    tokens = line.split(":")

                    # Later we are going to do some math on those numbers, better not include uneeded keys for performance
                    if tokens[0] in needed_keys:
                        mem_info_map[tokens[0]] = int(tokens[1].lstrip().split(" ")[0])
        except Exception:
            pass
""",
        """        try:
            meminfo = self.fs.file_contents("/proc/meminfo").decode("utf-8", "replace")
            for line in meminfo.splitlines():
                tokens = line.split(":")
                if tokens[0] in needed_keys:
                    mem_info_map[tokens[0]] = int(tokens[1].lstrip().split(" ")[0])
        except Exception:
            pass
""",
    )


# methods of the patched HoneyPotBaseProtocol (shell/protocol.py) after uptime(): the load, the
# tasks and the uptime line that top, uptime, w and /proc/loadavg share
PROTOCOL_LOAD = r'''
    # T-Pot: the load of the emulated system, shared by top, uptime, w and /proc/loadavg
    TPOT_LOAD_WAVES = ((0.5, 1740.0), (0.3, 410.0), (0.15, 97.0))

    def tpot_load_average(self, now: float | None = None) -> tuple[float, float, float]:
        """T-Pot: the 1, 5 and 15 minute load average. A base load ([shell] load_base) drifts in
        three slow waves (phases from the host name); the kernel's three exponential averages
        (time constants 1, 5 and 15 minutes, in closed form for a wave) smooth it, so the values
        move slowly, the 15 minute one least, and stay above zero."""
        now = time.time() if now is None else now
        try:
            base = max(0.0, float(CowrieConfig.get("shell", "load_base", fallback="0.05")))
        except ValueError:
            base = 0.05
        seed = hashlib.sha256(str(self.hostname).encode("utf-8")).digest()
        values = []
        for tau in (60.0, 300.0, 900.0):
            value = base
            for index, (share, period) in enumerate(self.TPOT_LOAD_WAVES):
                omega = 2 * math.pi / period
                phase = seed[index] / 256 * 2 * math.pi
                value += (base * share / math.hypot(1.0, omega * tau)
                          * math.sin(omega * now + phase - math.atan(omega * tau)))
            values.append(max(0.0, value))
        return values[0], values[1], values[2]

    def tpot_processes(self) -> list:
        """T-Pot: the ps list of the persona ([shell] processes)."""
        server = getattr(self.user, "server", None)
        return [p for p in (getattr(server, "process", None) or []) if isinstance(p, dict)]

    def tpot_tasks(self) -> int:
        """T-Pot: the tasks of the system: the ps list, the session's server process, the running
        command and the kernel threads ps does not list ([shell] kernel_threads)."""
        try:
            threads = max(0, CowrieConfig.getint("shell", "kernel_threads", fallback=0))
        except ValueError:
            threads = 0
        return len(self.tpot_processes()) + 2 + threads

    def tpot_last_pid(self, now: float | None = None) -> int:
        """T-Pot: the newest PID: a few after the newest process of the ps list, more the longer
        the session runs."""
        now = time.time() if now is None else now
        pids = [p["PID"] for p in self.tpot_processes() if isinstance(p.get("PID"), int)]
        since = max(0.0, now - getattr(self, "logintime", now))
        return max(pids or [1000]) + 9 + int(since // 7)

    def tpot_uptime_line(self, now: float | None = None) -> str:
        """T-Pot: the line of uptime and w, and of top after "top -": procps sprint_uptime
        (" 12:57:13 up 11 days,  4:22,  1 user,  load average: 0.06, 0.04, 0.01"); BusyBox
        ([shell] top_style busybox) names no users."""
        now = time.time() if now is None else now
        up = max(0, int(now - self.boot_time()))
        days, rest = divmod(up, 86400)
        hours, minutes = rest // 3600, rest % 3600 // 60
        text = f"{days} day{'s' if days != 1 else ''}, " if days else ""
        text += f"{hours:2d}:{minutes:02d}" if hours else f"{minutes} min"
        load = ", ".join(f"{value:.2f}" for value in self.tpot_load_average(now))
        clock = time.strftime("%H:%M:%S", time.localtime(now))
        if CowrieConfig.get("shell", "top_style", fallback="procps") == "busybox":
            return f" {clock} up {text},  load average: {load}"
        return f" {clock} up {text},  1 user,  load average: {load}"

    def proc_loadavg(self) -> bytes:
        """T-Pot: /proc/loadavg: the load average of top and uptime, runnable / all tasks and the
        newest PID."""
        now = time.time()
        load = " ".join(f"{value:.2f}" for value in self.tpot_load_average(now))
        return f"{load} 1/{self.tpot_tasks()} {self.tpot_last_pid(now)}\n".encode()
'''


def patch_protocol_load(cowrie_root):
    """The load average, the task count and the uptime line of the emulated system (PROTOCOL_LOAD)
    on the protocol, /proc/loadavg from them; a command with tpot_key (top) reads the keys of the
    terminal one by one. Runs after patch_protocol."""
    path = cowrie_root / "src" / "cowrie" / "shell" / "protocol.py"
    replace_once(path, "import functools\nimport random\n", "import functools\nimport hashlib\nimport math\nimport random\n")
    replace_once(
        path,
        '''    def uptime(self):
        """
        Uptime
        """
        return time.time() - self.boot_time()
''',
        '''    def uptime(self):
        """
        Uptime
        """
        return time.time() - self.boot_time()
''' + PROTOCOL_LOAD,
    )
    replace_once(
        path,
        '        self.fs.generated_files["/proc/uptime"] = self.proc_uptime\n',
        '        self.fs.generated_files["/proc/uptime"] = self.proc_uptime\n'
        '        self.fs.generated_files["/proc/loadavg"] = self.proc_loadavg\n',
    )
    replace_once(
        path,
        '''        Easier way to implement password input?
        """
        self.resetTimeout()  # Reset the idle timeout
''',
        '''        Easier way to implement password input?
        """
        self.resetTimeout()  # Reset the idle timeout
        # T-Pot: a full screen command (top) takes the keys one by one, without echo
        key = getattr(self.cmdstack[-1], "tpot_key", None) if self.cmdstack else None
        if key is not None and key(ch):
            return
''',
    )
    replace_once(
        path,
        '''        assert self.terminal is not None
        self.terminal.write(b"timed out waiting for input: auto-logout\\n")
''',
        '''        assert self.terminal is not None
        # T-Pot: a full screen command (top) gives the cursor back first
        restore = getattr(self.cmdstack[-1], "tpot_restore", None) if self.cmdstack else None
        if restore is not None:
            restore()
        self.terminal.write(b"timed out waiting for input: auto-logout\\n")
''',
    )
    replace_once(
        path,
        """    def keystrokeReceived(self, keyID, modifier):
        if not self.stdin_line_mode:
""",
        """    def keystrokeReceived(self, keyID, modifier):
        # T-Pot: a full screen command (top, ssh -t) takes the keys of the pty
        key = getattr(self.cmdstack[-1], "tpot_key", None) if self.cmdstack else None
        if key is not None and isinstance(keyID, bytes) and key(keyID):
            return
        if not self.stdin_line_mode:
""",
    )


def patch_uptime(cowrie_root):
    """uptime prints the line of procps (BusyBox: no users) with the live load average."""
    path = cowrie_root / "src" / "cowrie" / "commands" / "uptime.py"
    replace_once(
        path,
        '''        self.write(
            "{}  up {},  1 user,  load average: 0.00, 0.00, 0.00\\n".format(
                time.strftime("%H:%M:%S"), utils.uptime(self.protocol.uptime())
            )
        )
''',
        '''        # T-Pot: the line of procps uptime, the load average of top
        self.write(self.protocol.tpot_uptime_line() + "\\n")
''',
    )


# module code of the patched base.py before Command_ps: the terminal ps and top write to
PS_WINDOW = r'''

def tpot_window(cmd) -> tuple[int, int] | None:
    """T-Pot: (rows, columns) of the terminal when the stdout of cmd is the session's terminal
    with a pty, else None (a pipe, a file, ssh without a pty). LINES / COLUMNS win, as procps
    reads them."""
    targets = getattr(getattr(cmd, "pp", None), "targets", None) or {}
    if (targets.get(1) or ("",))[0] != "terminal":
        return None
    size = getattr(getattr(cmd.protocol, "user", None), "windowSize", None)
    if not size:
        return None

    def number(name: str, fallback: int) -> int:
        try:
            value = int(str((cmd.environ or {}).get(name, "")).strip())
        except ValueError:
            return fallback
        return value if 0 < value < 32768 else fallback

    return number("LINES", int(size[0]) or 24), number("COLUMNS", int(size[1]) or 80)


def tpot_ps_width(cmd, args: str) -> int:
    """T-Pot: the width procps ps cuts its lines to, 0 for none: the columns of the terminal,
    at least 132 with w, none with ww or when stdout is no terminal (ps aux | grep sees it all)."""
    window = tpot_window(cmd)
    wide = args.count("w")
    if window is None or wide >= 2:
        return 0
    return max(window[1], 132) if wide else window[1]
'''

# module code of the patched base.py after Command_ps: top of the persona
TOP_COMMAND = r'''

# T-Pot: top of the persona. The header is live: clock, uptime, load average, tasks and CPU of
# the session's system, memory of the emulated /proc/meminfo; the processes are those of the ps
# list, the session's server process and top itself. [shell] top_style: procps (procps_version
# picks KiB and the old columns before 3.3.13, unverified) or busybox. Nothing runs forever: at
# most TPOT_TOP_FRAMES frames and TPOT_TOP_SECONDS seconds, q or Ctrl-C ends the screen.
TPOT_TOP_FRAMES = 200
TPOT_TOP_SECONDS = 900.0
TPOT_TOP_ARGUMENT = frozenset("dnpuUoOwEe")
TPOT_TOP_FLAGS = frozenset("bcHiSs1")


def tpot_top_hash(*parts) -> int:
    return int.from_bytes(hashlib.sha256("\0".join(str(p) for p in parts).encode("utf-8")).digest()[:8], "big")


def tpot_top_time(seconds: float) -> str:
    """TIME+ of procps top: minutes:seconds.hundredths, the hundredths dropped when too wide."""
    hundredths = int(seconds * 100)
    minutes, rest = divmod(hundredths, 6000)
    text = f"{minutes}:{rest // 100:02d}.{rest % 100:02d}"
    return text if len(text) <= 9 else f"{minutes}:{rest // 100:02d}"


def tpot_top_comm(command: str) -> str:
    """The name procps top shows (comm, 15 characters): "/usr/sbin/cron -f" -> cron, "-bash" -> bash,
    "sshd: ubuntu@pts/0" -> sshd, a script run by its interpreter by the script's name."""
    words = command.split()
    if not words:
        return ""
    name = words[0].rstrip(":").rsplit("/", 1)[-1].lstrip("-")
    if re.fullmatch(r"(python|perl|ruby|bash|sh)[0-9.]*", name) and len(words) > 1 and words[1].startswith("/"):
        name = words[1].rsplit("/", 1)[-1]
    return name[:15]


def tpot_top_user(name: str) -> str:
    return name if len(name) <= 8 else name[:7] + "+"


def tpot_top_number(value: str, limit: int) -> int | None:
    """A number of an option of top: ASCII digits only, at most limit (int would take other digits
    and fail on more than 4300 of them)."""
    if not (0 < len(value) <= len(str(limit)) and value.isascii() and value.isdigit()):
        return None
    number = int(value)
    return number if number <= limit else None


class Command_top(HoneyPotCommand):
    """T-Pot: top of the persona (see TOP_COMMAND)."""

    def start(self) -> None:
        try:
            self.tpot_start()
        except Exception:  # noqa: BLE001 - the command always exits
            self.tpot_fail()

    def tpot_fail(self) -> None:
        scheduled = getattr(self, "scheduled", None)
        if scheduled is not None and scheduled.active():
            scheduled.cancel()
        self.scheduled = None
        try:
            self.tpot_restore()
        except Exception:  # noqa: BLE001 - the command always exits
            pass
        self.exit(1)

    def tpot_start(self) -> None:
        self.style = CowrieConfig.get("shell", "top_style", fallback="procps")
        version = CowrieConfig.get("shell", "procps_version", fallback="4.0.4")
        self.version = tuple(int(x) for x in re.findall(r"\d+", version)[:3]) or (4, 0, 4)
        self.version_text = version
        self.scheduled = None
        self.frames = 0
        self.began = time.time()
        # the PID of this top, the newest when it started
        self.pid = self.protocol.tpot_last_pid(self.began)
        self.interactive = False
        self.restored = False
        self.limit = 0
        self.delay = 5.0 if self.style == "busybox" else 3.0
        self.users: set[str] = set()
        self.pids: set[int] = set()
        if not self.tpot_options():
            return
        self.window = tpot_window(self)
        if not self.batch and self.window is None:
            if hasattr(self.protocol, "execcmd") and self.style != "busybox":
                # ssh host top: no terminal to draw on, and without a pty sshd sets no TERM (ncurses'
                # setupterm ends top then)
                self.errorWrite("top: failed tty get\n" if (self.environ or {}).get("TERM")
                                else "TERM environment variable not set.\n")
                self.exit(1)
                return
            self.batch = True
        if self.batch and self.window is None and not self.limit:
            # a pipe or a file without -n: one frame, as when the reader has what it wants
            self.limit = 1
        self.limit = min(self.limit or TPOT_TOP_FRAMES, TPOT_TOP_FRAMES)
        self.interactive = not self.batch
        if self.interactive:
            self.write("\x1b[?25l\x1b[H\x1b[2J")
        self.tpot_tick()

    def tpot_usage(self, error: str) -> None:
        if self.style == "busybox":
            self.errorWrite(f"top: {error}\nUsage: top [-b] [-nCOUNT] [-dSECONDS] [-m]\n")
        else:
            flags = "bcHiOSs" if self.version < (3, 3, 12) else "bcEeHiOSs1"
            self.errorWrite(f"top: {error}\nUsage:\n  top -hv | -{flags} -d secs -n max -u|U user -p pid(s)"
                            " -o field -w [cols]\n")
        self.exit(1)

    def tpot_options(self) -> bool:
        """-b, -n, -d, -p, -u / -U (filters) and the flags top takes; False when it ended."""
        self.batch = False
        args = list(self.args)
        while args:
            arg = args.pop(0)
            if not arg.startswith("-") or arg == "-":
                self.tpot_usage(f"unknown option '{arg[:1] or '-'}'" if self.style != "busybox"
                                else f"invalid option -- '{arg[:1]}'")
                return False
            letters = arg[1:]
            while letters:
                letter, letters = letters[0], letters[1:]
                if letter in ("h", "v") and self.style != "busybox":
                    self.write(f"  procps-ng {'version ' if self.version < (4,) else ''}{self.version_text}\n"
                               "Usage:\n  top -hv | -"
                               f"{'bcHiOSs' if self.version < (3, 3, 12) else 'bcEeHiOSs1'}"
                               " -d secs -n max -u|U user -p pid(s) -o field -w [cols]\n")
                    self.exit(0)
                    return False
                if letter == "b":
                    self.batch = True
                    continue
                if letter == "m" and self.style == "busybox":
                    continue
                if letter in TPOT_TOP_FLAGS and self.style != "busybox":
                    continue
                if letter not in ("d", "n") and (self.style == "busybox" or letter not in TPOT_TOP_ARGUMENT):
                    self.tpot_usage(f"unknown option '{letter}'" if self.style != "busybox"
                                    else f"invalid option -- '{letter}'")
                    return False
                value, letters = letters, ""
                if not value:
                    if letter == "w" and (not args or tpot_top_number(args[0], 0x7FFFFFFF) is None):
                        continue
                    if not args:
                        self.tpot_usage(f"-{letter} requires argument" if self.style != "busybox"
                                        else f"option requires an argument -- '{letter}'")
                        return False
                    value = args.pop(0)
                if not self.tpot_option(letter, value):
                    return False
        return True

    def tpot_option(self, letter: str, value: str) -> bool:
        if letter == "n":
            count = tpot_top_number(value, 0x7FFFFFFF)
            if count is None:
                self.errorWrite(f"top: bad iterations argument '{value}'\n")
                self.exit(1)
                return False
            self.limit = count
        elif letter == "d":
            # ASCII digits, one decimal point or comma, an exponent (float would take more)
            if len(value) <= 64 and re.fullmatch(r"(?:[0-9]+(?:[.,][0-9]*)?|[.,][0-9]+)(?:[eE][+-]?[0-9]{1,3})?",
                                                 value):
                delay = float(value.replace(",", "."))
            else:
                delay = -1.0
            if delay < 0 or delay != delay:
                self.errorWrite(f"top: bad delay interval '{value}'\n")
                self.exit(1)
                return False
            self.delay = delay
        elif letter == "p":
            for item in value.split(","):
                pid = tpot_top_number(item.strip(), 0x7FFFFFFF)
                if pid is not None:
                    self.pids.add(pid)
        elif letter in ("u", "U"):
            self.users.add(value)
        return True

    # --- frames -----------------------------------------------------------------------------

    def tpot_tick(self) -> None:
        try:
            self.tpot_draw()
        except Exception:  # noqa: BLE001 - the command always exits
            self.tpot_fail()

    def tpot_draw(self) -> None:
        self.scheduled = None
        if self.protocol is None or self.protocol.terminal is None:
            return
        if self not in self.protocol.cmdstack:
            # the shell ended under the screen: the cursor comes back, nothing more is drawn
            self.tpot_restore()
            return
        if self.interactive:
            # a screen that redraws is no idle session (bounded by TPOT_TOP_SECONDS)
            reset = getattr(self.protocol, "resetTimeout", None)
            if reset is not None:
                reset()
        now = time.time()
        lines = self.tpot_frame(now)
        if self.interactive:
            rows, columns = self.window
            lines = [line[:columns] for line in lines[:rows]]
            heading = 6 if self.style != "busybox" else 3
            if len(lines) > heading:
                lines[heading] = "\x1b[7m" + lines[heading].ljust(columns)[:columns] + "\x1b[m"
            self.write("\x1b[H" + "\n".join(line + "\x1b[K" for line in lines) + "\x1b[J")
        else:
            if self.window is not None:
                lines = [line[:max(self.window[1], 80)] if self.style == "busybox" else line for line in lines]
            self.write("\n".join(lines) + "\n\n")
        self.frames += 1
        if self.frames >= self.limit or now - self.began >= TPOT_TOP_SECONDS:
            self.tpot_end()
            return
        self.scheduled = reactor.callLater(min(max(self.delay, 0.5), 60.0), self.tpot_tick)

    def tpot_restore(self) -> None:
        """The cursor the screen hid comes back, once, on every way the screen ends."""
        if not self.interactive or self.restored:
            return
        self.restored = True
        terminal = getattr(self.protocol, "terminal", None)
        if terminal is not None:
            terminal.write(b"\x1b[?25h")

    def tpot_end(self, interrupted: bool = False) -> None:
        if self.scheduled is not None and self.scheduled.active():
            self.scheduled.cancel()
        self.scheduled = None
        if self.interactive and not self.restored:
            self.restored = True
            rows = self.window[0] if self.window else 24
            self.write(f"\x1b[{rows};1H\x1b[?25h\n")
        elif interrupted:
            self.write("^C\n")
        self.exit(0)

    def tpot_key(self, ch: bytes) -> bool:
        """A key of the terminal while top draws: q ends, space draws now, the rest is ignored."""
        if not self.interactive:
            return False
        if ch in (b"q", "q", b"\x03"):
            self.tpot_end()
        elif ch in (b" ", " ") and self.scheduled is not None:
            self.scheduled.cancel()
            self.tpot_tick()
        return True

    def lineReceived(self, line: str) -> None:
        if not self.interactive:
            super().lineReceived(line)
            return
        if self.scheduled is not None:
            self.scheduled.cancel()
            self.tpot_tick()

    def handle_CTRL_C(self) -> None:
        self.tpot_end(interrupted=True)

    def eofReceived(self) -> None:
        # Ctrl-D, or the end of the channel's input, ends the screen; a batch top runs on
        if self.interactive:
            self.tpot_end()
        else:
            super().eofReceived()

    # --- what a frame shows -----------------------------------------------------------------

    def tpot_rows(self, now: float) -> list[dict]:
        """The processes: the ps list, the session's server process and top itself."""
        protocol = self.protocol
        host = protocol.hostname
        uptime = max(1.0, now - protocol.boot_time())
        user = str(self.user.get("username", "root"))
        listed = protocol.tpot_processes()
        rows = []
        shell = None
        for entry in listed:
            try:
                pid = int(entry["PID"])
            except (KeyError, TypeError, ValueError):
                continue
            stat = str(entry.get("STAT") or "S")
            tty = str(entry.get("TTY") or "?")
            login = tty.startswith(("pts/", "tty"))
            if login:
                shell = pid
                seconds = 0.01 + tpot_top_hash(host, pid, "cpu") % 7 / 100
            else:
                rate = (50 + tpot_top_hash(host, pid, "cpu") % 950) / 1e7 * (1 + int(entry.get("RSS") or 0) / 20000)
                seconds = uptime * rate
            rows.append({"pid": pid, "ppid": 0 if pid == 1 else 1, "user": str(entry.get("USER") or "root"),
                         "stat": stat, "virt": int(entry.get("VSZ") or 0), "res": int(entry.get("RSS") or 0),
                         "mem": float(entry.get("MEM") or 0.0), "time": seconds,
                         "command": str(entry.get("COMMAND") or "")})
        last = self.pid
        daemon, chain = self.tpot_session(rows, user)
        # the session's processes just below the shell, each the child of the one before
        parent = daemon["pid"] if daemon else 1
        first = (shell or last - 1) - len(chain)
        for offset, process in enumerate(chain):
            rows.append({"pid": first + offset, "ppid": parent, "stat": "S", "mem": 0.1, "time": 0.02, **process})
            parent = first + offset
        for row in rows:
            if row["pid"] == shell:
                row["ppid"] = parent
        # unverified: what top itself takes (procps-ng 3.3.10 of CentOS 7 maps 160 MB; a router's less)
        if self.style == "busybox":
            virt, res = 1408, 0
        elif CowrieConfig.get("shell", "hardware_platform", fallback="x86_64") != "x86_64":
            virt, res = 4852, 2432
        elif self.version < (3, 3, 13):
            virt, res = 162020, 2228
        else:
            virt, res = 11928, 5376
        rows.append({"pid": last, "ppid": shell or 1, "user": tpot_top_user(user), "stat": "R", "virt": virt,
                     "res": res, "mem": 0.1, "time": 0.01 * (1 + self.frames), "command": "top", "self": True})
        if self.users:
            rows = [r for r in rows if r["user"] in self.users]
        if self.pids:
            rows = [r for r in rows if r["pid"] in self.pids]
        return rows

    def tpot_session(self, rows: list[dict], user: str) -> tuple[dict | None, list[dict]]:
        """The daemon of this session (in the ps list, or None) and the processes between it and the
        shell. SSH: the session's sshd (sshd-session from OpenSSH 9.8) or dropbear. Telnet on
        BusyBox: telnetd, whose login execs the shell (telnetd -> shell; a telnetd of its own
        where the ps list has none); elsewhere in.telnetd -> login -> shell."""
        names = {tpot_top_comm(r["command"]): r for r in rows}
        telnet = any("Telnet" in kind.__name__ for kind in type(self.protocol).__mro__)
        if telnet:
            daemon = names.get("telnetd") or names.get("utelnetd") or names.get("in.telnetd")
            if self.style == "busybox":
                return daemon, [] if daemon else [{"user": "root", "command": "/usr/sbin/telnetd", "virt": 1584,
                                                   "res": 388}]
            client = getattr(self.protocol, "clientIP", "")
            return daemon, [{"user": "root", "command": f"in.telnetd: {client}", "virt": 6704, "res": 1632},
                            {"user": "root", "command": f"login -- {user}", "virt": 14460, "res": 4128}]
        ssh = CowrieConfig.get("shell", "ssh_version", fallback="OpenSSH")
        if "dropbear" in ssh.lower():
            daemon = names.get("dropbear")
            return daemon, [{"user": "root", "command": daemon["command"] if daemon else "dropbear",
                             "virt": 1104, "res": 640}]
        match = re.match(r"OpenSSH_(\d+)\.(\d+)", ssh)
        newer = match is not None and (int(match.group(1)), int(match.group(2))) >= (9, 8)
        return names.get("sshd"), [{"user": tpot_top_user(user),
                                    "command": f"sshd{'-session' if newer else ''}: {user}@pts/0",
                                    "virt": 17400, "res": 6960}]

    def tpot_cpu(self, now: float, row: dict) -> float:
        """%CPU of a process in this frame: top itself some, now and then a daemon a tick."""
        host = self.protocol.hostname
        if row.get("self"):
            if self.frames == 0:
                return (0.0, 5.9, 6.2, 6.7, 11.8, 12.5)[tpot_top_hash(host, now) % 6]
            return (0.0, 0.3, 0.3, 0.7, 1.0)[tpot_top_hash(host, int(now)) % 5]
        if self.frames and tpot_top_hash(host, row["pid"], int(now)) % 40 == 0:
            return 0.3
        return 0.0

    def tpot_summary_cpu(self, now: float) -> dict:
        """us / sy / wa / si of this frame: since boot in the first (1 to 4 % busy, per host), then
        the last seconds, a little more with the load."""
        if self.frames == 0:
            busy = 1.0 + tpot_top_hash(self.protocol.hostname, "boot") % 300 / 100
            return {"us": round(busy * 0.55, 1), "sy": round(busy * 0.3, 1), "wa": round(busy * 0.1, 1),
                    "si": round(busy * 0.05, 1)}
        load = self.protocol.tpot_load_average(now)[0]
        draw = tpot_top_hash(self.protocol.hostname, "cpu", int(now))
        us = round(min(25.0, load * 4 + draw % 5 / 10), 1)
        sy = round(min(15.0, load * 2 + draw // 5 % 4 / 10), 1)
        return {"us": us, "sy": sy, "wa": 0.0, "si": 0.0}

    def tpot_frame(self, now: float) -> list[str]:
        protocol = self.protocol
        rows = self.tpot_rows(now)
        cpus = {id(r): self.tpot_cpu(now, r) for r in rows}
        rows.sort(key=lambda r: (-cpus[id(r)], r["pid"]))
        mem = tpot_top_meminfo(self.fs)
        total = mem.get("MemTotal", 0)
        free = mem.get("MemFree", 0)
        cache = mem.get("Buffers", 0) + mem.get("Cached", 0) + mem.get("SReclaimable", 0)
        load = protocol.tpot_load_average(now)
        tasks = protocol.tpot_tasks()
        running = 1 + sum(1 for r in rows if r["stat"][:1] == "R" and not r.get("self"))
        cpu = self.tpot_summary_cpu(now)
        if self.style == "busybox":
            idle = max(0, 100 - round(cpu["us"]) - round(cpu["sy"]) - round(cpu["si"]))
            lines = [
                f"Mem: {total - free}K used, {free}K free, {mem.get('Shmem', 0)}K shrd, {mem.get('Buffers', 0)}K buff, "
                f"{mem.get('Cached', 0)}K cached",
                f"CPU: {round(cpu['us']):3d}% usr {round(cpu['sy']):3d}% sys   0% nic {idle:3d}% idle   0% io"
                f"   0% irq {round(cpu['si']):3d}% sirq",
                f"Load average: {load[0]:.2f} {load[1]:.2f} {load[2]:.2f} {running}/{tasks} {self.pid}",
                "  PID  PPID USER     STAT   VSZ %VSZ %CPU COMMAND",
            ]
            for r in rows:
                state = r["stat"][:1] + ("<" if "<" in r["stat"] else "N" if "N" in r["stat"] else "")
                share = r["virt"] * 100 // total if total else 0
                lines.append(f"{r['pid']:5d}{r['ppid']:6d} {r['user'][:8]:<8} {state:<4}{r['virt']:6d}{share:4d}%"
                             f"{round(cpus[id(r)]):4d}% {r['command']}")
            return lines
        idle = round(100.0 - cpu["us"] - cpu["sy"] - cpu["wa"] - cpu["si"], 1)
        used = max(0, total - free - cache)
        lines = [
            "top -" + protocol.tpot_uptime_line(now),
            f"Tasks:{tasks:4d} total,{running:4d} running,{tasks - running:4d} sleeping,   0 stopped,   0 zombie",
            f"%Cpu(s):{cpu['us']:5.1f} us,{cpu['sy']:5.1f} sy,  0.0 ni,{idle:5.1f} id,{cpu['wa']:5.1f} wa,"
            f"  0.0 hi,{cpu['si']:5.1f} si,  0.0 st",
        ]
        swap_total, swap_free = mem.get("SwapTotal", 0), mem.get("SwapFree", 0)
        old = self.version < (3, 3, 13)
        if old:
            lines += [f"KiB Mem :{total:9d} total,{free:9d} free,{used:9d} used,{cache:9d} buff/cache",
                      f"KiB Swap:{swap_total:9d} total,{swap_free:9d} free,{swap_total - swap_free:9d} used."
                      f"{mem.get('MemAvailable', free):9d} avail Mem"]
        else:
            lines += [f"MiB Mem :{total / 1024:9.1f} total,{free / 1024:9.1f} free,{used / 1024:9.1f} used,"
                      f"{cache / 1024:9.1f} buff/cache",
                      f"MiB Swap:{swap_total / 1024:9.1f} total,{swap_free / 1024:9.1f} free,"
                      f"{(swap_total - swap_free) / 1024:9.1f} used.{mem.get('MemAvailable', free) / 1024:9.1f} avail Mem"]
        lines.append("")
        if old:
            lines.append("  PID USER      PR  NI    VIRT    RES    SHR S %CPU %MEM     TIME+ COMMAND")
        else:
            lines.append("    PID USER      PR  NI    VIRT    RES    SHR S  %CPU  %MEM     TIME+ COMMAND")
        for r in rows:
            nice = -4 if "<" in r["stat"] else 19 if "N" in r["stat"] else 0
            shared = r["res"] * (55 + tpot_top_hash(r["pid"], "shr") % 25) // 100
            comm = tpot_top_comm(r["command"])
            if old:
                lines.append(f"{r['pid']:5d} {r['user'][:8]:<8}  {20 + nice:2d} {nice:3d} {r['virt']:7d} {r['res']:6d} "
                             f"{shared:6d} {r['stat'][:1]} {cpus[id(r)]:4.1f} {r['mem']:4.1f} "
                             f"{tpot_top_time(r['time']):>9} {comm}")
            else:
                lines.append(f"{r['pid']:7d} {r['user'][:8]:<8}  {20 + nice:2d} {nice:3d} {r['virt']:7d} {r['res']:6d} "
                             f"{shared:6d} {r['stat'][:1]} {cpus[id(r)]:5.1f} {r['mem']:5.1f} "
                             f"{tpot_top_time(r['time']):>9} {comm}")
        return lines


def tpot_top_meminfo(fs) -> dict:
    """The kB figures of the emulated /proc/meminfo (as free reads them)."""
    try:
        text = fs.file_contents("/proc/meminfo").decode("utf-8", "replace")
    except Exception:
        text = ""
    values = {}
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        try:
            values[name.strip()] = int(rest.split()[0])
        except (IndexError, ValueError):
            pass
    return values


commands["/usr/bin/top"] = Command_top
commands["top"] = Command_top
'''


def patch_ps_top(cowrie_root):
    """ps cuts its lines as procps does (PS_WINDOW): at the width of the terminal, not when its
    output goes to a pipe or a file (ps aux | grep sshd finds the COMMAND); top of the persona
    (TOP_COMMAND) and the load average of w. Runs after patch_protocol_load."""
    path = cowrie_root / "src" / "cowrie" / "commands" / "base.py"
    replace_once(path, "import getopt\nimport random\nimport re\n", "import getopt\nimport hashlib\nimport random\nimport re\n")
    replace_once(path, "from cowrie.core import utils\n", "from cowrie.core import utils\nfrom cowrie.core.config import CowrieConfig\n")
    replace_once(path, "\n\nclass Command_ps(HoneyPotCommand):\n", PS_WINDOW + "\n\nclass Command_ps(HoneyPotCommand):\n")
    replace_once(
        path,
        '''            s = "".join([output_array[i][x] for x in line])
            if "w" not in args:
                s = s[: self.columns()]
            self.write(f"{s}\\n")
''',
        '''            s = "".join([output_array[i][x] for x in line])
            # T-Pot: cut as procps does (tpot_ps_width)
            width = tpot_ps_width(self, args)
            if width:
                s = s[:width]
            self.write(f"{s}\\n")
''',
    )
    replace_once(path, 'commands["/bin/ps"] = Command_ps\ncommands["ps"] = Command_ps\n',
                 'commands["/bin/ps"] = Command_ps\ncommands["ps"] = Command_ps\n' + TOP_COMMAND)
    replace_once(
        path,
        '''        self.write(
            f" {time.strftime('%H:%M:%S')} up {utils.uptime(self.protocol.uptime())},  1 user,  load average: 0.00, 0.00, 0.00\\n"
        )
''',
        '''        # T-Pot: the uptime line of procps, the load average of top
        self.write(self.protocol.tpot_uptime_line() + "\\n")
''',
    )


# module code of the patched fs.py, before Command_grep
GREP_MATCHER = r'''
class TpotGrepMatcher:
    """T-Pot: whether a line has a match of a grep pattern in Python's re syntax (the
    lines re.search selects). The parsed pattern becomes an automaton that reads each
    byte of a line once, so the time grows with the input and not with the ways a
    pattern can repeat. Back references, lookarounds, conditionals, atomic groups and
    possessive repeats select no line. The budget limits the work of building automaton
    states for one grep; when it is used up, `spent` is set and grep stops as GNU grep does
    when its memory runs out."""

    MAX_PATTERN = 64 * 1024  # characters, a cap before parsing; the automaton limit comes first
    MAX_INPUT = 1024 * 1024  # bytes one grep reads
    MAX_STATES = 4096  # states of the automaton of a pattern
    MAX_CACHE = 16384  # cached automaton states and transitions
    BUDGET = 4_000_000
    # 1: a word byte of a bytes pattern, 2: any other byte, 0: no byte (start or end of the line)
    CLASS = bytes(1 if 48 <= b <= 57 or 65 <= b <= 90 or 97 <= b <= 122 or b == 95 else 2 for b in range(256))
    # whether \B matches an empty line (it does from Python 3.14 on)
    EMPTY_NON_BOUNDARY = re.search(rb"\B", b"") is not None

    class TooBig(Exception):
        pass

    def __init__(self, pattern: bytes, ignore_case: bool = False) -> None:
        import warnings
        from re import _constants as sre
        from re import _parser

        self.sre = sre
        self.kind: list[int] = []  # 0 reads a byte of the set arg, 1 goes to out1 and out2, 2 tests arg, 3 accepts
        self.arg: list = []
        self.out1: list[int] = []
        self.out2: list[int] = []
        self.tables: dict = {}
        self.budget = self.BUDGET
        self.spent = False
        self.context = False
        self.start = -1
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # re.compile has given them
            parsed = _parser.parse(pattern, re.IGNORECASE if ignore_case else 0)
        flags = parsed.state.flags
        self.regular = not flags & re.LOCALE
        if self.regular:
            try:
                self.start = self.seq(parsed, self.new(3, None, -1), bool(flags & re.IGNORECASE))
            except LookupError:
                self.regular = False
        self.reset()

    def new(self, kind: int, arg, out1: int, out2: int = -1) -> int:
        if len(self.kind) >= self.MAX_STATES:
            raise self.TooBig()
        self.kind.append(kind)
        self.arg.append(arg)
        self.out1.append(out1)
        self.out2.append(out2)
        return len(self.kind) - 1

    def table(self, codes: set, negate: bool, ignore_case: bool) -> bytes:
        if ignore_case:
            codes = codes | {c ^ 32 for c in codes if 65 <= (c & ~32) <= 90}
        key = (frozenset(codes), negate)
        found = self.tables.get(key)
        if found is None:
            found = self.tables[key] = bytes((b in codes) != negate for b in range(256))
        return found

    def category(self, name) -> set:
        sre = self.sre
        sets = {sre.CATEGORY_DIGIT: set(range(48, 58)), sre.CATEGORY_SPACE: {9, 10, 11, 12, 13, 32},
                sre.CATEGORY_WORD: {b for b in range(256) if self.CLASS[b] == 1}}
        opposite = {sre.CATEGORY_NOT_DIGIT: sre.CATEGORY_DIGIT, sre.CATEGORY_NOT_SPACE: sre.CATEGORY_SPACE,
                    sre.CATEGORY_NOT_WORD: sre.CATEGORY_WORD}
        if name in opposite:
            return set(range(256)) - sets[opposite[name]]
        return sets[name]

    def seq(self, items, nxt: int, ignore_case: bool) -> int:
        """The first state of items, which go on to the state nxt (built from the end)."""
        for op, av in reversed(list(items)):
            nxt = self.item(op, av, nxt, ignore_case)
        return nxt

    def item(self, op, av, nxt: int, ignore_case: bool) -> int:
        sre = self.sre
        if op is sre.LITERAL or op is sre.NOT_LITERAL:
            return self.new(0, self.table({av}, op is sre.NOT_LITERAL, ignore_case), nxt)
        if op is sre.ANY:
            return self.new(0, self.table({10}, True, False), nxt)
        if op is sre.IN:
            codes, negate = set(), False
            for kind, value in av:
                if kind is sre.NEGATE:
                    negate = True
                elif kind is sre.LITERAL:
                    codes.add(value)
                elif kind is sre.RANGE:
                    codes.update(range(value[0], value[1] + 1))
                elif kind is sre.CATEGORY:
                    codes |= self.category(value)
                else:
                    raise LookupError(kind)
            return self.new(0, self.table(codes, negate, ignore_case), nxt)
        if op is sre.MAX_REPEAT or op is sre.MIN_REPEAT:
            low, high, body = av
            start = nxt
            if high == sre.MAXREPEAT:
                start = self.new(1, None, -1, nxt)
                self.out1[start] = self.seq(body, start, ignore_case)
            else:
                for _ in range(high - low):
                    start = self.new(1, None, self.seq(body, start, ignore_case), nxt)
            for _ in range(low):
                start = self.seq(body, start, ignore_case)
            return start
        if op is sre.SUBPATTERN:
            _, add, remove, body = av
            if (add | remove) & re.LOCALE:
                raise LookupError(op)
            ignore_case = (ignore_case or bool(add & re.IGNORECASE)) and not remove & re.IGNORECASE
            return self.seq(body, nxt, ignore_case)
        if op is sre.BRANCH:
            starts = [self.seq(alternative, nxt, ignore_case) for alternative in av[1]]
            start = starts.pop()
            while starts:
                start = self.new(1, None, starts.pop(), start)
            return start
        if op is sre.AT:
            test = {sre.AT_BEGINNING: 0, sre.AT_BEGINNING_STRING: 0, sre.AT_END: 1, sre.AT_END_STRING: 1,
                    sre.AT_BOUNDARY: 2, sre.AT_NON_BOUNDARY: 3}[av]
            self.context = True
            return self.new(2, test, nxt)
        raise LookupError(op)

    def reset(self) -> None:
        self.ids: dict = {}
        self.consume: list = []
        self.accepting: list[bool] = []
        self.initial: dict = {}
        self.trans: dict = {}
        self.stored = 0

    def holds(self, test: int, prev: int, nxt: int) -> bool:
        if test == 0:
            return prev == 0
        if test == 1:
            return nxt == 0
        if prev == 0 and nxt == 0:
            return test == 3 and self.EMPTY_NON_BOUNDARY
        return ((prev == 1) != (nxt == 1)) == (test == 2)

    def state(self, kernel, prev: int, nxt: int) -> int | None:
        """The automaton state for the states kernel between a byte of class prev and
        one of class nxt; None when the budget is used up."""
        kind, arg, out1, out2 = self.kind, self.arg, self.out1, self.out2
        stack, seen, consume, accept = list(kernel), set(), [], False
        while stack:
            s = stack.pop()
            if s in seen:
                continue
            seen.add(s)
            k = kind[s]
            if k == 0:
                consume.append(s)
            elif k == 1:
                stack.append(out1[s])
                if out2[s] >= 0:
                    stack.append(out2[s])
            elif k == 2:
                if self.holds(arg[s], prev, nxt):
                    stack.append(out1[s])
            else:
                accept = True
        self.budget -= len(seen) + len(kernel)
        if self.budget < 0:
            self.spent = True
            return None
        key = (tuple(sorted(consume)), accept)
        found = self.ids.get(key)
        if found is None:
            if len(self.consume) >= self.MAX_CACHE or self.stored > 4 * self.MAX_CACHE:
                self.reset()
            found = self.ids[key] = len(self.consume)
            self.consume.append(key[0])
            self.accepting.append(accept)
            self.stored += len(key[0])
        return found

    def search(self, line: bytes) -> bool | None:
        """True when the line has a match, else None."""
        if not self.regular or self.spent:
            return None
        n = len(line)
        context, cls = self.context, self.CLASS
        nc = cls[line[0]] if n and context else 0
        d = self.initial.get(nc)
        if d is None:
            d = self.state((self.start,), 0, nc)
            if d is None:
                return None
            self.initial[nc] = d
        accepting, trans, start, arg, out1 = self.accepting, self.trans, self.start, self.arg, self.out1
        if accepting[d]:
            return True
        for i in range(n):
            b = line[i]
            if context:
                nc = cls[line[i + 1]] if i + 1 < n else 0
            key = (d << 10) | (b << 2) | nc
            nd = trans.get(key)
            if nd is None:
                kernel = [out1[s] for s in self.consume[d] if arg[s][b]]
                kernel.append(start)
                if len(trans) >= self.MAX_CACHE:
                    trans.clear()
                nd = self.state(kernel, cls[b] if context else 0, nc)
                if nd is None:
                    return None
                if trans is self.trans:
                    trans[key] = nd
                else:  # the cache was emptied, the state ids before are gone
                    trans, accepting = self.trans, self.accepting
            d = nd
            if accepting[d]:
                return True
        return None


'''


# (pattern, -i): every construct TpotGrepMatcher reads; grep_self_test compares it with re.search
GREP_SELF_TEST = (
    (rb"proc", False),  # literal
    (rb"[^a]b", False),  # not-literal
    (rb"m.d", False),  # any
    (rb"[a-c]_", False),  # set with a range
    (rb"\d kB", False),  # category
    (rb"[^\s\d:]{3}", False),  # negated set with categories and a literal
    (rb"\w\W\S", False),  # word, non-word and non-space categories
    (rb"\D\d", False),  # non-digit category
    (rb"a+b", False),  # repeat
    (rb"a+?b", False),  # lazy repeat
    (rb"x{1,3}-", False),  # counted repeat
    (rb"(?i:MEM)Total", False),  # group with a flag added
    (rb"(?i)a(?-i:b)", False),  # group with a flag removed
    (rb"(foo|bar) ", False),  # alternation in a group
    (rb"^model", False),  # start of the line
    (rb"\Aproc", False),  # start of the string
    (rb"kB$", False),  # end of the line
    (rb"\d\Z", False),  # end of the string
    (rb"\bbar\b", False),  # word boundary
    (rb"a\Bb", False),  # no word boundary
    (rb"(a+)+$", False),  # nested repeats
    (rb"PROCESSOR", True),  # -i
)
GREP_SELF_TEST_LINES = (b"", b"processor\t: 0", b"model name\t: Intel", b"MemTotal: 8023548 kB", b"aaab",
                        b"Ab_c", b"foo bar", b"x-y", b" 42 ", b"ab", b"a b", b"amd64")


def grep_self_test(cowrie_root):
    """Runs TpotGrepMatcher of the patched fs.py in this Python: every pattern of
    GREP_SELF_TEST is read as a regular expression and selects the lines re.search
    selects, and an automaton over the state limit is too big. A Python whose re
    parser changed fails here, in the build, and not in every grep."""
    import ast
    import re

    path = cowrie_root / "src" / "cowrie" / "commands" / "fs.py"
    try:
        nodes = [node for node in ast.parse(path.read_text(encoding="utf-8")).body
                 if isinstance(node, ast.ClassDef) and node.name == "TpotGrepMatcher"]
        if len(nodes) != 1:
            raise LookupError("no TpotGrepMatcher in fs.py")
        namespace = {"re": re, "__name__": "tpot_grep_self_test"}
        exec(compile(ast.Module(nodes, []), str(path), "exec"), namespace)  # noqa: S102 (our own patched source)
        matcher = namespace["TpotGrepMatcher"]
        for pattern, ignore_case in GREP_SELF_TEST:
            found = matcher(pattern, ignore_case)
            if found.regular is not True:
                raise ValueError(f"{pattern!r} not read as a regular expression")
            expected = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
            for line in GREP_SELF_TEST_LINES:
                if (found.search(line) is not None) != (expected.search(line) is not None):
                    raise ValueError(f"{pattern!r} on {line!r} differs from re.search")
        try:
            matcher(rb"(a{100}){100}")
        except matcher.TooBig:
            pass
        else:
            raise ValueError("(a{100}){100} is not too big")
    except Exception as err:
        raise RuntimeError(f"grep matcher self-test failed: {err!r}") from err
    print(f"grep matcher self-test: {len(GREP_SELF_TEST)} patterns OK")


def patch_grep(cowrie_root):
    """grep as GNU grep for the options bots use: -c prints the number of selected
    lines (name:count per file for several files; `grep -c processor /proc/cpuinfo`),
    -v inverts, -i ignores case, -q prints nothing; -m counts per file. The exit
    status is 0 for a selected line, 1 for none and 2 for an error (missing file,
    directory, invalid option), with -q a selected line wins over an error.
    The empty string after the last newline is no line. Lines are matched by
    TpotGrepMatcher (GREP_MATCHER) in time that grows with the input: a pattern whose
    automaton gets too large is too big (exit 2), one grep reads at most 1 MiB, and
    when the work budget is used up grep stops with "memory exhausted" (exit 2).
    The Dockerfile runs grep_self_test (--self-test) on the patched source."""
    path = cowrie_root / "src" / "cowrie" / "commands" / "fs.py"
    replace_once(path, "class Command_grep(HoneyPotCommand):\n",
                 GREP_MATCHER.lstrip("\n") + "class Command_grep(HoneyPotCommand):\n")
    replace_once(
        path,
        """    def grep_get_contents(self, filename: str, match: str) -> None:
        try:
            contents = self.fs.file_contents(filename)
            self.grep_application(contents, match)
        except Exception:
            self.errorWrite(f"grep: {filename}: No such file or directory\\n")
""",
        """    count_only: bool = False
    invert: bool = False
    tpot_matcher: TpotGrepMatcher | None = None
    tpot_input_left: int = TpotGrepMatcher.MAX_INPUT
    tpot_exhausted: bool = False
    ignore_case: bool = False
    quiet: bool = False
    failed: bool = False

    def write_count(self, label: str | None = None) -> None:
        if self.quiet or self.tpot_exhausted:
            return
        prefix = f"{label}:" if label is not None else ""
        self.write(f"{prefix}{self.match_count}\\n")

    def exit_status(self) -> int:
        if self.tpot_exhausted:
            return 2
        if self.matched and (self.quiet or not self.failed):
            return 0
        return 2 if self.failed else 1

    def grep_get_contents(
        self, filename: str, match: str, label: str | None = None, show_name: bool = False
    ) -> None:
        name = filename if label is None else label
        try:
            contents = self.fs.file_contents(filename)
            self.match_count = 0
            self.grep_application(contents, match)
        except Exception:
            self.errorWrite(f"grep: {name}: No such file or directory\\n")
            self.failed = True
            return
        if self.count_only:
            self.write_count(name if show_name else None)
""",
    )
    replace_once(
        path,
        """    def compile_match(self, match: str) -> re.Pattern[bytes]:
        bmatch = os.path.basename(match).replace('"', "").encode("utf8")
        return re.compile(bmatch)
""",
        """    def compile_match(self, match: str) -> TpotGrepMatcher:
        if self.tpot_matcher is None:
            if len(match) > TpotGrepMatcher.MAX_PATTERN:
                raise TpotGrepMatcher.TooBig()
            bmatch = os.path.basename(match).replace('"', "").encode("utf8")
            try:
                re.compile(bmatch, re.IGNORECASE if self.ignore_case else 0)
                self.tpot_matcher = TpotGrepMatcher(bmatch, self.ignore_case)
            except RecursionError:
                raise TpotGrepMatcher.TooBig() from None
        return self.tpot_matcher
""",
    )
    replace_once(
        path,
        """        for line in contents.split(b"\\n"):
            if self.max_count is not None and self.match_count >= self.max_count:
                break
            if matcher.search(line):
                self.matched = True
                self.match_count += 1
                self.writeBytes(line + b"\\n")
""",
        """        if self.tpot_exhausted or (self.quiet and self.matched):
            return
        contents = contents[: self.tpot_input_left]
        self.tpot_input_left -= len(contents)
        lines = contents.split(b"\\n")
        if lines[-1] == b"":
            lines.pop()
        for line in lines:
            if self.max_count is not None and self.match_count >= self.max_count:
                break
            found = matcher.search(line) is not None
            if matcher.spent:
                self.errorWrite("grep: memory exhausted\\n")
                self.tpot_exhausted = True
                return
            if found != self.invert:
                self.matched = True
                self.match_count += 1
                if self.quiet:
                    return
                if not self.count_only:
                    self.writeBytes(line + b"\\n")
""",
    )
    replace_once(
        path,
        """            self.errorWrite(f"grep: invalid option -- {err.opt}\\n")
            self.help()
            self.exit()
""",
        """            self.errorWrite(f"grep: invalid option -- {err.opt}\\n")
            self.help()
            self.exit(2)
""",
    )
    replace_once(
        path,
        """        for opt, arg in optlist:
            if opt == "-h":
""",
        """        for opt, arg in optlist:
            if opt == "-c":
                self.count_only = True
            elif opt == "-v":
                self.invert = True
            elif opt == "-i":
                self.ignore_case = True
            elif opt == "-q":
                self.quiet = True
            elif opt == "-h":
""",
    )
    replace_once(
        path,
        """        if self.input_data is not None:
            self.grep_application(self.input_data, self.match)
        elif files:
            for pname in self.check_arguments("grep", files):
                self.grep_get_contents(pname, self.match)
""",
        """        if self.input_data is not None:
            self.grep_application(self.input_data, self.match)
            if self.count_only:
                self.write_count()
        elif files:
            for arg in files:
                if self.tpot_exhausted:
                    break
                pnames = self.check_arguments("grep", [arg])
                if not pnames:
                    self.failed = True
                for pname in pnames:
                    self.grep_get_contents(pname, self.match, arg, len(files) > 1)
""",
    )
    replace_once(
        path,
        """            self.compile_match(self.match)
        except re.error:
""",
        """            self.compile_match(self.match)
        except TpotGrepMatcher.TooBig:
            self.errorWrite("grep: regular expression too big\\n")
            self.exit(2)
            return
        except re.error:
""",
    )
    replace_once(
        path,
        """            self.interactive = True
            return

        self.exit(0 if self.matched else 1)
""",
        """            self.interactive = True
            return

        self.exit(self.exit_status())
""",
    )
    replace_once(
        path,
        """            self.grep_application(line.encode("utf8"), self.match)
""",
        """            self.grep_application((line + "\\n").encode("utf8"), self.match)
""",
    )
    replace_once(
        path,
        """                    self.grep_application(f.read(), self.match)
        self.exit(0 if self.matched else 1)
""",
        """                    self.grep_application(f.read(self.tpot_input_left), self.match)
            if self.count_only:
                self.write_count()
        self.exit(self.exit_status())
""",
    )


# module code of the patched commands/fs.py after grep: df of the persona
DF_COMMAND = r'''

# T-Pot: df of the persona. The file systems are those of [shell] df_table (a JSON file of the
# runtime folder, the table mount and /proc/mounts show); the format is that of GNU coreutils
# or of the persona's BusyBox (its version decides the options). Nothing of the host is read.
TPOT_DF_FIELDS = ("source", "fstype", "itotal", "iused", "iavail", "ipcent", "size", "used", "avail", "pcent",
                  "file", "target")
TPOT_DF_LEFT = frozenset({"source", "fstype", "file", "target"})
TPOT_DF_WIDTH = {"source": 14, "fstype": 4, "file": 4, "size": 5, "used": 5, "avail": 5, "pcent": 4,
                 "itotal": 5, "iused": 5, "iavail": 5, "ipcent": 5, "target": 0}
TPOT_DF_HEADER = {"source": "Filesystem", "fstype": "Type", "itotal": "Inodes", "iused": "IUsed", "iavail": "IFree",
                  "ipcent": "IUse%", "used": "Used", "avail": "Avail", "pcent": "Use%", "file": "File",
                  "target": "Mounted on"}
TPOT_DF_LONG = {"all": False, "block-size": True, "human-readable": False, "si": False, "inodes": False,
                "local": False, "no-sync": False, "sync": False, "output": None, "portability": False,
                "total": False, "type": True, "print-type": False, "exclude-type": True, "help": False,
                "version": False, "direct": False}
TPOT_DF_SHORT_LONG = {"a": "all", "B": "block-size", "h": "human-readable", "H": "si", "i": "inodes", "l": "local",
                      "P": "portability", "t": "type", "T": "print-type", "x": "exclude-type"}
TPOT_DF_POWERS = "KMGTPEZY"
TPOT_DF_TRY = "Try 'df --help' for more information.\n"


@functools.cache
def tpot_df_data(path: str) -> dict:
    """The table of [shell] df_table; without one a GNU df of one disk."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8")) if path else None
    except (OSError, ValueError):
        data = None
    if isinstance(data, dict) and isinstance(data.get("filesystems"), list):
        return data
    return {"style": "gnu", "coreutils": "9.1", "coreutils_year": "2022", "filesystems": [
        {"source": "/dev/sda1", "target": "/", "fstype": "ext4", "options": "rw,relatime", "size": 20134592,
         "used": 4180336, "avail": 14905244, "inodes": 1258291, "iused": 136754}]}


def tpot_df_human(amount: int, base: int, suppress_zero: bool = False) -> str:
    """human_readable of gnulib with autoscale and SI letters, rounded up (as df rounds)."""
    if amount < base:
        return str(amount)
    tenths = rounding = exponent = 0
    while base <= amount and exponent < len(TPOT_DF_POWERS):
        r10 = (amount % base) * 10 + tenths
        r2 = (r10 % base) * 2 + (rounding >> 1)
        amount //= base
        tenths = r10 // base
        rounding = (1 if r2 else 0) if r2 < base else 2 + (base < r2)
        exponent += 1
    point = ""
    if amount < 10:
        if rounding:
            tenths, rounding = tenths + 1, 0
            if tenths == 10:
                amount, tenths = amount + 1, 0
        if amount < 10 and (tenths or not suppress_zero):
            point, tenths, rounding = f".{tenths}", 0, 0
    if tenths + rounding:
        amount += 1
        if amount == base and exponent < len(TPOT_DF_POWERS):
            exponent += 1
            amount, point = 1, "" if suppress_zero else ".0"
    letter = TPOT_DF_POWERS[exponent - 1] if exponent else ""
    if letter == "K" and base == 1000:
        letter = "k"
    return f"{amount}{point}{letter}"


def tpot_df_unit_name(block: int) -> str:
    """The unit in the size header of GNU df (1K, 1M, 1kB, 512B): in the base that divides the
    block size more exactly, with B for powers of 1000."""
    q1000 = q1024 = block
    while True:
        by1000, by1024 = q1000 % 1000 == 0, q1024 % 1024 == 0
        q1000, q1024 = q1000 // 1000, q1024 // 1024
        if not (by1000 and by1024):
            break
    binary = by1024 and not by1000
    return tpot_df_human(block, 1024 if binary else 1000, True) + ("" if binary else "B")


def tpot_df_block_size(text: str):
    """(bytes, suffix of the values or "") of a GNU --block-size argument, None if invalid."""
    match = re.fullmatch(r"([0-9]{0,18})([kKMGTPEZY]?)(iB|B)?", text)
    if not match or not (match.group(1) or match.group(2)) or (match.group(3) and not match.group(2)):
        return None
    number = int(match.group(1) or "1")
    letter = match.group(2).upper()
    base = 1000 if match.group(3) == "B" else 1024
    size = number * base ** (TPOT_DF_POWERS.index(letter) + 1 if letter else 0)
    if size <= 0:
        return None
    suffix = "" if match.group(1) or not letter else letter + (match.group(3) or "")
    if suffix == "K" and base == 1000:
        suffix = "kB"
    return size, suffix


def tpot_df_bb_human(value: int, block_size: int, unit: int) -> str:
    """make_human_readable_str of BusyBox: one decimal with suffixes when unit is 0, else value
    in units, rounded to the nearest."""
    if value == 0:
        return "0"
    if block_size > 1:
        value *= block_size
    if unit:
        return str((value + unit // 2) // unit)
    letters, frac, index = "KMGTPEZY", 0, 0
    if value < 1024:
        return str(value)
    while value >= 1024:
        frac = ((value % 1024) * 10 + 512) // 1024
        value //= 1024
        index += 1
    if frac >= 10:
        value, frac = value + 1, 0
    if block_size == 0:
        return f"{value + (frac >= 5)}{letters[index - 1]}"
    return f"{value}.{frac}{letters[index - 1]}"


class Command_df(HoneyPotCommand):
    """df of the persona: GNU coreutils (servers, devices with coreutils) or BusyBox."""

    def call(self) -> None:
        self.data = tpot_df_data(CowrieConfig.get("shell", "df_table", fallback=""))
        rows = [r for r in self.data.get("filesystems", []) if isinstance(r, dict)]
        self.rows = [dict(r, size=int(r.get("size", 0)), used=int(r.get("used", 0)),
                          avail=int(r.get("avail", 0)), inodes=int(r.get("inodes", 0)),
                          iused=int(r.get("iused", 0))) for r in rows]
        if self.data.get("style") == "busybox":
            self.exit_code = self.busybox(list(self.args))
        else:
            self.exit_code = self.gnu(list(self.args))

    # --- the file systems of the arguments -----------------------------------------------

    def row_of(self, arg: str):
        """The row df shows for an argument: the mounted device it names, else the mount
        point the path lies on (the last mount of a point wins); None for a missing path."""
        for row in reversed(self.rows):
            if row.get("source") == arg and arg.startswith("/"):
                return row
        path = self.fs.resolve_path(arg, self.cwd)
        if not self.fs.exists(path):
            return None
        best = None
        for row in self.rows:
            target = row.get("target", "")
            if target == "/" or path == target or path.startswith(target.rstrip("/") + "/"):
                if best is None or len(target) >= len(best.get("target", "")):
                    best = row
        return best

    # --- GNU coreutils -----------------------------------------------------------------

    def gnu_options(self, args: list[str]):
        """(options in order, operands) as getopt_long of glibc reads them; a str is the error."""
        options, operands = [], []
        index = 0
        while index < len(args):
            arg = args[index]
            index += 1
            if arg == "--":
                operands += args[index:]
                break
            if arg.startswith("--"):
                name, eq, value = arg[2:].partition("=")
                found = [n for n in TPOT_DF_LONG if n == name] or [n for n in TPOT_DF_LONG if n.startswith(name)]
                if len(found) != 1 or not name:
                    return f"df: unrecognized option '{arg}'\n"
                name = found[0]
                takes = TPOT_DF_LONG[name]
                if takes is False and eq:
                    return f"df: option '--{name}' doesn't allow an argument\n"
                if takes is True and not eq:
                    if index >= len(args):
                        return f"df: option '--{name}' requires an argument\n"
                    value, index = args[index], index + 1
                options.append((name, value if (eq or takes is True) else None))
            elif arg.startswith("-") and arg != "-":
                position = 1
                while position < len(arg):
                    letter = arg[position]
                    position += 1
                    if letter in "kmv":
                        options.append((letter, None))
                        continue
                    name = TPOT_DF_SHORT_LONG.get(letter)
                    if name is None:
                        return f"df: invalid option -- '{letter}'\n"
                    if TPOT_DF_LONG[name] is True:
                        value = arg[position:]
                        if not value:
                            if index >= len(args):
                                return f"df: option requires an argument -- '{letter}'\n"
                            value, index = args[index], index + 1
                        options.append((name, value))
                        break
                    options.append((name, None))
            else:
                operands.append(arg)
        return options, operands

    def gnu(self, args: list[str]) -> int:
        parsed = self.gnu_options(args)
        if isinstance(parsed, str):
            self.errorWrite(parsed + TPOT_DF_TRY)
            return 1
        options, operands = parsed
        human, block, suffix, inodes, posix, show_type, every, total = 0, 1024, "", False, False, False, False, False
        fields, types, excluded, given = None, set(), set(), []
        for name, value in options:
            if name == "direct" and not self.data.get("el"):
                self.errorWrite("df: unrecognized option '--direct'\n" + TPOT_DF_TRY)
                return 1
            if name in ("help", "version"):
                self.write(self.gnu_help() if name == "help" else self.gnu_version())
                return 0
            if name == "human-readable":
                human, suffix = 1024, ""
            elif name == "si":
                human, suffix = 1000, ""
            elif name == "k":
                human, block, suffix = 0, 1024, ""
            elif name == "m":
                human, block, suffix = 0, 1024 * 1024, ""
            elif name == "block-size":
                size = tpot_df_block_size(value)
                if size is None:
                    self.errorWrite(f"df: invalid --block-size argument '{value}'\n")
                    return 1
                human, (block, suffix) = 0, size
            elif name == "inodes":
                inodes = True
                given.append("-i")
            elif name == "portability":
                posix = True
                given.append("-P")
            elif name == "print-type":
                show_type = True
                given.append("-T")
            elif name == "all":
                every = True
            elif name == "total":
                total = True
            elif name == "type":
                types.add(value)
            elif name == "exclude-type":
                excluded.add(value)
            elif name == "output":
                if fields is None:
                    fields = []
                for field in (value.split(",") if value is not None else TPOT_DF_FIELDS):
                    if field not in TPOT_DF_FIELDS:
                        self.errorWrite(f"df: option --output: field '{field}' unknown\n" + TPOT_DF_TRY)
                        return 1
                    if field in fields:
                        self.errorWrite(f"df: option --output: field '{field}' used more than once\n" + TPOT_DF_TRY)
                        return 1
                    fields.append(field)
                given.append("--output")
        if fields is not None:
            other = next((option for option in given if option != "--output"), None)
            if other:
                self.errorWrite(f"df: options {other} and --output are mutually exclusive\n" + TPOT_DF_TRY)
                return 1
        both = types & excluded
        if both:
            self.errorWrite(f"df: file system type '{sorted(both)[0]}' both selected and excluded\n")
            return 1
        status = 0
        if operands:
            chosen = []
            for arg in operands:
                row = self.row_of(arg)
                if row is None:
                    self.errorWrite(f"df: {arg}: No such file or directory\n")
                    status = 1
                else:
                    chosen.append((row, arg))
        else:
            chosen = [(row, "-") for row in self.rows if every or row["size"]]
        chosen = [(row, arg) for row, arg in chosen if (not types or row.get("fstype") in types)
                  and row.get("fstype") not in excluded]
        if not chosen:
            if status == 0:
                self.errorWrite("df: no file systems processed\n")
            return 1
        mode = "output" if fields is not None else "inode" if inodes else "human" if human else \
            "posix" if posix else "default"
        if fields is None:
            fields = ["source"] + (["fstype"] if show_type else [])
            fields += ["itotal", "iused", "iavail", "ipcent"] if inodes else ["size", "used", "avail", "pcent"]
            fields.append("target")
        header = []
        for field in fields:
            if field == "size":
                header.append("Size" if human else f"{block}-blocks" if mode == "posix" else
                              f"{tpot_df_unit_name(block)}-blocks")
            elif field == "avail" and mode in ("default", "posix"):
                header.append("Available")
            elif field == "pcent" and mode == "posix":
                header.append("Capacity")
            else:
                header.append(TPOT_DF_HEADER[field])
        table = [header]
        if total:
            sums = {key: sum(row[key] for row, _ in chosen) for key in ("size", "used", "avail", "inodes", "iused")}
            chosen.append((dict(sums, source="total", target="-", fstype="-"), "-"))
        for row, arg in chosen:
            table.append([self.gnu_cell(field, row, arg, human, block, suffix, total and row.get("source") == "total"
                                        and row.get("target") == "-") for field in fields])
        widths = [max([TPOT_DF_WIDTH[field]] + [len(line[col]) for line in table]) for col, field in enumerate(fields)]
        for line in table:
            cells = []
            for col, field in enumerate(fields):
                if col == len(fields) - 1:
                    cells.append(line[col] if field in TPOT_DF_LEFT else line[col].rjust(widths[col]))
                elif field in TPOT_DF_LEFT:
                    cells.append(line[col].ljust(widths[col]))
                else:
                    cells.append(line[col].rjust(widths[col]))
            self.write(" ".join(cells) + "\n")
        return status

    def gnu_cell(self, field: str, row: dict, arg: str, human: int, block: int, suffix: str, is_total: bool) -> str:
        if field == "source":
            return str(row.get("source", ""))
        if field == "fstype":
            return str(row.get("fstype", "-"))
        if field == "target":
            return str(row.get("target", "-"))
        if field == "file":
            return arg
        if field in ("itotal", "iused", "iavail", "ipcent"):
            itotal, iused = row["inodes"], row["iused"]
            values = {"itotal": itotal, "iused": iused, "iavail": itotal - iused}
            if field == "ipcent":
                return f"{-(-iused * 100 // itotal)}%" if itotal else "-"
            return tpot_df_human(values[field], human) if human else str(values[field])
        size, used, avail = row["size"], row["used"], row["avail"]
        if field == "pcent":
            return f"{-(-used * 100 // (used + avail))}%" if used + avail else "-"
        value = {"size": size, "used": used, "avail": avail}[field] * 1024
        if human:
            return tpot_df_human(value, human)
        return f"{-(-value // block)}{suffix}"

    def gnu_help(self) -> str:
        el = bool(self.data.get("el"))
        nine = not str(self.data.get("coreutils", "9")).startswith("8")
        direct = "      --direct          show statistics for a file instead of mount point\n" if el else ""
        tail = ("      --help        display this help and exit\n"
                "      --version     output version information and exit\n") if nine else \
            ("      --help     display this help and exit\n"
             "      --version  output version information and exit\n")
        text = ("Usage: df [OPTION]... [FILE]...\n"
                "Show information about the file system on which each FILE resides,\n"
                "or all file systems by default.\n\n"
                "Mandatory arguments to long options are mandatory for short options too.\n"
                "  -a, --all             include pseudo, duplicate, inaccessible file systems\n"
                "  -B, --block-size=SIZE  scale sizes by SIZE before printing them; e.g.,\n"
                "                           '-BM' prints sizes in units of 1,048,576 bytes;\n"
                "                           see SIZE format below\n"
                f"{direct}"
                "  -h, --human-readable  print sizes in powers of 1024 (e.g., 1023M)\n"
                "  -H, --si              print sizes in powers of 1000 (e.g., 1.1G)\n"
                "  -i, --inodes          list inode information instead of block usage\n"
                "  -k                    like --block-size=1K\n"
                "  -l, --local           limit listing to local file systems\n"
                "      --no-sync         do not invoke sync before getting usage info (default)\n"
                "      --output[=FIELD_LIST]  use the output format defined by FIELD_LIST,\n"
                "                               or print all fields if FIELD_LIST is omitted.\n"
                "  -P, --portability     use the POSIX output format\n"
                "      --sync            invoke sync before getting usage info\n"
                "      --total           elide all entries insignificant to available space,\n"
                "                          and produce a grand total\n"
                "  -t, --type=TYPE       limit listing to file systems of type TYPE\n"
                "  -T, --print-type      print file system type\n"
                "  -x, --exclude-type=TYPE   limit listing to file systems not of type TYPE\n"
                "  -v                    (ignored)\n"
                f"{tail}\n"
                "Display values are in units of the first available SIZE from --block-size,\n"
                "and the DF_BLOCK_SIZE, BLOCK_SIZE and BLOCKSIZE environment variables.\n"
                "Otherwise, units default to 1024 bytes (or 512 if POSIXLY_CORRECT is set).\n\n"
                "The SIZE argument is an integer and optional unit (example: 10K is 10*1024).\n"
                "Units are K,M,G,T,P,E,Z,Y (powers of 1024) or KB,MB,... (powers of 1000).\n"
                "Binary prefixes can be used, too: KiB=K, MiB=M, and so on.\n\n"
                "FIELD_LIST is a comma-separated list of columns to be included.  Valid\n"
                "field names are: 'source', 'fstype', 'itotal', 'iused', 'iavail', 'ipcent',\n"
                "'size', 'used', 'avail', 'pcent', 'file' and 'target' (see info page).\n\n"
                "GNU coreutils online help: <https://www.gnu.org/software/coreutils/>\n"
                "Report any translation bugs to <https://translationproject.org/team/>\n"
                "Full documentation <https://www.gnu.org/software/coreutils/df>\n"
                "or available locally via: info '(coreutils) df invocation'\n")
        text = self.gnu_help_of_release(text)
        if str(self.data.get("collate", "c")).startswith("en"):
            # coreutils names no translation address in an English locale
            text = re.sub(r"Report (?:any|df) translation bugs to <[^>]+>\n", "", text)
        return text

    def gnu_help_of_release(self, text: str) -> str:
        """The help of coreutils before 8.29 (checked: 8.22 of CentOS 7, 8.26 of Debian 9)."""
        try:
            version = tuple(int(x) for x in str(self.data.get("coreutils", "9.1")).split(".")[:2])
        except ValueError:
            return text
        if version >= (8, 29):
            return text
        total = ("      --total           elide all entries insignificant to available space,\n"
                 "                          and produce a grand total\n")
        text = text.replace("Binary prefixes can be used, too: KiB=K, MiB=M, and so on.\n", "").replace(
            "GNU coreutils online help: <https://www.gnu.org/software/coreutils/>\n"
            "Report any translation bugs to <https://translationproject.org/team/>\n"
            "Full documentation <https://www.gnu.org/software/coreutils/df>\n",
            "GNU coreutils online help: <http://www.gnu.org/software/coreutils/>\n"
            "Report df translation bugs to <http://translationproject.org/team/>\n"
            "Full documentation at: <http://www.gnu.org/software/coreutils/df>\n")
        if version >= (8, 23):
            return text
        return text.replace(total, "").replace(
            "  -h, --human-readable  print sizes in powers of 1024 (e.g., 1023M)\n"
            "  -H, --si              print sizes in powers of 1000 (e.g., 1.1G)\n",
            "      --total           produce a grand total\n"
            "  -h, --human-readable  print sizes in human readable format (e.g., 1K 234M 2G)\n"
            "  -H, --si              likewise, but use powers of 1000 not 1024\n").replace(
            "The SIZE argument is an integer and optional unit (example: 10K is 10*1024).\n"
            "Units are K,M,G,T,P,E,Z,Y (powers of 1024) or KB,MB,... (powers of 1000).\n",
            "SIZE is an integer and optional unit (example: 10M is 10*1024*1024).  Units\n"
            "are K, M, G, T, P, E, Z, Y (powers of 1024) or KB, MB, ... (powers of 1000).\n").replace(
            "Full documentation at: <http://www.gnu.org/software/coreutils/df>\n"
            "or available locally via: info '(coreutils) df invocation'\n",
            "For complete documentation, run: info coreutils 'df invocation'\n")

    def gnu_version(self) -> str:
        version = str(self.data.get("coreutils", "9.1"))
        try:
            old = tuple(int(x) for x in version.split(".")[:2]) < (8, 27)
        except ValueError:
            old = False
        packager = self.data.get("packager")
        return (f"df (GNU coreutils) {version}\n" + (f"Packaged by {packager}\n" if packager else "")
                + f"Copyright (C) {self.data.get('coreutils_year', '2022')} Free Software Foundation, Inc.\n"
                f"License GPLv3+: GNU GPL version 3 or later <{'http' if old else 'https'}://gnu.org/licenses/gpl.html>.\n"
                "This is free software: you are free to change and redistribute it.\n"
                "There is NO WARRANTY, to the extent permitted by law.\n\n"
                "Written by Torbjorn Granlund, David MacKenzie, and Paul Eggert.\n")

    # --- BusyBox -----------------------------------------------------------------------

    def busybox_version(self) -> tuple:
        try:
            return tuple(int(x) for x in self.data.get("busybox", [1, 24, 1]))[:3]
        except (TypeError, ValueError):
            return (1, 24, 1)

    def busybox_option_error(self, option: str, kind: str) -> str:
        """The getopt message of the firmware's C library (glibc, uClibc, musl)."""
        libc = self.data.get("libc", "glibc")
        if kind == "long":
            return {"musl": f"df: unrecognized option: {option[2:].partition('=')[0]}\n",
                    "uclibc": f"df: unrecognized option `{option}'\n"}.get(libc, f"df: unrecognized option '{option}'\n")
        if kind == "argument":
            return {"musl": f"df: option requires an argument: {option}\n",
                    "uclibc": f"df: option requires an argument -- {option}\n"}.get(
                libc, f"df: option requires an argument -- '{option}'\n")
        return {"musl": f"df: unrecognized option: {option}\n", "uclibc": f"df: invalid option -- {option}\n"}.get(
            libc, f"df: invalid option -- '{option}'\n")

    def busybox_usage(self) -> str:
        version = self.busybox_version()
        banner = self.data.get("banner", "")
        # libbb of 1.14 prints no dot after "multi-call binary"
        head = (f"{banner} multi-call binary.\n\n" if version >= (1, 15) else f"{banner} multi-call binary\n\n") \
            if banner else ""
        if version < (1, 15):
            return (f"{head}Usage: df [-Pkmhai] [-B SIZE] [FILESYSTEM...]\n\n"
                    "Print filesystem usage statistics\n\nOptions:\n"
                    "\t-P\tPOSIX output format\n\t-k\t1024-byte blocks (default)\n\t-m\t1M-byte blocks\n"
                    "\t-h\tHuman readable (e.g. 1K 243M 2G)\n\t-a\tShow all filesystems\n\t-i\tInodes\n"
                    "\t-B SIZE\tBlocksize\n")
        flags = "PkmhTai" if version >= (1, 23) else "Pkmhai"
        text = f"{head}Usage: df [-{flags}] [-B SIZE]{' [-t TYPE]' if version >= (1, 34) else ''} [FILESYSTEM]...\n\n"
        text += ("Print filesystem usage statistics\n\n"
                 "\t-P\tPOSIX output format\n\t-k\t1024-byte blocks (default)\n\t-m\t1M-byte blocks\n"
                 "\t-h\tHuman readable (e.g. 1K 243M 2G)\n")
        if version >= (1, 23):
            text += "\t-T\tPrint filesystem type\n"
        if version >= (1, 34):
            text += "\t-t TYPE\tPrint only mounts of this type\n"
        return text + "\t-a\tShow all filesystems\n\t-i\tInodes\n\t-B SIZE\tBlocksize\n"

    def busybox(self, args: list[str]) -> int:
        version = self.busybox_version()
        letters = "kPaiBhm" + ("T" if version >= (1, 23) else "") + ("t" if version >= (1, 34) else "") + \
            ("H" if version >= (1, 35) else "")
        options, operands, index = [], [], 0
        while index < len(args):
            arg = args[index]
            index += 1
            if arg == "--":
                operands += args[index:]
                break
            if arg.startswith("--"):
                self.errorWrite(self.busybox_option_error(arg, "long") + self.busybox_usage())
                return 1
            if arg.startswith("-") and arg != "-":
                position = 1
                while position < len(arg):
                    letter = arg[position]
                    position += 1
                    if letter not in letters:
                        self.errorWrite(self.busybox_option_error(letter, "short") + self.busybox_usage())
                        return 1
                    if letter in "Bt":
                        value = arg[position:]
                        if not value:
                            if index >= len(args):
                                self.errorWrite(self.busybox_option_error(letter, "argument")
                                                + self.busybox_usage())
                                return 1
                            value, index = args[index], index + 1
                        options.append((letter, value))
                        break
                    options.append((letter, None))
            else:
                operands.append(arg)
        unit, human, posix, inodes, show_type, every, only = 1024, False, False, False, False, False, None
        for letter, value in options:
            if letter == "k":
                unit, human = 1024, False
            elif letter == "m":
                unit, human = 1024 * 1024, False
            elif letter == "B":
                size = tpot_df_block_size(value) if value else None
                if size is None or (version < (1, 27) and not value.isdigit()):
                    self.errorWrite(f"df: invalid number '{value}'\n")
                    return 1
                unit, human = size[0], False
            elif letter in "hH":
                human = True
            elif letter == "P":
                posix = True
            elif letter == "i":
                inodes = True
            elif letter == "T":
                show_type = True
            elif letter == "a":
                every = True
            elif letter == "t":
                only = value
        if human:
            units = "     Size"
        elif inodes:
            units = "   Inodes"
        else:
            units = f"{tpot_df_bb_human(unit, 0, 1 if posix else 0)}-blocks"
        self.write(f"Filesystem           {'Type       ' if show_type else ''}{units:<15}Used Available "
                   f"{'Capacity' if posix else 'Use%'} Mounted on\n")
        status = 0
        if operands:
            chosen = []
            for arg in operands:
                row = self.row_of(arg)
                if row is None:
                    self.errorWrite(f"df: {arg}: can't find mount point\n")
                    status = 1
                else:
                    chosen.append(row)
        else:
            chosen = [row for row in self.rows if (every or row["size"]) and row.get("source") != "rootfs"]
        for row in chosen:
            if only is not None and row.get("fstype") != only:
                continue
            if inodes:
                blocks, used, avail, block_size = row["inodes"], row["iused"], row["inodes"] - row["iused"], 1
                display = 0 if human else 1
            else:
                blocks, used, avail, block_size = row["size"], row["used"], row["avail"], 1024
                display = 0 if human else unit
            percent = (used * 100 + (used + avail) // 2) // (used + avail) if used + avail else 0
            device = str(row.get("source", ""))
            line = device.ljust(20)
            if len(device) > 20 and not posix:
                line = device + "\n" + " " * 20
            if show_type:
                fstype = f" {str(row.get('fstype', '')):<10}"
                line += fstype if len(fstype) <= 11 or posix else fstype + "\n" + " " * 30
            line += (f" {tpot_df_bb_human(blocks, block_size, display):>9} "
                     f"{tpot_df_bb_human(used, block_size, display):>9} "
                     f"{tpot_df_bb_human(avail, block_size, display):>9} {percent:>3}% {row.get('target', '')}\n")
            self.write(line)
        return status


commands["/bin/df"] = Command_df
commands["/usr/bin/df"] = Command_df
commands["df"] = Command_df
'''


def patch_df(cowrie_root):
    """df as a command over the persona's file system table (DF_COMMAND, in the module of grep):
    GNU coreutils or the persona's BusyBox, the same file systems as mount and /proc/mounts.
    Runs after patch_grep."""
    path = cowrie_root / "src" / "cowrie" / "commands" / "fs.py"
    replace_once(path, "import copy\nimport getopt\n", "import copy\nimport functools\nimport getopt\nimport json\n")
    replace_once(path, "from cowrie.shell import fs\n",
                 "from cowrie.core.config import CowrieConfig\nfrom cowrie.shell import fs\n")
    replace_once(path, "from typing import TYPE_CHECKING\n", "from pathlib import Path\nfrom typing import TYPE_CHECKING\n")
    replace_once(path, 'commands["/bin/fgrep"] = Command_grep\n', 'commands["/bin/fgrep"] = Command_grep\n' + DF_COMMAND)


# module code of the patched commands/awk.py after its commands: awk of the persona
AWK_COMMAND = r'''

# T-Pot: awk of the persona ([shell] awk_style gawk, mawk or busybox; awk_version). A small
# interpreter for the awk that scripts run on the output of df, free, uptime, ps or
# /proc/cpuinfo: BEGIN and END, patterns (expressions, /regex/), print and printf, if, while,
# for, next, exit, variables, fields and the usual operators, and length, substr, index,
# tolower, toupper, int, sprintf, sub and gsub. The program becomes a tree of tuples that is
# walked; nothing of it is executed. Regular expressions run on TpotGrepMatcher, the automaton
# of grep. Program, input, output, values, steps and nesting have limits; past one, awk ends
# with status 2. The awk this leaves out (arrays, functions, getline, system, pipes and
# redirections) ends awk with status 2 and no output; syntax errors read as those of the
# persona's awk.
import math
import time

from twisted.python import log

from cowrie.commands.fs import TpotGrepMatcher
from cowrie.core.config import CowrieConfig


class TpotAwkStop(Exception):
    """Ends awk with a status and a text for stderr (limits, run time errors, the awk left out)."""

    def __init__(self, code: int = 2, text: str = "") -> None:
        super().__init__(text)
        self.code, self.text = code, text


class TpotAwkSyntax(Exception):
    def __init__(self, token, braces: int = 0) -> None:
        super().__init__(token[4])
        self.token, self.braces = token, braces


class TpotAwkJump(Exception):
    """next, exit, break and continue."""

    def __init__(self, kind: str, code: int | None = None) -> None:
        super().__init__(kind)
        self.kind, self.code = kind, code


class TpotAwkStrnum(str):
    """A string of the input (a field, $0, -v): a number in comparisons when it looks like one."""


class TpotAwkUninit(TpotAwkStrnum):
    """The value of an unset variable or a missing field: 0 and "" at once."""


TPOT_AWK_UNINIT = TpotAwkUninit("")
TPOT_AWK_KEYWORDS = frozenset({"BEGIN", "END", "if", "else", "while", "for", "break", "continue", "next", "exit",
                               "print", "printf"})
# name: (fewest, most arguments)
TPOT_AWK_FUNCTIONS = {"length": (0, 1), "substr": (2, 3), "index": (2, 2), "tolower": (1, 1), "toupper": (1, 1),
                      "int": (1, 1), "sprintf": (1, 255), "sub": (2, 3), "gsub": (2, 3)}
# the awk left out: words of every awk, then gawk's functions (names of variables elsewhere)
TPOT_AWK_OTHER = frozenset({"function", "func", "getline", "delete", "in", "do", "return", "nextfile", "split",
                            "match", "system", "close", "fflush", "srand", "rand", "sin", "cos", "atan2", "exp",
                            "log", "sqrt"})
TPOT_AWK_GAWK = frozenset({"gensub", "strftime", "systime", "mktime", "asort", "asorti", "patsplit", "strtonum",
                           "and", "or", "xor", "lshift", "rshift", "compl", "isarray", "typeof"})
TPOT_AWK_PUNCT = ("&&", "||", "==", "!=", "<=", ">=", "!~", "++", "--", "+=", "-=", "*=", "/=", "%=", "^=", ">>",
                  "{", "}", "(", ")", ";", ",", "+", "-", "*", "/", "%", "^", "!", "<", ">", "~", "?", ":", "=",
                  "$", "[", "]", "|")
TPOT_AWK_ASSIGN = frozenset({"=", "+=", "-=", "*=", "/=", "%=", "^="})
TPOT_AWK_COMPARE = {"<": lambda x, y: x < y, "<=": lambda x, y: x <= y, ">": lambda x, y: x > y,
                    ">=": lambda x, y: x >= y, "==": lambda x, y: x == y, "!=": lambda x, y: x != y}
TPOT_AWK_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "/": "/", "a": "\a", "b": "\b",
                    "f": "\f", "v": "\v"}
# the regular expressions of the interpreter have one way to match a text (possessive, no
# alternatives that overlap), so their time grows with the text
TPOT_AWK_DECIMAL = r"(?:[0-9]++(?:\.[0-9]*+)?+|\.[0-9]++)(?:[eE][-+]?+[0-9]++)?+"
TPOT_AWK_NUMBER = re.compile(r"[ \t\n\r\f\v]*+([-+]?+" + TPOT_AWK_DECIMAL + ")")
TPOT_AWK_HEX = re.compile(r"[ \t\n\r\f\v]*+([-+]?+)0[xX]([0-9a-fA-F]++)")
TPOT_AWK_LOOKS = re.compile(r"[ \t\n\r\f\v]*+[-+]?+" + TPOT_AWK_DECIMAL + r"[ \t\n\r\f\v]*+")
TPOT_AWK_LITERAL = re.compile(TPOT_AWK_DECIMAL)
TPOT_AWK_BLANKS = re.compile(r"[ \t\n]++")
TPOT_AWK_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*+=")
TPOT_AWK_FORMAT = re.compile(r"%([-+ #0]*+)(\*|[0-9]++)?+(?:\.(\*|[0-9]*+))?+(.?)", re.S)
TPOT_AWK_INTERVAL = re.compile(r"\{[0-9]++(?:,[0-9]*+)?+\}")
TPOT_AWK_NON_ASCII = re.compile(r"[^\x00-\x7f]")
TPOT_AWK_AMP = re.compile(r"\\[\\&]|&")
TPOT_AWK_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
TPOT_AWK_UPPER = str.maketrans("abcdefghijklmnopqrstuvwxyz", "ABCDEFGHIJKLMNOPQRSTUVWXYZ")
TPOT_AWK_CLASSES = {"alpha": "a-zA-Z", "digit": "0-9", "alnum": "0-9a-zA-Z", "upper": "A-Z", "lower": "a-z",
                    "space": " \\t\\n\\r\\f\\v", "blank": " \\t", "punct": "!-/:-@\\[-`{-~", "xdigit": "0-9A-Fa-f",
                    "cntrl": "\\x00-\\x1f\\x7f", "print": " -~", "graph": "!-~"}


def tpot_awk_unescape(raw: str, gawk: bool) -> str:
    """The escape sequences of a string literal or -v value; gawk drops the backslash of an
    unknown one, mawk and BusyBox keep it."""
    def one(found):
        d = found.group(1)
        if d[0] in "01234567":
            return chr(int(d, 8) & 255)
        if d == "\n":
            return ""
        return TPOT_AWK_ESCAPES.get(d, d if gawk else "\\" + d)
    return re.sub(r"\\([0-7]{1,3}+|.)", one, raw, flags=re.S) if "\\" in raw else raw


def tpot_awk_bytes(text: str) -> bytes:
    """One byte per character (255 for any that is not ASCII), so match positions are characters."""
    if text.isascii():
        return text.encode("ascii")
    return TPOT_AWK_NON_ASCII.sub("\xff", text).encode("latin-1")


def tpot_awk_ere(source: str, intervals: bool) -> str:
    """An awk extended regular expression in the syntax of Python's re: bracket classes, braces as
    literals where the awk has no intervals, * + ? as literals where nothing precedes them."""
    out, i, n, start = [], 0, len(source), True
    while i < n:
        c = source[i]
        if c == "\\" and i + 1 < n:
            d = source[i + 1]
            out.append("\\" + d if d in "nrtfvsSwW" else re.escape(d))
            i, start = i + 2, False
            continue
        if c == "[":
            j, body = i + 1, []
            if source.startswith("^", j):
                body.append("^")
                j += 1
            if source.startswith("]", j):
                body.append("\\]")
                j += 1
            while j < n and source[j] != "]":
                if source.startswith("[:", j):
                    end = source.find(":]", j + 2)
                    name = source[j + 2:end] if end > 0 else ""
                    if name not in TPOT_AWK_CLASSES:
                        raise re.error("class")
                    body.append(TPOT_AWK_CLASSES[name])
                    j = end + 2
                elif source[j] == "\\" and j + 1 < n:
                    body.append("\\" + source[j + 1] if source[j + 1] in "nrtfv" else re.escape(source[j + 1]))
                    j += 2
                else:
                    body.append("\\" + source[j] if source[j] in "[&~|" else source[j])
                    j += 1
            if j >= n:
                raise re.error("unterminated [")
            out.append("[" + "".join(body) + "]")
            i, start = j + 1, False
            continue
        if c == "{":
            found = TPOT_AWK_INTERVAL.match(source, i) if intervals and not start else None
            if found:
                out.append(found.group(0))
                i, start = found.end(), False
                continue
            out.append("\\{")
        elif c in "*+?":
            out.append("\\" + c if start else c)
        elif c == "(" and source.startswith("(?", i):
            out.append("(\\?")
            i += 1
        elif c in "^$.|()":
            out.append(c)
        else:
            out.append(re.escape(c) if c.isascii() else "\\xff")
        start = c in "(|^"
        i += 1
    return "".join(out)


class TpotAwkRegex:
    """An awk regular expression on TpotGrepMatcher, in time that grows with the text: whether a
    text has a match, and the leftmost longest match (sub, gsub, FS)."""

    def __init__(self, awk, source: str) -> None:
        self.awk = awk
        try:
            pattern = tpot_awk_ere(source, awk.intervals)
            re.compile(pattern)
            self.matcher = TpotGrepMatcher("".join(c if c.isascii() else "\\xff" for c in pattern).encode("ascii"))
        except (re.error, RecursionError, OverflowError, TpotGrepMatcher.TooBig):
            raise TpotAwkStop(2) from None
        awk.charge(awk.STATE_STEPS * len(self.matcher.kind))
        if not self.matcher.regular:
            raise TpotAwkStop(2)

    def search(self, text: str) -> bool:
        """Whether text has a match; the automaton states it builds count as steps of awk."""
        data = tpot_awk_bytes(text)
        awk, matcher = self.awk, self.matcher
        awk.charge(len(data) >> 1)  # the automaton reads each byte once
        matcher.budget = budget = max(0, awk.MAX_STEPS - awk.steps) * 2
        found = matcher.search(data)
        if matcher.spent:
            raise TpotAwkStop(2)
        awk.charge((budget - matcher.budget) >> 1)
        return bool(found)

    def find(self, data: bytes, pos: int):
        """(start, end) of the leftmost longest match at or after pos, None for none."""
        m = self.matcher
        kind, arg, out1, out2, cls, holds = m.kind, m.arg, m.out1, m.out2, m.CLASS, m.holds
        n, best_start, best_end, threads, i = len(data), -1, -1, {}, pos
        while True:
            prev = cls[data[i - 1]] if i > 0 else 0
            nxt = cls[data[i]] if i < n else 0
            seeds = list(threads.items())
            if best_start < 0:
                seeds.append((m.start, i))
            seen, current = {}, {}
            for state, start in seeds:
                stack = [state]
                while stack:
                    s = stack.pop()
                    if s in seen:
                        continue
                    seen[s] = start
                    k = kind[s]
                    if k == 0:
                        current[s] = start
                    elif k == 1:
                        stack.append(out1[s])
                        if out2[s] >= 0:
                            stack.append(out2[s])
                    elif k == 2:
                        if holds(arg[s], prev, nxt):
                            stack.append(out1[s])
                    elif best_start < 0 or start < best_start or (start == best_start and i > best_end):
                        best_start, best_end = start, i
            self.awk.charge(8 * (len(seen) + 1))
            if best_start >= 0:
                current = {s: st for s, st in current.items() if st <= best_start}
            if i >= n or (not current and best_start >= 0):
                break
            b, threads = data[i], {}
            for s, st in current.items():
                if arg[s][b] and out1[s] not in threads:
                    threads[out1[s]] = st
            i += 1
        return (best_start, best_end) if best_start >= 0 else None


class TpotAwkLexer:
    """The tokens of a program: (kind, value, line, column, text); the kind is the punctuation or
    keyword itself, num, str, ere, name, func (a function here), call (any other f(), nl or eof."""

    def __init__(self, awk, text: str) -> None:
        self.awk, self.text = awk, text

    def tokens(self) -> list:
        text, n, out = self.text, len(self.text), []
        i, line, line_start = 0, 1, 0
        while True:
            while i < n:
                if text[i] in " \t\r":
                    i += 1
                elif text.startswith("\\\n", i):
                    i, line, line_start = i + 2, line + 1, i + 2
                elif text[i] == "#":
                    while i < n and text[i] != "\n":
                        i += 1
                else:
                    break
            col = i - line_start
            if i >= n:
                out.append(("eof", "", line, col, "end of file"))
                return out
            c = text[i]
            if c == "\n":
                out.append(("nl", "\n", line, col, "end of line"))
                i, line, line_start = i + 1, line + 1, i + 1
                continue
            if c == '"' or (c == "/" and self.regex_allowed(out)):
                j = i + 1
                bracket = False
                while j < n and text[j] != "\n" and (text[j] != c or bracket):
                    if text[j] == "\\" and j + 1 < n:
                        j += 1
                    elif c == "/" and text[j] in "[]":
                        bracket = text[j] == "["
                    j += 1
                if j >= n or text[j] != c:
                    raise TpotAwkSyntax(("str", "", line, col, text[i:j]))
                raw = text[i + 1:j]
                if c == '"':
                    out.append(("str", tpot_awk_unescape(raw, self.awk.style == "gawk"), line, col, text[i:j + 1]))
                else:
                    out.append(("ere", raw, line, col, text[i:j + 1]))
                i = j + 1
                continue
            found = TPOT_AWK_LITERAL.match(text, i) if c.isdigit() or c == "." else None
            if found:
                out.append(("num", float(found.group(0)), line, col, found.group(0)))
                i = found.end()
                continue
            if c.isascii() and (c.isalpha() or c == "_"):
                j = i
                while j < n and text[j].isascii() and (text[j].isalnum() or text[j] == "_"):
                    j += 1
                word, call = text[i:j], text.startswith("(", j)
                if word in TPOT_AWK_OTHER or (word in TPOT_AWK_GAWK and call):
                    raise TpotAwkStop(2)
                kind = word if word in TPOT_AWK_KEYWORDS else "func" if word in TPOT_AWK_FUNCTIONS \
                    else "call" if call else "name"
                out.append((kind, word, line, col, word))
                i = j
                continue
            punct = next((p for p in TPOT_AWK_PUNCT if text.startswith(p, i)), None)
            if punct is None:
                raise TpotAwkSyntax(("?", c, line, col, c))
            if punct in ("[", "]", "|", ">>"):  # arrays, pipes, appending output
                raise TpotAwkStop(2)
            out.append((punct, punct, line, col, punct))
            i += len(punct)

    @staticmethod
    def regex_allowed(out: list) -> bool:
        """Whether a / starts a regular expression (not a division after an operand)."""
        if not out:
            return True
        kind = out[-1][0]
        return kind not in ("num", "str", "ere", "name", "func", ")", "++", "--", "$")


class TpotAwkParser:
    """Recursive descent over the tokens. Rules are (kind, pattern, action) with the kind BEGIN,
    END or main; statements and expressions are tuples whose first item names them."""

    TERMINATORS = ("nl", ";", "}", "eof")

    def __init__(self, awk, tokens: list) -> None:
        self.awk, self.tokens, self.pos = awk, tokens, 0
        self.depth = self.braces = 0
        self.no_gt = False  # in the arguments of print, > is a redirection
        self.undefined = None  # the first call of a function no program here defines

    def peek(self):
        return self.tokens[self.pos]

    def kind(self) -> str:
        return self.tokens[self.pos][0]

    def take(self, kind: str | None = None):
        token = self.tokens[self.pos]
        if kind is not None and token[0] != kind:
            raise TpotAwkSyntax(token, self.braces)
        if token[0] != "eof":
            self.pos += 1
        return token

    def fail(self):
        return TpotAwkSyntax(self.peek(), self.braces)

    def newlines(self) -> None:
        while self.kind() == "nl":
            self.pos += 1

    def terms(self) -> None:
        while self.kind() in ("nl", ";"):
            self.pos += 1

    def nested(self, method, *args):
        self.depth += 1
        if self.depth > self.awk.MAX_NESTING:
            raise TpotAwkStop(2)
        try:
            return method(*args)
        finally:
            self.depth -= 1

    def program(self) -> list:
        rules = []
        self.terms()
        while self.kind() != "eof":
            kind = self.kind()
            if kind in ("BEGIN", "END"):
                self.take()
                if self.kind() != "{":
                    raise self.fail()
                rules.append((kind, None, self.block()))
            elif kind == "{":
                rules.append(("main", None, self.block()))
            else:
                pattern = self.expr()
                if self.kind() == ",":  # range patterns
                    raise TpotAwkStop(2)
                if self.kind() == "{":
                    rules.append(("main", pattern, self.block()))
                elif self.kind() in ("nl", ";", "eof"):
                    rules.append(("main", pattern, None))
                else:
                    raise self.fail()
            self.terms()
        return rules

    def block(self):
        self.take("{")
        self.braces += 1
        statements = []
        self.terms()
        while self.kind() != "}":
            if self.kind() == "eof":
                raise self.fail()
            statements.append(self.nested(self.statement))
            self.terms()
        self.take("}")
        self.braces -= 1
        return ("block", statements)

    def statement(self):
        kind = self.kind()
        if kind == "{":
            return self.block()
        if kind == ";":
            self.take()
            return ("block", [])
        if kind == "if":
            self.take()
            cond = self.condition()
            then, other = self.nested(self.statement), None
            mark = self.pos
            self.terms()
            if self.kind() == "else":
                self.take()
                self.newlines()
                other = self.nested(self.statement)
            else:
                self.pos = mark
            return ("if", cond, then, other)
        if kind == "while":
            self.take()
            cond = self.condition()
            return ("while", cond, self.nested(self.statement))
        if kind == "for":
            self.take()
            self.take("(")
            init = None if self.kind() == ";" else self.simple_expr()
            self.take(";")
            self.newlines()
            cond = None if self.kind() == ";" else self.expr()
            self.take(";")
            self.newlines()
            step = None if self.kind() == ")" else self.simple_expr()
            self.take(")")
            self.newlines()
            return ("for", init, cond, step, self.nested(self.statement))
        statement = self.simple()
        if self.kind() not in self.TERMINATORS:
            raise self.fail()
        return statement

    def condition(self):
        self.take("(")
        cond = self.expr()
        self.take(")")
        self.newlines()
        return cond

    def simple_expr(self):
        old, self.no_gt = self.no_gt, False
        try:
            return self.expr()
        finally:
            self.no_gt = old

    def simple(self):
        kind = self.kind()
        if kind in ("print", "printf"):
            self.take()
            args = self.print_args()
            if self.kind() in (">", "|"):  # output to a file or a command
                raise TpotAwkStop(2)
            if kind == "printf" and not args:
                raise self.fail()
            return (kind, args)
        if kind in ("next", "break", "continue"):
            self.take()
            return (kind,)
        if kind == "exit":
            self.take()
            return ("exit", None if self.kind() in self.TERMINATORS else self.expr())
        return ("expr", self.expr())

    def print_args(self) -> list:
        if self.kind() in self.TERMINATORS or self.kind() in (">", "|"):
            return []
        if self.kind() == "(":  # print (a, b) or print (a) b
            mark = self.pos
            self.take()
            items = self.nested(self.expr_list, False)
            if self.kind() == ")":
                self.take()
                if self.kind() in self.TERMINATORS or self.kind() in (">", "|"):
                    return items
            self.pos = mark
        return self.expr_list(True)

    def expr_list(self, no_gt: bool) -> list:
        old, self.no_gt = self.no_gt, no_gt
        try:
            items = [self.expr()]
            while self.kind() == ",":
                self.take()
                self.newlines()
                items.append(self.expr())
            return items
        finally:
            self.no_gt = old

    def expr(self):
        left = self.ternary()
        if self.kind() in TPOT_AWK_ASSIGN:
            if left[0] not in ("var", "field"):
                raise self.fail()
            op = self.take()[0]
            return ("assign", op, left, self.nested(self.expr))
        return left

    def ternary(self):
        cond = self.binary_or()
        if self.kind() != "?":
            return cond
        self.take()
        self.newlines()
        then = self.nested(self.ternary)
        self.take(":")
        self.newlines()
        return ("cond", cond, then, self.nested(self.ternary))

    def binary_or(self):
        left = self.binary_and()
        while self.kind() == "||":
            self.take()
            self.newlines()
            left = ("or", left, self.binary_and())
        return left

    def binary_and(self):
        left = self.match()
        while self.kind() == "&&":
            self.take()
            self.newlines()
            left = ("and", left, self.match())
        return left

    def match(self):
        left = self.comparison()
        while self.kind() in ("~", "!~"):
            negate = self.take()[0] == "!~"
            left = ("match", negate, left, self.comparison())
        return left

    def comparison(self):
        left = self.concat()
        kind = self.kind()
        if kind in TPOT_AWK_COMPARE and not (kind == ">" and self.no_gt):
            self.take()
            return ("cmp", kind, left, self.concat())
        return left

    def concat(self):
        left = self.additive()
        while self.kind() in ("num", "str", "ere", "name", "func", "call", "$", "(", "++", "--"):
            left = ("cat", left, self.additive())
        return left

    def additive(self):
        left = self.multiplicative()
        while self.kind() in ("+", "-"):
            op = self.take()[0]
            left = ("bin", op, left, self.multiplicative())
        return left

    def multiplicative(self):
        left = self.unary()
        while self.kind() in ("*", "/", "%"):
            op = self.take()[0]
            left = ("bin", op, left, self.unary())
        return left

    def unary(self):
        kind = self.kind()
        if kind in ("!", "-", "+"):
            self.take()
            return ({"!": "not", "-": "neg", "+": "plus"}[kind], self.nested(self.unary))
        return self.power()

    def power(self):
        base = self.postfix()
        if self.kind() == "^":
            self.take()
            if self.awk.style == "busybox":  # its ^ groups from the left
                base = ("bin", "^", base, self.postfix())
                while self.kind() == "^":
                    self.take()
                    base = ("bin", "^", base, self.postfix())
                return base
            return ("bin", "^", base, self.nested(self.unary))
        return base

    def postfix(self):
        node = self.primary()
        if node[0] in ("var", "field") and self.kind() in ("++", "--"):
            return ("incr", False, 1.0 if self.take()[0] == "++" else -1.0, node)
        return node

    def lvalue(self):
        node = self.nested(self.primary)
        if node[0] not in ("var", "field"):
            raise self.fail()
        return node

    def primary(self):
        token = self.peek()
        kind = token[0]
        if kind in ("num", "str", "ere"):
            self.take()
            return (kind, token[1])
        if kind == "name":
            self.take()
            return ("var", token[1])
        if kind == "(":
            self.take()
            old, self.no_gt = self.no_gt, False
            node = self.nested(self.expr)
            self.no_gt = old
            self.take(")")
            return ("group", node)
        if kind == "$":
            self.take()
            if self.kind() == "-":
                self.take()
                return ("field", ("neg", self.nested(self.primary)))
            return ("field", self.nested(self.primary))
        if kind in ("++", "--"):
            self.take()
            return ("incr", True, 1.0 if kind == "++" else -1.0, self.lvalue())
        if kind in ("func", "call"):
            self.take()
            args = self.call_args() if self.kind() == "(" else None
            if kind == "call":
                self.undefined = self.undefined or token
                return ("num", 0.0)
            if args is None and token[1] != "length":
                raise TpotAwkSyntax(token, self.braces)
            args = args or []
            fewest, most = TPOT_AWK_FUNCTIONS[token[1]]
            if not fewest <= len(args) <= most or (token[1] in ("sub", "gsub") and len(args) == 3
                                                     and args[2][0] not in ("var", "field")):
                raise TpotAwkSyntax(token, self.braces)
            return ("fn", token[1], args)
        raise self.fail()

    def call_args(self) -> list:
        self.take("(")
        args = [] if self.kind() == ")" else self.nested(self.expr_list, False)
        self.take(")")
        return args


def tpot_awk_shallow(tree, limit: int) -> bool:
    """Whether the program tree is at most limit levels deep (walked without recursion)."""
    stack = [(tree, 0)]
    while stack:
        node, depth = stack.pop()
        if depth > limit:
            return False
        stack.extend((child, depth + 1) for child in node if isinstance(child, (tuple, list)))
    return True


class TpotAwk:
    """One run of an awk program over the persona's files and the piped input."""

    MAX_PROGRAM = 64 * 1024  # characters of program text
    MAX_INPUT = 1024 * 1024  # bytes of input one awk reads
    MAX_OUTPUT = 1024 * 1024  # characters of output
    MAX_STRING = 1024 * 1024  # characters of one value
    MAX_SECONDS = 1.0  # of one run on the reactor, checked with every charge of steps
    MAX_STEPS = 2_000_000  # evaluated nodes, records, characters touched and matcher work
    MAX_LIVE = 32 * 1024 * 1024  # characters in all variables together
    STATE_STEPS = 32  # steps per automaton state built
    MAX_NESTING = 32  # nested blocks, parentheses and operators while parsing
    MAX_DEPTH = 200  # levels of the program tree
    MAX_FIELDS = 32767
    MAX_WIDTH = 4096  # width and precision of a printf conversion
    PRINT = ("print", [])

    def __init__(self, style: str, version: str, read_file, stdin: bytes | None) -> None:
        self.style, self.version, self.read_file, self.stdin = style, version, read_file, stdin or b""
        new_mawk = style == "mawk" and (version.split() + ["0"])[1] >= "20230000"
        self.intervals = style != "mawk" or (version.split() + ["0"])[1] >= "20200717"
        self.int_limit = 2.0 ** 31 if style == "mawk" and not new_mawk else 2.0 ** 63
        self.source_name = "cmd. line"
        self.out, self.err = [], []
        self.written = self.steps = self.status = 0
        self.vars = {"FS": " ", "OFS": " ", "ORS": "\n", "OFMT": "%.6g", "CONVFMT": "%.6g", "NR": 0.0, "FNR": 0.0,
                     "NF": 0.0, "FILENAME": TPOT_AWK_UNINIT, "RS": "\n", "SUBSEP": "\x1c"}
        self.deadline = time.monotonic() + self.MAX_SECONDS
        self.sizes, self.live = {}, 0  # characters per variable, in all of them
        self.record, self.fields, self.regexes, self.in_begin = TPOT_AWK_UNINIT, [], {}, True

    # -- limits and messages

    def charge(self, steps: int) -> None:
        self.steps += steps
        if self.steps > self.MAX_STEPS or time.monotonic() > self.deadline:
            raise TpotAwkStop(2)

    def cap(self, text: str) -> str:
        if len(text) > self.MAX_STRING:
            raise TpotAwkStop(2)
        self.charge(len(text) >> 6)
        return text

    def touch(self, text: str, shift: int = 6) -> str:
        """text, its characters charged as steps (one per 64, one per 16 for a regular expression)."""
        self.charge(len(text) >> shift)
        return text

    def emit(self, text: str) -> None:
        self.written += len(text)
        if self.written > self.MAX_OUTPUT:
            raise TpotAwkStop(2)
        self.out.append(text)

    def fatal(self, gawk: str, mawk: str, busybox: str):
        name, fnr, nr = self.text(self.vars["FILENAME"]), int(self.num(self.vars["FNR"])), int(self.num(self.vars["NR"]))
        if self.style == "busybox":
            raise TpotAwkStop(1, f"awk: cmd. line:1: {busybox}\n")
        if self.style == "mawk":
            raise TpotAwkStop(2, f'awk: run time error: {mawk}\n\tFILENAME="{name}" FNR={fnr} NR={nr}\n')
        where = "" if self.in_begin else f"(FILENAME={name or '-'} FNR={fnr}) "
        raise TpotAwkStop(2, f"awk: {self.source_name}:1: {where}fatal: {gawk}\n")

    def syntax_message(self, error: TpotAwkSyntax, program: str) -> tuple:
        kind, _, line, col, text = error.token
        if self.style == "busybox":
            return 1, f"awk: cmd. line:{line}: Unexpected token\n"
        if self.style == "mawk":
            if kind == "eof" and error.braces:
                return 2, f"awk: line {program.count(chr(10)) + 2}: missing }} near end of file\n"
            return 2, f"awk: line {line}: syntax error at or near {text}\n"
        source = (program.split("\n") + [""])[line - 1]
        where = f"awk: {self.source_name}:{line}: "
        what = "unexpected newline or end of string" if kind in ("nl", "eof") else "syntax error"
        return 1, f"{where}{source}\n{where}{' ' * col}^ {what}\n"

    def undefined_message(self, token, program: str) -> tuple:
        if self.style == "busybox":
            return 1, f"awk: cmd. line:{token[2]}: Call to undefined function\n"
        if self.style == "mawk":
            return 2, f"awk: line {program.count(chr(10)) + 2}: function {token[1]} never defined\n"
        return 2, f"awk: {self.source_name}:{token[2]}: fatal: function `{token[1]}' not defined\n"

    # -- values

    def num(self, value) -> float:
        if isinstance(value, float):
            return value
        if not value:
            return 0.0
        self.touch(value, 4)
        if self.style != "gawk":
            found = TPOT_AWK_HEX.match(value)
            if found:
                digits = found.group(2)
                number = float(int(digits, 16)) if len(digits) <= 256 else math.inf
                return -number if found.group(1) == "-" else number
        found = TPOT_AWK_NUMBER.match(value)
        return float(found.group(1)) if found else 0.0

    def num_text(self, x: float, fmt) -> str:
        if x != x:
            return ("-" if math.copysign(1.0, x) < 0 else "+" if self.style == "gawk" else "") + "nan"
        if x in (math.inf, -math.inf):
            return ("-" if x < 0 else "+" if self.style == "gawk" else "") + "inf"
        if x == int(x) and abs(x) < self.int_limit:
            return str(int(x))
        if not isinstance(fmt, str) or fmt == "%.6g":
            return "%.6g" % x
        return self.format(fmt, [x])

    def text(self, value) -> str:
        if not isinstance(value, float):
            return value
        return self.num_text(value, "%.6g" if self.style == "busybox" else self.vars["CONVFMT"])

    def out_text(self, value) -> str:
        return self.num_text(value, self.vars["OFMT"]) if isinstance(value, float) else value

    def numeric(self, value) -> bool:
        return isinstance(value, (float, TpotAwkUninit)) or (
            isinstance(value, TpotAwkStrnum) and TPOT_AWK_LOOKS.fullmatch(self.touch(value, 4)) is not None)

    def truth(self, value) -> bool:
        if isinstance(value, TpotAwkStrnum) and self.numeric(value):
            return self.num(value) != 0
        return value != 0 if isinstance(value, float) else value != ""

    def integer(self, value) -> int:
        x = self.num(value)
        return int(x) if math.isfinite(x) else 0

    def regex(self, source: str) -> TpotAwkRegex:
        found = self.regexes.get(source)
        if found is None:
            if len(self.regexes) >= 64:
                self.regexes.clear()
            self.charge(16 * len(source) + 16)  # the build costs about that before its states
            found = self.regexes[source] = TpotAwkRegex(self, source)
        return found

    def regex_of(self, node) -> TpotAwkRegex:
        return self.regex(node[1] if node[0] == "ere" else self.text(self.value(node)))

    # -- records and fields

    def split(self, text: str) -> list:
        fs = self.text(self.vars["FS"])
        if not text:
            return []
        self.charge(len(text) >> 6)
        if fs == " ":
            text = text.strip(" \t\n")
            fields = TPOT_AWK_BLANKS.split(text) if text else []
        elif len(fs) == 1 and fs != "\\" or not any(c in fs for c in "\\^$.[]|()*+?{}"):
            if (text.count(fs) if fs else len(text) - 1) >= self.MAX_FIELDS:
                raise TpotAwkStop(2)
            fields = text.split(fs) if fs else list(text)
        else:
            regex, data, fields, start, pos = self.regex(fs), tpot_awk_bytes(text), [], 0, 0
            while pos <= len(text) and len(fields) < self.MAX_FIELDS:
                found = regex.find(data, pos)
                if found is None:
                    break
                if found[0] == found[1]:
                    pos = found[0] + 1
                    continue
                fields.append(text[start:found[0]])
                start = pos = found[1]
            fields.append(text[start:])
        if len(fields) > self.MAX_FIELDS:  # as mawk, which has at most 32767
            raise TpotAwkStop(2)
        self.charge(len(fields))
        return fields

    def set_record(self, text: str) -> None:
        self.record = TpotAwkStrnum(self.cap(text))
        self.fields = [TpotAwkStrnum(field) for field in self.split(text)]
        self.vars["NF"] = float(len(self.fields))

    def rebuild(self) -> None:
        self.charge(len(self.fields))
        self.vars["NF"] = float(len(self.fields))
        ofs, texts = self.text(self.vars["OFS"]), [self.text(f) for f in self.fields]
        if len(ofs) * max(0, len(texts) - 1) + sum(map(len, texts)) > self.MAX_STRING:  # sized before it is built
            raise TpotAwkStop(2)
        self.record = TpotAwkStrnum(self.cap(ofs.join(texts)))

    def field_index(self, value) -> int:
        x = self.num(value)
        if x < 0:
            self.fatal(f"attempt to access field {int(x)}", f"negative field index ${int(x)}",
                       "Access to negative field")
        return int(min(x, 2.0 ** 31)) if x == x else 0

    def field(self, index: int):
        if index == 0:
            return self.record
        return self.fields[index - 1] if index <= len(self.fields) else TPOT_AWK_UNINIT

    def set_field(self, index: int, value) -> None:
        if index == 0:
            self.set_record(self.text(value))
            return
        self.set_nf(float(max(index, len(self.fields))))
        self.fields[index - 1] = value
        self.rebuild()

    def set_nf(self, value) -> None:
        count = self.integer(value)
        if count < 0 or count > self.MAX_FIELDS:
            raise TpotAwkStop(2)
        self.charge(abs(count - len(self.fields)))
        self.fields = self.fields[:count] + [TPOT_AWK_UNINIT] * (count - len(self.fields))
        self.rebuild()

    def get(self, target):
        if target[0] == "var":
            return self.vars.get(target[1], TPOT_AWK_UNINIT)
        return self.field(self.field_index(self.value(target[1])))

    def set(self, target, value) -> None:
        if target[0] == "field":
            self.set_field(self.field_index(self.value(target[1])), value)
        elif target[1] == "NF":
            self.set_nf(value)
        elif target[1] in ("RS", "SUBSEP"):  # records end at newlines; there are no arrays
            raise TpotAwkStop(2)
        else:
            size = 0 if isinstance(value, float) else len(value)
            self.live += size - self.sizes.get(target[1], 0)
            if self.live > self.MAX_LIVE:
                raise TpotAwkStop(2)
            self.sizes[target[1]] = size
            self.vars[target[1]] = value

    # -- expressions

    def value(self, node):
        self.steps += 1
        if self.steps > self.MAX_STEPS or not self.steps & 255 and time.monotonic() > self.deadline:
            raise TpotAwkStop(2)
        op = node[0]
        if op in ("num", "str"):
            return node[1]
        if op == "var":
            return self.vars.get(node[1], TPOT_AWK_UNINIT)
        if op == "field":
            return self.field(self.field_index(self.value(node[1])))
        if op == "group":
            return self.value(node[1])
        if op == "ere":
            return 1.0 if self.regex(node[1]).search(self.record) else 0.0
        if op == "cmp":
            return 1.0 if self.compare(node[1], self.value(node[2]), self.value(node[3])) else 0.0
        if op == "match":
            subject = self.text(self.value(node[2]))
            return 1.0 if self.regex_of(node[3]).search(subject) != node[1] else 0.0
        if op == "and":
            return 1.0 if self.truth(self.value(node[1])) and self.truth(self.value(node[2])) else 0.0
        if op == "or":
            return 1.0 if self.truth(self.value(node[1])) or self.truth(self.value(node[2])) else 0.0
        if op == "not":
            return 0.0 if self.truth(self.value(node[1])) else 1.0
        if op == "neg":
            return -self.num(self.value(node[1]))
        if op == "plus":
            return self.num(self.value(node[1]))
        if op == "cat":
            left, right = self.text(self.value(node[1])), self.text(self.value(node[2]))
            if len(left) + len(right) > self.MAX_STRING:
                raise TpotAwkStop(2)
            return self.cap(left + right)
        if op == "bin":
            return self.arith(node[1], self.num(self.value(node[2])), self.num(self.value(node[3])))
        if op == "cond":
            return self.value(node[2] if self.truth(self.value(node[1])) else node[3])
        if op == "assign":
            _, kind, target, expr = node
            value = self.value(expr)
            if kind != "=":
                value = self.arith(kind[0], self.num(self.get(target)), self.num(value))
            self.set(target, value)
            return value
        if op == "incr":
            _, pre, delta, target = node
            old = self.num(self.get(target))
            self.set(target, old + delta)
            return old + delta if pre else old
        return self.builtin(node[1], node[2])

    def compare(self, op: str, a, b) -> bool:
        if self.numeric(a) and self.numeric(b):
            return TPOT_AWK_COMPARE[op](self.num(a), self.num(b))
        return TPOT_AWK_COMPARE[op](self.touch(self.text(a)), self.touch(self.text(b)))

    def arith(self, op: str, x: float, y: float) -> float:
        if op == "+":
            return x + y
        if op == "-":
            return x - y
        if op == "*":
            return x * y
        if op == "^":
            try:
                return math.pow(x, y)
            except OverflowError:
                return math.inf
            except ValueError:
                return math.nan
        if y == 0:
            if self.style != "mawk":
                self.fatal("division by zero attempted" + (" in `%'" if op == "%" else ""), "", "Division by zero")
            return math.nan if op == "%" or x == 0 or x != x else math.copysign(math.inf, x)
        try:
            return x / y if op == "/" else math.fmod(x, y)
        except ValueError:
            return math.nan

    def builtin(self, name: str, args: list):
        if name == "length":
            return float(len(self.text(self.value(args[0])) if args else self.record))
        if name in ("sub", "gsub"):
            return self.substitute(name == "gsub", args)
        values = [self.value(arg) for arg in args]
        text = self.touch(self.text(values[0]))
        if name == "substr":
            start = self.num(values[1])
            if not math.isfinite(start):
                return ""
            start = max(1, int(max(min(start, 2.0 ** 31), -2.0 ** 31)))
            if len(values) == 2:
                return text[start - 1:]
            length = self.num(values[2])
            if not length > 0:
                return ""
            return text[start - 1:start - 1 + int(min(length, 2.0 ** 31))]
        if name == "index":
            return float(text.find(self.touch(self.text(values[1]))) + 1)
        if name == "tolower":
            return text.lower() if text.isascii() else text.translate(TPOT_AWK_LOWER)
        if name == "toupper":
            return text.upper() if text.isascii() else text.translate(TPOT_AWK_UPPER)
        if name == "int":
            x = self.num(values[0])
            return float(math.trunc(x)) if math.isfinite(x) else x
        return self.format(text, values[1:])

    def substitute(self, every: bool, args: list) -> float:
        regex = self.regex_of(args[0])
        template = self.text(self.value(args[1]))
        target = args[2] if len(args) == 3 else ("field", ("num", 0.0))
        text = self.touch(self.text(self.get(target)))
        parts = self.replacement_parts(template)
        literal = sum(map(len, parts))
        data, out, size, pos, last, count = tpot_awk_bytes(text), [], 0, 0, -1, 0
        while pos <= len(text):
            found = regex.find(data, pos)
            if found is None:
                break
            start, end = found
            if literal + (len(parts) - 1) * (end - start) > self.MAX_STRING:
                raise TpotAwkStop(2)
            if start == end == last:  # no empty match right after a match
                out.append(text[start:start + 1])
                pos = start + 1
                continue
            piece = text[pos:start] + text[start:end].join(parts)
            self.charge(len(piece) >> 6)
            out.append(piece)
            size += len(piece)
            if size > self.MAX_STRING:
                raise TpotAwkStop(2)
            count, last, pos = count + 1, end, end
            if not every:
                break
            if start == end:
                out.append(text[start:start + 1])
                pos = start + 1
        if count:
            out.append(text[pos:])
            self.set(target, self.cap("".join(out)))
        return float(count)

    def replacement_parts(self, template: str) -> list:
        """The replacement text of sub and gsub as the pieces between its matches: & is the match,
        \\& a literal &, \\\\ a backslash."""
        if "\\" not in template:
            return template.split("&")
        parts, piece, pos = [], [], 0
        for found in TPOT_AWK_AMP.finditer(template):
            self.charge(1)
            piece.append(template[pos:found.start()])
            if found.group() == "&":
                parts.append("".join(piece))
                piece = []
            else:
                piece.append(found.group()[1])
            pos = found.end()
        piece.append(template[pos:])
        parts.append("".join(piece))
        return parts

    def format(self, fmt: str, args: list) -> str:
        """printf and sprintf: the conversions of C's printf (c d i o x X u e E f F g G s %)."""
        out, pos, used, size = [], 0, 0, 0
        while True:
            i = fmt.find("%", pos)
            if i < 0:
                out.append(fmt[pos:])
                break
            out.append(fmt[pos:i])
            self.charge(8)
            found = TPOT_AWK_FORMAT.match(fmt, i)
            flags, width, precision, conv = found.groups()
            pos = found.end()
            if conv == "%":
                out.append("%")
                continue
            if conv == "" or conv not in "cdiouxXeEfFgGs":
                out.append(fmt[i:pos])
                continue
            if "*" in (width, precision) and self.style == "busybox":
                self.fatal("", "", "%*x formats are not supported")
            if width == "*":
                width, used = str(self.integer(args[used]) if used < len(args) else 0), used + 1
            if precision == "*":
                precision, used = str(self.integer(args[used]) if used < len(args) else 0), used + 1
                precision = None if precision.startswith("-") else precision  # as if omitted
            if width and width.startswith("-"):
                flags, width = flags + "-", width[1:]
            if len(width or "") > 9 or len(precision or "") > 9:  # more than any awk can print
                raise TpotAwkStop(2)
            if int(width or 0) > self.MAX_WIDTH or int(precision or 0) > self.MAX_WIDTH:
                raise TpotAwkStop(2)
            value = args[used] if used < len(args) else TPOT_AWK_UNINIT
            used += 1
            spec = "%" + flags + (width or "") + ("" if precision is None else "." + (precision or "0"))
            if conv == "s":
                piece = (spec + "s") % self.text(value)
            elif conv == "c":
                if isinstance(value, float) or (isinstance(value, TpotAwkStrnum) and self.numeric(value)):
                    code = self.integer(value)
                    char = chr(code) if 0 <= code < 256 else ""
                else:
                    char = value[:1]
                piece = (spec.split(".")[0] + "s") % char
            else:
                x = self.num(value)
                if conv in "eEfFgG":
                    piece = (spec + conv) % x
                elif not math.isfinite(x):
                    piece = (spec.replace("0", "").split(".")[0] + "s") % self.num_text(x, "%.6g")
                else:
                    n = int(x)
                    if self.style == "mawk" and self.int_limit == 2.0 ** 31:
                        n = max(-2147483647, min(2147483647, n))
                    if conv in "ouxX" and n < 0:
                        n += 2 ** 64
                    piece = (spec + ("d" if conv in "iu" else conv)) % n
                    if conv == "o" and "#" in flags:
                        piece = piece.replace("0o", "0", 1)
            out.append(piece)
            size += len(piece)
            if size > self.MAX_STRING:
                raise TpotAwkStop(2)
        return self.cap("".join(out))

    # -- statements and the run

    def execute(self, node) -> None:
        self.charge(1)
        op = node[0]
        if op == "block":
            for statement in node[1]:
                self.execute(statement)
        elif op == "expr":
            self.value(node[1])
        elif op == "print":
            items = [self.out_text(self.value(arg)) for arg in node[1]] if node[1] else [self.record]
            ofs, ors = self.text(self.vars["OFS"]), self.text(self.vars["ORS"])
            if sum(map(len, items)) + len(ofs) * len(items) + len(ors) + self.written > self.MAX_OUTPUT:
                raise TpotAwkStop(2)
            self.emit(ofs.join(items) + ors)
        elif op == "printf":
            values = [self.value(arg) for arg in node[1]]
            self.emit(self.format(self.text(values[0]), values[1:]))
        elif op == "if":
            branch = node[2] if self.truth(self.value(node[1])) else node[3]
            if branch is not None:
                self.execute(branch)
        elif op in ("while", "for"):
            init, cond, step, body = (None, node[1], None, node[2]) if op == "while" else node[1:]
            if init is not None:
                self.value(init)
            while cond is None or self.truth(self.value(cond)):
                try:
                    self.execute(body)
                except TpotAwkJump as jump:
                    if jump.kind == "break":
                        break
                    if jump.kind != "continue":
                        raise
                if step is not None:
                    self.value(step)
        elif op == "exit":
            raise TpotAwkJump("exit", None if node[1] is None else self.integer(self.value(node[1])) & 255)
        else:
            raise TpotAwkJump(op)

    def action(self, node) -> None:
        """One action; next, break and continue outside of their place end it."""
        try:
            self.execute(node)
        except TpotAwkJump as jump:
            if jump.kind == "exit":
                raise

    def assign_operand(self, text: str) -> None:
        name, _, value = text.partition("=")
        self.set(("var", name), TpotAwkStrnum(tpot_awk_unescape(value, self.style == "gawk")))

    def open(self, name: str):
        data = self.read_file(name)
        if data == "dir":
            if self.style == "gawk":
                self.err.append(f"awk: warning: command line argument `{name}' is a directory: skipped\n")
                return None
            if self.style == "mawk":
                raise TpotAwkStop(2, "awk: read error (Is a directory)\n")
            raise TpotAwkStop(1, "awk: cmd. line:1: Is a directory\n")
        if not isinstance(data, bytes):
            if self.style == "busybox":
                self.err.append(f"awk: {name}: No such file or directory\n")
                self.status = 1
                return None
            if self.style == "mawk":
                raise TpotAwkStop(2, f"awk: cannot open {name} (No such file or directory)\n")
            where = "" if self.num(self.vars["NR"]) == 0 else f"{self.source_name}:1: "
            why = " (No such file or directory)" if self.version.startswith("4.0") else ": No such file or directory"
            raise TpotAwkStop(2, f"awk: {where}fatal: cannot open file `{name}' for reading{why}\n")
        return data

    def records(self, operands: list):
        files = [name for name in operands if not TPOT_AWK_ASSIGNMENT.match(name)]
        read = 0
        for name in operands + ([] if files else [None]):
            if name is not None and TPOT_AWK_ASSIGNMENT.match(name):
                self.assign_operand(name)
                continue
            if name in (None, "-", "/dev/stdin"):
                data, label = self.stdin, "-" if name or self.style == "mawk" else ""
            else:
                data, label = self.open(name), name
                if data is None:
                    continue
            data = data[:max(0, self.MAX_INPUT - read)]
            read += len(data)
            self.vars["FILENAME"], self.vars["FNR"] = label, 0.0
            lines = data.decode("utf-8", errors="replace").split("\n")
            if lines[-1] == "":
                lines.pop()
            for line in lines:
                self.charge(1)
                self.vars["NR"] = self.num(self.vars["NR"]) + 1
                self.vars["FNR"] = self.num(self.vars["FNR"]) + 1
                self.set_record(line)
                yield

    def run(self, program: str, operands: list) -> int:
        self.deadline = time.monotonic() + self.MAX_SECONDS
        try:
            if len(program) > self.MAX_PROGRAM:
                raise TpotAwkStop(2)
            try:
                parser = TpotAwkParser(self, TpotAwkLexer(self, program).tokens())
                rules = parser.program()
            except TpotAwkSyntax as error:
                raise TpotAwkStop(*self.syntax_message(error, program)) from None
            if parser.undefined:
                raise TpotAwkStop(*self.undefined_message(parser.undefined, program))
            if not tpot_awk_shallow(rules, self.MAX_DEPTH):
                raise TpotAwkStop(2)
            return self.main(rules, operands)
        except TpotAwkStop as stop:
            self.err.append(stop.text)
            return stop.code
        except RecursionError:
            return 2

    def main(self, rules: list, operands: list) -> int:
        code = None
        main = [(pattern, action) for kind, pattern, action in rules if kind == "main"]
        try:
            for kind, _, action in rules:
                if kind == "BEGIN":
                    self.action(action)
            self.in_begin = False
            if main or any(kind == "END" for kind, _, _ in rules):
                for _ in self.records(operands):
                    for pattern, action in main:
                        if pattern is None or self.truth(self.value(pattern)):
                            try:
                                self.execute(action or self.PRINT)
                            except TpotAwkJump as jump:
                                if jump.kind == "exit":
                                    raise
                                if jump.kind == "next":
                                    break
        except TpotAwkJump as jump:
            code = jump.code
        self.in_begin = False
        try:
            for kind, _, action in rules:
                if kind == "END":
                    self.action(action)
        except TpotAwkJump as jump:
            code = jump.code if jump.code is not None else code
        return self.status if code is None else code


TPOT_AWK_GPL = """This program is free software; you can redistribute it and/or modify
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
TPOT_AWK_GAWK_VERSIONS = {
    "5.1.0": "GNU Awk 5.1.0, API: 3.0 (GNU MPFR 4.1.0-p9, GNU MP 6.2.0)\nCopyright (C) 1989, 1991-2020 Free Software "
             "Foundation.\n",
    "4.1.4": "GNU Awk 4.1.4, API: 1.1 (GNU MPFR 4.0.1, GNU MP 6.1.2)\nCopyright (C) 1989, 1991-2016 Free Software "
             "Foundation.\n",
    "4.0.2": "GNU Awk 4.0.2\nCopyright (C) 1989, 1991-2012 Free Software Foundation.\n",
}
TPOT_AWK_GAWK_USAGE = """Usage: awk [POSIX or GNU style options] -f progfile [--] file ...
Usage: awk [POSIX or GNU style options] [--] 'program' file ...
POSIX options:\t\tGNU long options: (standard)
\t-f progfile\t\t--file=progfile
\t-F fs\t\t\t--field-separator=fs
\t-v var=val\t\t--assign=var=val
Short options:\t\tGNU long options: (extensions)
\t-b\t\t\t--characters-as-bytes
\t-c\t\t\t--traditional
\t-C\t\t\t--copyright
\t-d[file]\t\t--dump-variables[=file]
\t-D[file]\t\t--debug[=file]
\t-e 'program-text'\t--source='program-text'
\t-E file\t\t\t--exec=file
\t-g\t\t\t--gen-pot
\t-h\t\t\t--help
{include}{lint}{bignum}\t-N\t\t\t--use-lc-numeric
\t-n\t\t\t--non-decimal-data
\t-o[file]\t\t--pretty-print[=file]
\t-O\t\t\t--optimize
\t-p[file]\t\t--profile[=file]
\t-P\t\t\t--posix
\t-r\t\t\t--re-interval
{no_optimize}\t-S\t\t\t--sandbox
\t-t\t\t\t--lint-old
\t-V\t\t\t--version

{bugs}
gawk is a pattern scanning and processing language.
By default it reads standard input and writes standard output.

Examples:
\tawk '{{ sum += $1 }}; END {{ print sum }}' file
\tawk -F: '{{ print $1 }}' /etc/passwd
"""
TPOT_AWK_GAWK40_USAGE = """Usage: awk [POSIX or GNU style options] -f progfile [--] file ...
Usage: awk [POSIX or GNU style options] [--] 'program' file ...
POSIX options:\t\tGNU long options: (standard)
\t-f progfile\t\t--file=progfile
\t-F fs\t\t\t--field-separator=fs
\t-v var=val\t\t--assign=var=val
Short options:\t\tGNU long options: (extensions)
\t-b\t\t\t--characters-as-bytes
\t-c\t\t\t--traditional
\t-C\t\t\t--copyright
\t-d[file]\t\t--dump-variables[=file]
\t-e 'program-text'\t--source='program-text'
\t-E file\t\t\t--exec=file
\t-g\t\t\t--gen-pot
\t-h\t\t\t--help
\t-L [fatal]\t\t--lint[=fatal]
\t-n\t\t\t--non-decimal-data
\t-N\t\t\t--use-lc-numeric
\t-O\t\t\t--optimize
\t-p[file]\t\t--profile[=file]
\t-P\t\t\t--posix
\t-r\t\t\t--re-interval
\t-S\t\t\t--sandbox
\t-t\t\t\t--lint-old
\t-V\t\t\t--version

To report bugs, see node `Bugs' in `gawk.info', which is
section `Reporting Problems and Bugs' in the printed version.

gawk is a pattern scanning and processing language.
By default it reads standard input and writes standard output.

Examples:
\tgawk '{ sum += $1 }; END { print sum }' file
\tgawk -F: '{ print $1 }' /etc/passwd
"""
TPOT_AWK_MAWK_USAGE = """Usage: mawk [Options] [Program] [file ...]

Program:
    The -f option value is the name of a file containing program text.
    If no -f option is given, a "--" ends option processing; the following
    parameters are the program text.

Options:
    -f program-file  Program  text is read from file instead of from the
                     command-line.  Multiple -f options are accepted.
    -F value         sets the field separator, FS, to value.
    -v var=value     assigns value to program variable var.
    --               unambiguous end of options.

    Implementation-specific options are prefixed with "-W".  They can be
    abbreviated:

    -W version       show version information and exit.
{compat}    -W dump          show assembler-like listing of program and exit.
    -W help          show this message and exit.
    -W interactive   set unbuffered output, line-buffered input.
    -W exec file     use file as program as well as last option.
{posix}    -W random=number set initial random seed.
    -W sprintf=number adjust size of sprintf buffer.
{posix_space}    -W usage         show this message and exit.
"""


def tpot_awk_gawk_usage(version: str) -> str:
    if version.startswith("4.0"):
        return TPOT_AWK_GAWK40_USAGE
    new = version.startswith("5")
    return TPOT_AWK_GAWK_USAGE.format(
        include="\t-i includefile\t\t--include=includefile\n\t-l library\t\t--load=library\n",
        lint="\t-L[fatal|invalid|no-ext]\t--lint[=fatal|invalid|no-ext]\n" if new else
        "\t-L[fatal|invalid]\t--lint[=fatal|invalid]\n",
        bignum="\t-M\t\t\t--bignum\n",
        no_optimize="\t-s\t\t\t--no-optimize\n" if new else "",
        bugs=("To report bugs, see node `Bugs' in `gawk.info'\nwhich is section `Reporting Problems and Bugs' in "
              "the\nprinted version.  This same information may be found at\n"
              "https://www.gnu.org/software/gawk/manual/html_node/Bugs.html.\n"
              "PLEASE do NOT try to report bugs by posting in comp.lang.awk,\nor by using a web forum such as "
              "Stack Overflow.\n") if new else
        ("To report bugs, see node `Bugs' in `gawk.info', which is\nsection `Reporting Problems and Bugs' in the "
         "printed version.\n"))


def tpot_awk_mawk_usage(version: str) -> str:
    if version.startswith("1.3.3"):
        return ("usage: mawk [-F value] [-v var=value] [--] 'program text' [file ...]\n"
                "usage: mawk [-F value] [-v var=value] [-f program-file] [--] [file ...]\n")
    new = (version.split() + ["0"])[1] >= "20230000"
    return TPOT_AWK_MAWK_USAGE.format(
        compat="    -W compat        pre-POSIX 2001.\n" if new else "",
        posix="    -W posix         stricter POSIX checking.\n" if new else "",
        posix_space="" if new else '    -W posix_space   do not consider "\\n" a space.\n')


def tpot_awk_mawk_version(version: str) -> str:
    if version.startswith("1.3.3"):
        return "mawk 1.3.3 Nov 1996, Copyright (C) Michael D. Brennan\n\ncompiled limits:\nmax NF             32767\n" \
               "sprintf buffer      2040\n"
    date = (version.split() + ["20200120"])[1]
    year, new = date[:4], date >= "20230000"
    previous = str(int(year) - 1) if year.isdigit() else "2019"
    return (f"mawk 1.3.4 {date}\nCopyright 2008-{previous},{year}, Thomas E. Dickey\n"
            "Copyright 1991-1996,2014, Michael D. Brennan\n\n"
            f"random-funcs:       {'arc4random_stir/arc4random' if new else 'srandom/random'}\n"
            "regex-funcs:        internal\n" + ("\n" if new else "") + "compiled limits:\nsprintf buffer      8192\n"
            f"maximum-integer     {'9223372036854775808' if new else '2147483647'}\n")


def tpot_awk_busybox_usage(banner: str) -> str:
    head = f"{banner} multi-call binary.\n\n" if banner else ""
    found = re.match(r"BusyBox v(\d+)\.(\d+)", banner or "")
    version = (int(found.group(1)), int(found.group(2))) if found else (1, 24)
    if version < (1, 15):
        return (f"{head}Usage: awk [OPTION]... [program-text] [FILE...]\n\nOptions:\n"
                "\t-v var=val\tSet variable\n\t-F sep\t\tUse sep as field separator\n"
                "\t-f file\t\tRead program from file\n")
    text = (f"{head}Usage: awk [OPTIONS] [AWK_PROGRAM] [FILE]...\n\n\t-v VAR=VAL\tSet variable\n"
            "\t-F SEP\t\tUse SEP as field separator\n\t-f FILE\t\tRead program from FILE\n")
    return text + ("\t-e AWK_PROGRAM\n" if version >= (1, 26) else "")


class Command_tpot_awk(Command_awk):
    """awk of the persona: the options, help and version texts of its awk; TpotAwk runs the program."""

    def start(self) -> None:
        style = CowrieConfig.get("shell", "awk_style", fallback="gawk")
        self.style = style if style in ("gawk", "mawk", "busybox") else "gawk"
        self.version = CowrieConfig.get("shell", "awk_version", fallback="") or (
            {"gawk": "5.1.0", "mawk": "1.3.4 20200120"}.get(self.style, ""))
        self.tpot_out, self.tpot_err = [], []
        try:
            code = self.tpot_run(list(self.args))
        except Exception:  # noqa: BLE001 - awk ends, the session goes on; the error goes to Cowrie's log
            log.err(None, "awk of the persona ended on an internal error")
            code = 2
        if self.tpot_out:
            self.write("".join(self.tpot_out))
        if self.tpot_err:
            self.errorWrite("".join(self.tpot_err))
        self.exit_code = code
        self.exit()

    def tpot_read(self, name: str):
        """The contents of a file of the emulated file system, "dir" for a directory, None if missing."""
        path = self.fs.resolve_path(name, self.cwd)
        try:
            return "dir" if self.fs.isdir(path) else self.fs.file_contents(path)
        except Exception:  # noqa: BLE001 - a missing or unreadable file
            return None

    def tpot_usage(self, gawk: str = "", mawk: str = "", busybox: str = "") -> int:
        """A wrong option or no program: the message of the persona's awk (none: its usage)."""
        if self.style == "mawk":
            if mawk or not self.version.startswith("1.3.3"):  # 1.3.3 ends without a word
                self.tpot_err.append(f"awk: {mawk}\n" if mawk else tpot_awk_mawk_usage(self.version))
            return 2 if mawk else 0
        text = busybox if self.style == "busybox" else gawk
        usage = tpot_awk_busybox_usage(self.version) if self.style == "busybox" else tpot_awk_gawk_usage(self.version)
        self.tpot_err.append((f"awk: {text}\n" if text else "") + usage)
        return 1

    def tpot_version(self) -> int:
        """--version, -V or -W version; mawk before 2023 writes its compiled limits to stderr."""
        if self.style == "mawk" and (self.version.split() + ["0"])[1] < "20230000":
            out, _, err = tpot_awk_mawk_version(self.version).partition("\n\n")
            self.tpot_out.append(out + "\n\n")
            self.tpot_err.append(err)
        elif self.style == "mawk":
            self.tpot_out.append(tpot_awk_mawk_version(self.version))
        else:
            self.tpot_out.append(TPOT_AWK_GAWK_VERSIONS.get(self.version, f"GNU Awk {self.version}\n") + "\n"
                                 + TPOT_AWK_GPL)
        return 0

    def tpot_run(self, args: list) -> int:
        style, gawk = self.style, self.style == "gawk"
        awk = TpotAwk(style, self.version, self.tpot_read, self.input_data)
        self.tpot_out, self.tpot_err = awk.out, awk.err
        sources, index = [], 0
        new_mawk = style == "mawk" and (self.version.split() + ["0"])[1] >= "20230000"
        while index < len(args):
            arg = args[index]
            if arg == "--":
                index += 1
                break
            if not arg.startswith("-") or arg == "-":
                break
            index += 1
            if arg.startswith("--"):
                option, eq, value = arg[2:].partition("=")
                if (gawk or new_mawk) and option in ("version", "help", "usage"):
                    if option == "version":
                        return self.tpot_version()
                    awk.out.append(tpot_awk_gawk_usage(self.version) if gawk else tpot_awk_mawk_usage(self.version))
                    return 0
                if not gawk or option not in ("field-separator", "assign", "file", "source"):
                    return self.tpot_usage("", f"not an option: {arg}", f"unrecognized option '{arg}'")
                if not eq:
                    if index >= len(args):
                        return self.tpot_usage(f"option '{arg}' requires an argument")
                    value, index = args[index], index + 1
                letters = {"field-separator": "F", "assign": "v", "file": "f", "source": "e"}
                pairs = [(letters[option], value)]
            else:
                pairs, position = [], 1
                while position < len(arg):
                    letter = arg[position]
                    position += 1
                    if letter in "FvfW" or (
                            letter == "e" and (gawk or "-e AWK_PROGRAM" in tpot_awk_busybox_usage(self.version))):
                        value = arg[position:]
                        if not value:
                            if index >= len(args):
                                return self.tpot_usage(f"option requires an argument -- {letter}",
                                                       f"option -{letter} lacks argument",
                                                       f"option requires an argument -- '{letter}'")
                            value, index = args[index], index + 1
                        pairs.append((letter, value))
                        break
                    if gawk and letter == "V":
                        return self.tpot_version()
                    if gawk and letter == "h":
                        awk.out.append(tpot_awk_gawk_usage(self.version))
                        return 0
                    if not (gawk and letter in "bcCdDgMnNoOpPrsStL"):
                        return self.tpot_usage("", f"not an option: -{letter}", f"invalid option -- '{letter}'")
            for letter, value in pairs:
                if letter == "F":
                    value = tpot_awk_unescape(value, gawk)
                    awk.vars["FS"] = "\t" if gawk and value == "t" else value
                elif letter == "v":
                    if not TPOT_AWK_ASSIGNMENT.match(value):
                        return self.tpot_usage(f"`{value}' argument to `-v' not in `var=value' form\n",
                                               f"improper assignment: -v {value}")
                    awk.assign_operand(value)
                elif letter == "W":
                    word = value.split("=", 1)[0]
                    if style == "busybox":
                        awk.err.append("awk: warning: option -W is ignored\n")
                    elif word and "version".startswith(word):
                        return self.tpot_version()
                    elif word and ("usage".startswith(word) or "help".startswith(word)):
                        awk.out.append(tpot_awk_gawk_usage(self.version) if gawk else tpot_awk_mawk_usage(self.version))
                        return 0
                else:
                    sources.append((letter, value))
        operands = args[index:]
        texts = []
        for letter, value in sources:
            if letter == "e":
                texts.append(value)
                continue
            data = self.tpot_read(value)
            if not isinstance(data, bytes):
                if style == "busybox":
                    awk.err.append(f"awk: can't open '{value}': No such file or directory\n")
                    return 1
                awk.err.append(f"awk: cannot open {value} (No such file or directory)\n" if style == "mawk" else
                               f"awk: fatal: can't open source file `{value}' for reading (No such file or directory)\n"
                               if self.version.startswith("4.0") else
                               f"awk: fatal: cannot open source file `{value}' for reading: No such file or directory\n")
                return 2
            texts.append(data.decode("utf-8", errors="replace"))
            awk.source_name = value
        if texts:
            program = "\n".join(texts)
        elif operands:
            program = operands.pop(0)
        else:
            return self.tpot_usage()
        return awk.run(program, operands)


commands["/bin/awk"] = Command_tpot_awk
commands["/usr/bin/awk"] = Command_tpot_awk
commands["awk"] = Command_tpot_awk
'''


def patch_awk(cowrie_root):
    """awk of the persona (AWK_COMMAND after the commands of awk.py): a small interpreter for
    the awk of scripts, regular expressions on the grep automaton (TpotGrepMatcher of the
    patched fs.py). Runs after patch_grep."""
    path = cowrie_root / "src" / "cowrie" / "commands" / "awk.py"
    replace_once(path, 'commands["/bin/awk"] = Command_awk\ncommands["awk"] = Command_awk\n',
                 'commands["/bin/awk"] = Command_awk\ncommands["awk"] = Command_awk\n' + AWK_COMMAND)


# module code of the patched commands/wc.py after its commands: wc, sort and seq of the persona
TEXT_COMMAND = r'''

# T-Pot: wc, sort and seq of the persona. [shell] text_style gnu: GNU coreutils (text_version,
# text_year, text_packager, text_collate c or en for the order of sort); text_style busybox: the
# BusyBox of the firmware (text_banner, text_version, text_libc glibc, uclibc or musl for the texts
# of option errors, text_unicode). They read files of the emulated file system and the data of a pipe
# or of the terminal; input, output, lines and keys have limits.
import posixpath
import time
import unicodedata
from decimal import Context, Decimal, InvalidOperation, localcontext

from twisted.python import log

from cowrie.core.config import CowrieConfig
from cowrie.shell import fs as tpot_shell_fs

TPOT_TEXT_INPUT = 4 << 20
TPOT_TEXT_OUTPUT = 1 << 20
# the work of one command: about 0.8 s of wall-clock time, sort's lines times keys
TPOT_TEXT_SECONDS = 0.8
TPOT_SORT_BYTES = 1 << 20
TPOT_SORT_LINES = 50_000
TPOT_SORT_WORK = 100_000
TPOT_SORT_KEYS = 32
TPOT_SEQ_NUMBERS = 2_000_000
# a long double: about 1.19e4932; seq's precision and width as far as the output can reach
TPOT_SEQ_EXPONENT = 4932
TPOT_SEQ_DIGITS = 1 << 20
TPOT_TEXT_DIGITS = 4096
# white space of glibc in a UTF-8 locale (iswspace: no no-break spaces)
TPOT_TEXT_SPACE = "[ \t\n\v\f\r\u1680\u2000-\u2006\u2008-\u200a\u2028\u2029\u205f\u3000]"
TPOT_TEXT_WORDS = re.compile(f"(?:(?!{TPOT_TEXT_SPACE}).)++", re.S)
TPOT_TEXT_C_WORDS = re.compile(rb"[^ \t\n\v\f\r]++")
TPOT_TEXT_C_PRINT = re.compile(rb"[!-~]")
TPOT_TEXT_C_CONTROL_RUNS = re.compile(rb"(?<![^ \t\n\v\f\r])[\x00-\x08\x0e-\x1f\x7f]++(?![^ \t\n\v\f\r])")
TPOT_TEXT_C_PLAIN = re.compile(rb"[ -~]*+")
TPOT_WC_INPUT = 8 << 20
# beyond TPOT_WC_INPUT up to this size wc counts lines, words, characters and bytes at C speed
TPOT_WC_TAIL = 32 << 20
TPOT_WC_PIECE = 1 << 20
# white space Python's str.split takes that glibc's iswspace does not, characters of no width,
# wide characters (East Asian W and F, the main blocks)
TPOT_TEXT_PY_SPACE = re.compile("[\x1c-\x1f\x85\xa0\u2007\u202f]")
TPOT_TEXT_ZERO_WIDTH = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f\u0300-\u036f\u200b-\u200f\u20d0-\u20ff\ufe00-\ufe0f]")
TPOT_TEXT_WIDE = re.compile("[\u1100-\u115f\u2e80-\u303e\u3041-\u33ff\u3400-\u4dbf\u4e00-\u9fff\ua000-\ua4cf"
                            "\uac00-\ud7a3\uf900-\ufaff\ufe30-\ufe4f\uff00-\uff60\uffe0-\uffe6"
                            "\U0001f300-\U0001f64f\U00020000-\U0003fffd]")
TPOT_TEXT_NOT_PRINTABLE = re.compile("[\x00-\x08\x0e-\x1f\x7f-\x9f\u200b-\u200f\u2028-\u202e\ufff0-\uffff]")
TPOT_TEXT_C_NO_PRINT_RUNS = re.compile(rb"(?<![^ \t\n\v\f\r])[^ \t\n\v\f\r!-~]++(?![^ \t\n\v\f\r])")
TPOT_TEXT_CONTINUATION = bytes(range(0x80, 0xC0))
TPOT_TEXT_NON_PRINT_BYTES = bytes(b for b in range(256) if not 0x20 <= b <= 0x7E and b != 9)
TPOT_TEXT_LINE_ENDS = re.compile("[\n\r\f]")
TPOT_TEXT_ASCII_PRINT = re.compile(r"[ -~]*+")
TPOT_TEXT_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
TPOT_TEXT_NUMBER = re.compile(r"[ \t]*+(-?+)([0-9]*+)(?:\.([0-9]*+))?+")
TPOT_TEXT_STRTOD = re.compile(r"[ \t\n\v\f\r]*+([-+]?+(?:(?:[0-9]++\.?+[0-9]*+|\.[0-9]++)(?:[eE][-+]?+[0-9]++)?+"
                              r"|0[xX][0-9a-fA-F]++|inf(?:inity)?+|nan))", re.I)
TPOT_TEXT_GNU_FLOAT = re.compile(r"[ \t\n\v\f\r]*+[-+]?+(?:(?:[0-9]++\.?+[0-9]*+|\.[0-9]++)(?:[eE][-+]?+[0-9]++)?+"
                                 r"|0[xX][0-9a-fA-F]++(?:\.[0-9a-fA-F]*+)?+(?:[pP][-+]?+[0-9]++)?+|inf(?:inity)?+"
                                 r"|nan)", re.I)
TPOT_TEXT_FORMAT = re.compile(r"%([-+ #0']*+)([0-9]*+)(?:\.([0-9]*+))?+([a-zA-Z%]?)")


def tpot_text(key: str, fallback: str = "") -> str:
    return CowrieConfig.get("shell", key, fallback=fallback) or fallback


class TpotTextStop(Exception):
    """Ends the command: the text for stderr and the exit status."""

    def __init__(self, text: str, code: int) -> None:
        super().__init__(text)
        self.text, self.code = text, code


def tpot_text_quote(value: str) -> str:
    """quote() of gnulib: the quotation marks of a UTF-8 locale, ASCII ones in the POSIX locale."""
    if tpot_text("text_collate", "c") == "posix":
        return f"'{value}'"
    return f"\u2018{value}\u2019"


def tpot_text_quotef(name: str) -> str:
    """A file name in a message of coreutils 9: quoted only with characters a shell would treat."""
    if re.fullmatch(r"[\w./+,:@%^=-]++", name):
        return name
    return "'" + name.replace("'", "'\\''") + "'"


class TpotTextTool(HoneyPotCommand):
    """What wc, sort and seq share: the persona's style, the option parsers and their messages, the
    files of the emulated file system and standard input (a pipe or the terminal until its end)."""

    consumes_stdin = True
    tool = ""
    status = 1           # the exit status of an option error
    shorts = ""          # GNU getopt string ("+" first: options end at the first operand)
    longs: dict = {}     # GNU long option: (short letter or own key, 0 no argument, 1 required, 2 optional)
    busybox_shorts = ""

    def start(self) -> None:
        self.gnu = tpot_text("text_style", "gnu") != "busybox"
        numbers = re.findall(r"[0-9]+", tpot_text("text_version", "9.1" if self.gnu else "1.36.1"))
        self.version = tuple(int(x) for x in numbers[:3]) or ((9, 1) if self.gnu else (1, 36, 1))
        self.libc = tpot_text("text_libc", "glibc")
        self.unicode = tpot_text("text_unicode", "false").lower() in ("1", "true", "yes")
        self.collate = tpot_text("text_collate", "c")
        self.tpot_lines = None
        self.tpot_run(None)

    # --- running ---------------------------------------------------------------------------

    def tpot_run(self, terminal: bytes | None) -> None:
        self.tpot_out, self.tpot_size, self.tpot_err = [], 0, []
        self.tpot_deadline = time.monotonic() + TPOT_TEXT_SECONDS
        try:
            code = self.tpot_main(terminal)
        except TpotTextStop as stop:
            self.tpot_err.append(stop.text)
            code = stop.code
        except Exception:  # noqa: BLE001 - the command ends, the session goes on; the error goes to Cowrie's log
            log.err(None, f"{self.tool} of the persona ended on an internal error")
            code = self.status
        if code is None:
            # standard input is the terminal: its lines until ctrl-d
            self.tpot_lines, self.tpot_read_size = [], 0
            return
        if self.tpot_out:
            self.writeBytes(b"".join(self.tpot_out))
        if self.tpot_err:
            self.errorWrite("".join(self.tpot_err))
        self.exit_code = code
        self.exit()

    def tpot_main(self, terminal: bytes | None) -> int | None:
        raise NotImplementedError

    def lineReceived(self, line: str) -> None:
        self.protocol.events.dispatch("cowrie.command.input", "INPUT (%(realm)s): %(input)s",
                                      realm=self.tool, input=line)
        if self.tpot_lines is None:
            return
        data = line.encode("utf-8", "surrogateescape") + b"\n"
        if self.tpot_read_size + len(data) <= TPOT_TEXT_INPUT:
            self.tpot_lines.append(data)
            self.tpot_read_size += len(data)

    def eofReceived(self) -> None:
        if self.tpot_lines is None:
            self.exit()
            return
        data, self.tpot_lines = b"".join(self.tpot_lines), None
        if not data and hasattr(self.protocol, "execcmd"):
            # an exec channel buffers its stdin on the protocol, it does not arrive as lines
            data = bytes(getattr(self.protocol, "input_data", b"") or b"")[:TPOT_TEXT_INPUT]
        self.tpot_run(data)

    def tpot_late(self) -> bool:
        """The time of the command is used up."""
        return time.monotonic() > self.tpot_deadline

    def tpot_write_file(self, name: str, data: bytes) -> str:
        """Write data as a file of the emulated file system (sort -o); the error text, "" if written."""
        path = self.fs.resolve_path(name, self.cwd)
        if self.fs.isdir(path):
            return "Is a directory"
        if not self.fs.isdir(posixpath.dirname(path)):
            return "No such file or directory"
        try:
            self.fs.mkfile(path, self.user["uid"], self.user["gid"], 0, 0o644)
        except Exception:  # noqa: BLE001 - the file system refuses the path
            return "No such file or directory"
        node = self.fs.getfile(path)
        if isinstance(node, list):
            node[tpot_shell_fs.A_CONTENTS] = bytes(data[:TPOT_TEXT_INPUT])
        self.fs.update_size(path, len(data))
        return ""

    def tpot_emit(self, data) -> bool:
        """Output, at most TPOT_TEXT_OUTPUT bytes; False once the limit is reached."""
        if isinstance(data, str):
            data = data.encode("utf-8", "surrogateescape")
        room = TPOT_TEXT_OUTPUT - self.tpot_size
        if room <= 0:
            return False
        self.tpot_out.append(data[:room])
        self.tpot_size += min(len(data), room)
        return len(data) <= room

    def tpot_stdin(self, terminal: bytes | None, limit: int = TPOT_TEXT_INPUT) -> bytes | None:
        """The data of standard input: the pipe, the terminal's lines, None while the terminal is open."""
        if terminal is not None:
            return terminal
        if self.input_data is not None:
            return bytes(self.input_data[:limit])
        return None

    def tpot_file(self, name: str, limit: int = TPOT_TEXT_INPUT, real: bool = False):
        """(kind, data, size) of a file operand: kind file, dir or missing; the size of /proc and /sys
        files is 0, as stat gives it. real: (kind, data, size, length of the whole file)."""
        path = self.fs.resolve_path(name, self.cwd)
        try:
            if self.fs.isdir(path):
                return ("dir", b"", 0, 0) if real else ("dir", b"", 0)
            whole = self.fs.file_contents(path)
        except Exception:  # noqa: BLE001 - a missing or unreadable file of the emulated file system
            return ("missing", b"", 0, 0) if real else ("missing", b"", 0)
        whole = whole or b""
        data = bytes(whole[:limit])
        size = 0 if path.startswith(("/proc/", "/sys/")) else len(whole)
        return ("file", data, size, len(whole)) if real else ("file", data, size)

    # --- options ---------------------------------------------------------------------------

    def tpot_try(self) -> str:
        return f"Try '{self.tool} --help' for more information.\n"

    def tpot_gnu_options(self, args: list[str], negative_numbers: bool = False):
        """getopt_long of glibc: options and operands in any order (unless shorts starts with "+"),
        unique prefixes of long options, "--" ends the options. negative_numbers: an operand like
        -1 or -.5 ends the options (seq). Returns ([(name, value)], operands)."""
        found, operands, index = [], [], 0
        permute = not self.shorts.startswith("+")
        shorts = self.shorts.lstrip("+")
        while index < len(args):
            arg = args[index]
            index += 1
            if arg == "--":
                operands += args[index:]
                break
            if negative_numbers and re.match(r"-[0-9.]", arg):
                operands += args[index - 1:]
                break
            if not arg.startswith("-") or arg == "-":
                operands.append(arg)
                if not permute:
                    operands += args[index:]
                    break
                continue
            if arg.startswith("--"):
                word, eq, value = arg[2:].partition("=")
                names = [name for name in self.longs if name.startswith(word)]
                if word in self.longs:
                    names = [word]
                if not names:
                    raise TpotTextStop(f"{self.tool}: unrecognized option '{arg}'\n" + self.tpot_try(), self.status)
                if len(names) > 1 and len({self.longs[n] for n in names}) > 1:
                    raise TpotTextStop(f"{self.tool}: option '{arg.partition('=')[0]}' is ambiguous; possibilities:"
                                       + "".join(f" '--{n}'" for n in names) + "\n" + self.tpot_try(), self.status)
                name = names[0]
                key, takes = self.longs[name]
                if eq and not takes:
                    raise TpotTextStop(f"{self.tool}: option '--{name}' doesn't allow an argument\n" + self.tpot_try(),
                                       self.status)
                if takes == 1 and not eq:
                    if index >= len(args):
                        raise TpotTextStop(f"{self.tool}: option '--{name}' requires an argument\n"
                                           + self.tpot_try(), self.status)
                    value, index = args[index], index + 1
                found.append((key, value if (eq or takes == 1) else None))
                continue
            letters = arg[1:]
            for at, letter in enumerate(letters):
                spot = shorts.find(letter)
                if spot < 0 or letter == ":":
                    raise TpotTextStop(f"{self.tool}: invalid option -- '{letter}'\n" + self.tpot_try(), self.status)
                if shorts[spot + 1:spot + 2] == ":":
                    value = letters[at + 1:]
                    if not value:
                        if index >= len(args):
                            raise TpotTextStop(f"{self.tool}: option requires an argument -- '{letter}'\n"
                                               + self.tpot_try(), self.status)
                        value, index = args[index], index + 1
                    found.append((letter, value))
                    break
                found.append((letter, None))
        return found, operands

    def tpot_busybox_usage(self) -> str:
        raise NotImplementedError

    def tpot_usage_stop(self, prefix: str = "") -> TpotTextStop:
        banner = tpot_text("text_banner", "BusyBox v1.36.1")
        head = f"{banner} multi-call binary.\n" if self.version >= (1, 15) else f"{banner} multi-call binary\n"
        return TpotTextStop(f"{prefix}{head}\nUsage: {self.tool} {self.tpot_busybox_usage()}", self.status)

    def tpot_busybox_options(self, args: list[str]):
        """getopt32 of BusyBox: the getopt of the firmware's C library (its messages; musl does not
        reorder), then the usage text on an error."""
        found, operands, index = [], [], 0
        shorts = self.busybox_shorts.lstrip("+")
        permute = not self.busybox_shorts.startswith("+") and self.libc != "musl"
        while index < len(args):
            arg = args[index]
            index += 1
            if arg == "--":
                operands += args[index:]
                break
            if not arg.startswith("-") or arg == "-":
                operands.append(arg)
                if not permute:
                    operands += args[index:]
                    break
                continue
            if arg.startswith("--"):
                if self.libc == "musl":
                    text = f"{self.tool}: unrecognized option: {arg[2:].partition('=')[0]}\n"
                elif self.libc == "uclibc":
                    text = f"{self.tool}: unrecognized option `{arg}'\n"
                else:
                    text = f"{self.tool}: unrecognized option '{arg}'\n"
                if arg == "--help":
                    raise TpotTextStop(self.tpot_usage_stop().text, 0)
                raise self.tpot_usage_stop(text)
            letters = arg[1:]
            for at, letter in enumerate(letters):
                spot = shorts.find(letter)
                if spot < 0 or letter == ":":
                    text = {"musl": f"{self.tool}: unrecognized option: {letter}\n",
                            "uclibc": f"{self.tool}: invalid option -- {letter}\n"}.get(
                        self.libc, f"{self.tool}: invalid option -- '{letter}'\n")
                    raise self.tpot_usage_stop(text)
                if shorts[spot + 1:spot + 2] == ":":
                    value = letters[at + 1:]
                    if not value:
                        if index >= len(args):
                            text = {"musl": f"{self.tool}: option requires an argument: {letter}\n",
                                    "uclibc": f"{self.tool}: option requires an argument -- {letter}\n"}.get(
                                self.libc, f"{self.tool}: option requires an argument -- '{letter}'\n")
                            raise self.tpot_usage_stop(text)
                        value, index = args[index], index + 1
                    found.append((letter, value))
                    break
                found.append((letter, None))
        return found, operands

    # --- GNU texts -------------------------------------------------------------------------

    def tpot_gnu_help_lines(self) -> str:
        if self.version >= (9, 0):
            return ("      --help        display this help and exit\n"
                    "      --version     output version information and exit\n")
        return "      --help     display this help and exit\n      --version  output version information and exit\n"

    def tpot_gnu_footer(self) -> str:
        name = self.tool
        if self.version < (8, 23):
            return ("\nGNU coreutils online help: <http://www.gnu.org/software/coreutils/>\n"
                    + ("" if self.collate.startswith("en") else
                       f"Report {name} translation bugs to <http://translationproject.org/team/>\n")
                    + f"For complete documentation, run: info coreutils '{name} invocation'\n")
        if self.version < (8, 29):
            return ("\nGNU coreutils online help: <http://www.gnu.org/software/coreutils/>\n"
                    + ("" if self.collate.startswith("en") else
                       "Report translation bugs to <http://translationproject.org/team/>\n")
                    + f"Full documentation at: <http://www.gnu.org/software/coreutils/{name}>\n"
                    f"or available locally via: info '(coreutils) {name} invocation'\n")
        return ("\nGNU coreutils online help: <https://www.gnu.org/software/coreutils/>\n"
                + ("" if self.collate.startswith("en") else
                   "Report any translation bugs to <https://translationproject.org/team/>\n")
                + f"Full documentation <https://www.gnu.org/software/coreutils/{name}>\n"
                f"or available locally via: info '(coreutils) {name} invocation'\n")

    def tpot_gnu_version(self, authors: str) -> str:
        version = tpot_text("text_version", "9.1")
        packager = tpot_text("text_packager")
        return (f"{self.tool} (GNU coreutils) {version}\n" + (f"Packaged by {packager}\n" if packager else "")
                + f"Copyright (C) {tpot_text('text_year', '2022')} Free Software Foundation, Inc.\n"
                f"License GPLv3+: GNU GPL version 3 or later <{'http' if self.version < (8, 27) else 'https'}"
                "://gnu.org/licenses/gpl.html>.\nThis is free software: you are free to change and redistribute it.\n"
                f"There is NO WARRANTY, to the extent permitted by law.\n\nWritten by {authors}\n")


# --- wc ----------------------------------------------------------------------------------------

def tpot_wc_width(text: str) -> int:
    """The display width of a line (wc -L): tab stops of 8, wide characters 2, others of no width 0."""
    if TPOT_TEXT_ASCII_PRINT.fullmatch(text):
        return len(text)
    if "\t" not in text:
        return len(text) + len(TPOT_TEXT_WIDE.findall(text)) - len(TPOT_TEXT_ZERO_WIDTH.findall(text))
    width = 0
    for char in text:
        if char == "\t":
            width = (width // 8 + 1) * 8
        elif char.isprintable():
            if unicodedata.combining(char):
                continue
            width += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return width


def tpot_wc_piece(data: bytes, gnu: bool, version: tuple, unicode: bool, longest: bool) -> list[int]:
    """[lines, words, characters, bytes, longest line] of whole lines, as GNU wc (UTF-8) or BusyBox
    wc (bytes) counts them; the longest line only when asked for (-L)."""
    lines = data.count(b"\n")
    if data.isascii():
        words = len(data.split())
        if (gnu and version < (9, 5)) or not gnu:
            # a word has a printable character
            words -= len(TPOT_TEXT_C_CONTROL_RUNS.findall(data))
        chars = len(data)
        widest = 0
        if longest:
            for part in re.split(rb"[\n\r\f]" if gnu else rb"[\n\r\f\v]", data):
                widest = max(widest, len(part) if TPOT_TEXT_C_PLAIN.fullmatch(part) else tpot_wc_bb_width(part))
        return [lines, words, chars, len(data), widest]
    if gnu:
        text = data.decode("utf-8", "ignore")
        if TPOT_TEXT_PY_SPACE.search(text) or (version < (9, 5) and TPOT_TEXT_NOT_PRINTABLE.search(text)):
            runs = TPOT_TEXT_WORDS.findall(text)
            words = len(runs) if version >= (9, 5) else sum(1 for run in runs if any(c.isprintable() for c in run))
        else:
            words = len(text.split())
        widest = max((tpot_wc_width(part) for part in TPOT_TEXT_LINE_ENDS.split(text)), default=0) if longest else 0
        return [lines, words, len(text), len(data), widest]
    words = len(data.split()) - len(TPOT_TEXT_C_NO_PRINT_RUNS.findall(data))
    chars = len(data.translate(None, TPOT_TEXT_CONTINUATION)) if unicode else len(data)
    widest = 0
    if longest:
        for part in re.split(rb"[\n\r\f\v]", data):
            widest = max(widest, tpot_wc_bb_width(part))
    return [lines, words, chars, len(data), widest]


def tpot_wc_bb_width(part: bytes) -> int:
    """The longest-line count of BusyBox wc for one line: printable ASCII 1, tab to the next 8."""
    part = part.translate(None, TPOT_TEXT_NON_PRINT_BYTES)
    if b"\t" not in part:
        return len(part)
    position = 0
    for at, piece in enumerate(part.split(b"\t")):
        if at:
            position = (position | 7) + 1
        position += len(piece)
    return position


def tpot_wc_counts(data: bytes, gnu: bool, version: tuple, unicode: bool, longest: bool = True,
                   posix: bool = False, late=None) -> list[int]:
    """The counts of the data in pieces of at most TPOT_WC_PIECE bytes, cut after a line end, a blank
    or at least between characters (a word cut in two counts once); in the POSIX locale GNU wc
    counts bytes as characters. late(): the time is used up, the rest is not counted."""
    totals, at = [0, 0, 0, 0, 0], 0
    tail, data = data[TPOT_WC_INPUT:TPOT_WC_TAIL], data[:TPOT_WC_INPUT]
    while at < len(data):
        end = min(len(data), at + TPOT_WC_PIECE)
        joined = False
        if end < len(data):
            cut = max(data.rfind(b"\n", at, end), data.rfind(b" ", at, end), data.rfind(b"\t", at, end))
            if cut > at:
                end = cut + 1
            else:
                while end > at + 1 and 0x80 <= data[end] < 0xC0:
                    end -= 1
                joined = True
        piece = tpot_wc_piece(data[at:end], gnu, version, unicode, longest)
        if joined and data[end:end + 1] not in (b" ", b"\t", b"\n", b"\v", b"\f", b"\r"):
            piece[1] -= 1
        if posix:
            piece[2] = piece[3]
        totals = [a + b for a, b in zip(totals[:4], piece[:4])] + [max(totals[4], piece[4])]
        at = end
        if late is not None and late():
            return totals
    if tail:
        # the rest of a large input: lines, words between ASCII blanks, characters, bytes
        if data and data[-1:] not in (b" ", b"\t", b"\n") and tail[:1] not in (b" ", b"\t", b"\n"):
            totals[1] -= 1
        chars = len(tail) if (posix or (not gnu and not unicode)) else len(tail.translate(None, TPOT_TEXT_CONTINUATION))
        totals = [totals[0] + tail.count(b"\n"), totals[1] + len(tail.split()), totals[2] + chars,
                  totals[3] + len(tail), totals[4]]
    return totals


class Command_tpot_wc(TpotTextTool):
    """wc of the persona: GNU coreutils or BusyBox."""

    tool = "wc"
    shorts = "clLmw"
    longs = {"bytes": ("c", 0), "chars": ("m", 0), "lines": ("l", 0), "files0-from": ("files0", 1),
             "max-line-length": ("L", 0), "words": ("w", 0), "total": ("total", 1), "help": ("help", 0),
             "version": ("version", 0)}

    @property
    def busybox_shorts(self) -> str:
        return "lwcL" if self.version < (1, 15) else "lwmcL"

    def tpot_busybox_usage(self) -> str:
        if self.version < (1, 15):
            return ("[OPTION]... [FILE]...\n\n"
                    "Print line, word, and byte counts for each FILE, and a total line if\n"
                    "more than one FILE is specified. With no FILE, read standard input.\n\n"
                    "Options:\n\t-c\tPrint the byte counts\n\t-l\tPrint the newline counts\n"
                    "\t-L\tPrint the length of the longest line\n\t-w\tPrint the word counts\n")
        each = "FILEs" if self.version >= (1, 35) else "each FILE"
        return (f"[-c{'m' if self.unicode else ''}lwL] [FILE]...\n\n"
                f"Count lines, words, and bytes for {each} (or stdin)\n\n\t-c\tCount bytes\n"
                + ("\t-m\tCount characters\n" if self.unicode else "")
                + "\t-l\tCount newlines\n\t-w\tCount words\n\t-L\tPrint longest line length\n")

    def tpot_gnu_help(self) -> str:
        v = self.version
        if v < (8, 23):
            words = ("more than one FILE is specified.  With no FILE, or when FILE is -,\n"
                     "read standard input.  A word is a non-zero-length sequence of characters\n"
                     "delimited by white space.\n")
        elif v >= (9, 5):
            words = ("more than one FILE is specified.  A word is a nonempty sequence of non white\n"
                     "space delimited by white space characters or by start or end of input.\n\n"
                     "With no FILE, or when FILE is -, read standard input.\n\n")
        else:
            words = ("more than one FILE is specified.  A word is a non-zero-length sequence of\n"
                     + ("printable characters" if v >= (9, 0) else "characters") + " delimited by white space.\n\n"
                     "With no FILE, or when FILE is -, read standard input.\n\n")
        longest = "print the length of the longest line" if v < (8, 23) else "print the maximum display width"
        total = ("      --total=WHEN       when to print a line with total counts;\n"
                 "                           WHEN can be: auto, always, only, never\n") if v >= (9, 2) else ""
        return ("Usage: wc [OPTION]... [FILE]...\n  or:  wc [OPTION]... --files0-from=F\n"
                "Print newline, word, and byte counts for each FILE, and a total line if\n" + words +
                "The options below may be used to select which counts are printed, always in\n"
                "the following order: newline, word, character, byte, maximum line length.\n"
                "  -c, --bytes            print the byte counts\n  -m, --chars            print the character counts\n"
                "  -l, --lines            print the newline counts\n"
                "      --files0-from=F    read input from the files specified by\n"
                "                           NUL-terminated names in file F;\n"
                "                           If F is - then read names from standard input\n"
                f"  -L, --max-line-length  {longest}\n  -w, --words            print the word counts\n"
                + total + self.tpot_gnu_help_lines() + self.tpot_gnu_footer())

    def tpot_main(self, terminal: bytes | None) -> int | None:
        args = list(self.args)
        if self.gnu:
            options, names = self.tpot_gnu_options(args)
        else:
            options, names = self.tpot_busybox_options(args)
        chosen, total, files0 = set(), "auto", None
        for key, value in options:
            if key == "help":
                self.tpot_emit(self.tpot_gnu_help())
                return 0
            if key == "version":
                self.tpot_emit(self.tpot_gnu_version("Paul Rubin and David MacKenzie."))
                return 0
            if key == "total":
                if self.version < (9, 2):
                    raise TpotTextStop(f"wc: unrecognized option '--total={value}'\n" + self.tpot_try(), 1)
                matches = [w for w in ("auto", "always", "only", "never") if w.startswith(value or "\0")]
                if len(matches) != 1:
                    raise TpotTextStop(f"wc: invalid argument {tpot_text_quote(value)} for '--total'\n"
                                       "Valid arguments are:\n  - 'auto'\n  - 'always'\n  - 'only'\n  - 'never'\n"
                                       + self.tpot_try(), 1)
                total = matches[0]
            elif key == "files0":
                files0 = value
            else:
                chosen.add(key)
        order = [k for k in "lwmcL" if k in chosen] or ["l", "w", "c"]
        if files0 is not None:
            if names:
                raise TpotTextStop(f"wc: extra operand {tpot_text_quote(names[0])}\n"
                                   "file operands cannot be combined with --files0-from\n" + self.tpot_try(), 1)
            kind, data, _ = self.tpot_file(files0) if files0 != "-" else ("file", self.tpot_stdin(terminal), 0)
            if data is None:
                return None
            if kind != "file":
                raise TpotTextStop(f"wc: cannot open {tpot_text_quote(files0)} for reading: "
                                   + ("Is a directory" if kind == "dir" else "No such file or directory") + "\n", 1)
            names = [n.decode("utf-8", "surrogateescape") for n in data.split(b"\0") if n]
        operands = names or [None]
        if any(n in (None, "-") for n in operands) and self.tpot_stdin(terminal) is None:
            return None
        rows, code, sizes, stat_ok = [], 0, 0, []
        for name in operands:
            if name in (None, "-"):
                kind, data, size = "stdin", self.tpot_stdin(terminal, TPOT_WC_TAIL), 0
                length = len(data)
            else:
                kind, data, size, length = self.tpot_file(name, TPOT_WC_TAIL, real=True)
            if kind == "missing":
                if self.gnu:
                    shown = tpot_text_quotef(name) if self.version >= (8, 23) else name
                else:
                    shown = name
                rows.append((None, f"wc: {shown}: No such file or directory\n"))
                code = 1
                continue
            counts = tpot_wc_counts(data, self.gnu, self.version, self.unicode, "L" in order,
                                    self.gnu and self.collate == "posix", self.tpot_late)
            counts[3] = length
            error = ""
            if kind == "dir":
                counts, code = [0, 0, 0, 0, 0], 1
                error = f"wc: {name}: Is a directory\n"
            rows.append(((counts, name), error))
            stat_ok.append(kind)
            sizes += size
        totals = [0, 0, 0, 0, 0]
        for row, _ in rows:
            if row:
                totals = [a + b for a, b in zip(totals, row[0])]
        index = {"l": 0, "w": 1, "m": 2, "c": 3, "L": 4}
        many = len(operands) > 1
        if self.gnu:
            if len(operands) == 1 and len(order) == 1:
                width = 1
            else:
                width = len(str(sizes)) if sizes else 1
                if any(k in ("stdin", "dir") for k in stat_ok):
                    width = max(width, 7)
        for row, error in rows:
            if row:
                counts, name = row
                if self.gnu:
                    if total != "only":
                        line = " ".join(f"{counts[index[k]]:>{width}}" for k in order)
                        self.tpot_emit(line + (f" {name}" if name is not None else "") + "\n")
                else:
                    if len(order) == 1 and not many:
                        line = str(counts[index[order[0]]])
                    else:
                        line = " ".join(f"{counts[index[k]]:>9}" for k in order)
                    self.tpot_emit(line + (f" {name}" if name is not None else "") + "\n")
            if error:
                self.tpot_err.append(error)
        if self.gnu:
            if total == "always" or (total == "auto" and many):
                line = " ".join(f"{totals[index[k]]:>{width}}" for k in order)
                self.tpot_emit(line + " total\n")
            elif total == "only":
                self.tpot_emit(" ".join(str(totals[index[k]]) for k in order) + "\n")
        elif many:
            self.tpot_emit(" ".join(f"{totals[index[k]]:>9}" for k in order) + " total\n")
        return code


# --- seq ---------------------------------------------------------------------------------------

def tpot_seq_operand(text: str):
    """(value, precision, width) of a seq operand as GNU seq scans it: precision None for hex,
    infinities and NaN (%g then); None if it is no number."""
    match = TPOT_TEXT_GNU_FLOAT.fullmatch(text)
    if not match:
        return None
    word = text.strip(" \t\n\v\f\r").lstrip("+")
    lower = word.lower()
    if "x" in lower or "inf" in lower or "nan" in lower:
        try:
            value = float.fromhex(word) if "x" in lower else float(word)
        except (ValueError, OverflowError):
            return None
        return value, None, len(word)
    mantissa, _, exponent = lower.partition("e")
    whole, dot, fraction = mantissa.partition(".")
    sign = "-" if whole.startswith("-") else ""
    whole = whole.lstrip("-")
    try:
        shift = int(exponent) if exponent else 0
    except ValueError:  # an exponent of more digits than int() converts: far out of range
        shift = -(10 ** 12) if exponent.startswith("-") else 10 ** 12
    precision = len(fraction) if dot else 0
    precision = precision - shift if shift < 0 else precision - min(precision, shift)
    precision = min(precision, TPOT_SEQ_DIGITS)
    significant = (whole + fraction).lstrip("0")
    if not significant:
        return Decimal(0), precision, len(word)
    leading = (len(whole.lstrip("0")) - 1 if whole.lstrip("0") else -(len(fraction) - len(fraction.lstrip("0")) + 1))
    magnitude = leading + shift
    if magnitude > TPOT_SEQ_EXPONENT or (magnitude == TPOT_SEQ_EXPONENT and
                                         significant[:21].ljust(21, "0") > "118973149535723176502"):
        return None  # beyond a long double: strtold overflows
    if magnitude < -TPOT_SEQ_EXPONENT - 30:
        return Decimal(0), precision, len(word)  # strtold gives 0
    return Decimal(f"{sign}{whole or '0'}.{fraction or '0'}e{shift}"), precision, len(word)


def tpot_strtod(text: str):
    """(value, rest) as C strtod reads the front of text; value None without a number."""
    match = TPOT_TEXT_STRTOD.match(text)
    if not match:
        return None, text
    word = match.group(1)
    try:
        value = float.fromhex(word) if "x" in word.lower() else float(word)
    except (ValueError, OverflowError):
        return None, text
    return value, text[match.end():]


class Command_tpot_seq(TpotTextTool):
    """seq of the persona: GNU coreutils or BusyBox."""

    tool = "seq"
    consumes_stdin = False
    shorts = "+f:s:w"
    longs = {"format": ("f", 1), "separator": ("s", 1), "equal-width": ("w", 0), "help": ("help", 0),
             "version": ("version", 0)}
    busybox_shorts = "+ws:"

    def tpot_busybox_usage(self) -> str:
        if self.version < (1, 15):
            return ("[-w] [-s SEP] [FIRST [INC]] LAST\n\nPrint numbers from FIRST to LAST, in steps of INC.\n"
                    "FIRST, INC default to 1\n\nOptions:\n\t-w\tPad to last with leading zeros\n"
                    "\t-s SEP\tString separator\n")
        return ("[-w] [-s SEP] [FIRST [INC]] LAST\n\nPrint numbers from FIRST to LAST, in steps of INC.\n"
                "FIRST, INC default to 1.\n\n\t-w\tPad to last with leading zeros\n\t-s SEP\tString separator\n")

    def tpot_gnu_help(self) -> str:
        zero = "" if self.version < (8, 23) else \
            "INCREMENT must not be 0; none of FIRST, INCREMENT and LAST may be NaN.\n"
        return ("Usage: seq [OPTION]... LAST\n  or:  seq [OPTION]... FIRST LAST\n"
                "  or:  seq [OPTION]... FIRST INCREMENT LAST\n"
                "Print numbers from FIRST to LAST, in steps of INCREMENT.\n\n"
                "Mandatory arguments to long options are mandatory for short options too.\n"
                "  -f, --format=FORMAT      use printf style floating-point FORMAT\n"
                "  -s, --separator=STRING   use STRING to separate numbers (default: \\n)\n"
                "  -w, --equal-width        equalize width by padding with leading zeroes\n"
                + self.tpot_gnu_help_lines()
                + "\nIf FIRST or INCREMENT is omitted, it defaults to 1.  That is, an\n"
                "omitted INCREMENT defaults to 1 even when LAST is smaller than FIRST.\n"
                "The sequence of numbers ends when the sum of the current number and\n"
                "INCREMENT would become greater than LAST.\n"
                "FIRST, INCREMENT, and LAST are interpreted as floating point values.\n"
                "INCREMENT is usually positive if FIRST is smaller than LAST, and\n"
                "INCREMENT is usually negative if FIRST is greater than LAST.\n" + zero
                + "FORMAT must be suitable for printing one argument of type 'double';\n"
                "it defaults to %.PRECf if FIRST, INCREMENT, and LAST are all fixed point\n"
                "decimal numbers with maximum precision PREC, and to %g otherwise.\n" + self.tpot_gnu_footer())

    def tpot_main(self, terminal: bytes | None) -> int | None:
        if not self.gnu:
            return self.tpot_busybox()
        options, operands = self.tpot_gnu_options(list(self.args), negative_numbers=True)
        fmt, sep, equal = None, "\n", False
        for key, value in options:
            if key == "help":
                self.tpot_emit(self.tpot_gnu_help())
                return 0
            if key == "version":
                self.tpot_emit(self.tpot_gnu_version("Ulrich Drepper."))
                return 0
            if key == "f":
                fmt = value
            elif key == "s":
                sep = value
            elif key == "w":
                equal = True
        if not operands:
            raise TpotTextStop("seq: missing operand\n" + self.tpot_try(), 1)
        if len(operands) > 3:
            raise TpotTextStop(f"seq: extra operand {tpot_text_quote(operands[3])}\n" + self.tpot_try(), 1)
        if fmt is not None and equal:
            raise TpotTextStop("seq: format string may not be specified when printing equal width strings\n"
                               + self.tpot_try(), 1)
        scanned = []
        for operand in operands:
            found = tpot_seq_operand(operand)
            shown = tpot_text_quote(operand) if self.version >= (8, 23) else operand
            if found is None:
                raise TpotTextStop(f"seq: invalid floating point argument: {shown}\n" + self.tpot_try(), 1)
            if found[0] != found[0]:
                raise TpotTextStop(f"seq: invalid {tpot_text_quote('not-a-number')} argument: {shown}\n"
                                   + self.tpot_try(), 1)
            scanned.append(found)
        one = (Decimal(1), 0, 1)
        first, step, last = (scanned + [None])[:1] and (
            (one, one, scanned[0]) if len(scanned) == 1 else
            (scanned[0], one, scanned[1]) if len(scanned) == 2 else tuple(scanned))
        if step[0] == 0 and self.version >= (8, 23):
            raise TpotTextStop(f"seq: invalid Zero increment value: {tpot_text_quote(operands[1])}\n"
                               + self.tpot_try(), 1)
        if fmt is not None:
            directives = [m for m in TPOT_TEXT_FORMAT.finditer(fmt) if m.group(4) != "%"]
            if not directives:
                raise TpotTextStop(f"seq: format {tpot_text_quote(fmt)} has no % directive\n", 1)
            if directives[0].group(4) not in tuple("aAeEfFgG"):
                bad = "%" + directives[0].group(1) + directives[0].group(2) + (
                    "." + directives[0].group(3) if directives[0].group(3) is not None else "") + directives[0].group(4)
                raise TpotTextStop(f"seq: format {tpot_text_quote(fmt)} has unknown {bad} directive\n", 1)
            if len(directives) > 1:
                raise TpotTextStop(f"seq: format {tpot_text_quote(fmt)} has too many % directives\n", 1)
        fixed = all(p is not None for _, p, _ in (first, step, last))
        precision = max(first[1], step[1]) if fixed else None
        width = 0
        if equal and fixed:
            width = max(len(f"{first[0]:.{precision}f}"), len(f"{last[0]:.{precision}f}"))

        def show(value) -> str:
            if fmt is not None:
                return self.tpot_printf(fmt, float(value))
            if not fixed:
                return "%g" % float(value)
            text = f"{value:.{precision}f}"
            if width and len(text) < width:
                negative = text.startswith("-")
                text = ("-" if negative else "") + text[1 if negative else 0:].rjust(width - negative, "0")
            return text

        with localcontext(Context(prec=10_000, Emax=99_999, Emin=-99_999)):
            start, increment, end = (x[0] if fixed else float(x[0]) for x in (first, step, last))
            index, value, wrote = 0, start, False
            while (increment < 0 and value >= end) or (increment >= 0 and value <= end):
                if not self.tpot_emit((sep if wrote else "") + show(value)):
                    return 0
                wrote = True
                index += 1
                if index >= TPOT_SEQ_NUMBERS or self.tpot_late():
                    return 0
                value = start + index * increment
        if wrote:
            self.tpot_emit("\n")
        return 0

    def tpot_printf(self, fmt: str, value: float) -> str:
        """The -f format with its one directive (C printf of a double)."""
        out, last = [], 0
        for match in TPOT_TEXT_FORMAT.finditer(fmt):
            out.append(fmt[last:match.start()])
            last = match.end()
            flags, width, precision, conversion = match.groups()
            if conversion == "%":
                out.append("%")
                continue
            # width and precision as far as the output can reach (printf pads beyond it alike)
            wide = min(int(width[:8]) if width else 0, TPOT_SEQ_DIGITS)
            digits = None if precision is None else min(int(precision[:8] or "0"), TPOT_SEQ_DIGITS)
            if conversion in "aA":
                text = float.hex(value)
                core = text.upper() if conversion == "A" else text
            else:
                core = ("%" + "".join(f for f in flags if f in "+ #") + ("" if digits is None else f".{digits}")
                        + conversion) % value
            if wide > len(core):
                room = wide - len(core)
                if "-" in flags:
                    core += " " * room
                elif "0" in flags and value == value and abs(value) != float("inf"):
                    head = core[:1] if core[:1] in "+- " else ""
                    core = head + "0" * room + core[len(head):]
                else:
                    core = " " * room + core
            out.append(core)
        out.append(fmt[last:])
        return "".join(out).replace("\\n", "\n")

    def tpot_busybox(self) -> int:
        options, operands = self.tpot_busybox_options(list(self.args))
        equal, sep = False, "\n"
        for key, value in options:
            if key == "w":
                equal = True
            elif key == "s":
                sep = value
        if not 1 <= len(operands) <= 3:
            raise self.tpot_usage_stop()
        values = []
        for operand in operands:
            value, rest = tpot_strtod(operand)
            if value is None or rest:
                raise self.tpot_usage_stop()
            values.append(value)
        first = increment = 1.0
        if len(values) == 3:
            first, increment, last = values
        elif len(values) == 2:
            first, last = values
        else:
            last = values[0]
        width, fraction = 0, 0
        for at, operand in enumerate(operands):
            dot = operand.find(".")
            whole = len(operand) if dot < 0 else dot
            width = max(width, whole)
            if at < len(operands) - 1:
                fraction = max(fraction, len(operand) - whole)
        if fraction:
            fraction -= 1
            if fraction:
                width += fraction + 1
        if not equal:
            width = 0
        count, value, prefix = 0, first, ""
        width, fraction = min(width, TPOT_SEQ_DIGITS), min(fraction, TPOT_SEQ_DIGITS)
        while (increment >= 0 and value <= last) or (increment < 0 and value >= last):
            if not self.tpot_emit(prefix + "%0*.*f" % (width, fraction, value)):
                return 0
            prefix = sep
            count += 1
            if count >= TPOT_SEQ_NUMBERS or self.tpot_late():
                return 0
            value = first + count * increment
        if count:
            self.tpot_emit("\n")
        return 0


# --- sort --------------------------------------------------------------------------------------

def tpot_en_key(text: str, old: bool = False):
    """The order of glibc's en_US.UTF-8 (en_GB) collation, its levels as a tuple: letters and
    digits without case and accents, the accents, the case (lower first), then every character
    with punctuation and blanks marked; glibc 2.28 and later order a line with punctuation before
    the same line without it, glibc 2.17 (old) after it."""
    marked, plain = (1, 0) if old else (0, 1)
    if text.isascii():
        letters = re.sub(r"[^0-9A-Za-z]++", "", text)
        last = tuple((plain, 0) if c.isalnum() else (marked, ord(c)) for c in text)
        return letters.lower(), "", letters.translate(TPOT_EN_CASE), last
    primary, accents, case, last = [], [], [], []
    for char in text:
        if char.isalnum():
            parts = unicodedata.normalize("NFD", char)
            primary.append(parts[0].lower())
            accents.append(parts[1:])
            case.append("1" if char.isupper() else "0")
            last.append((plain, 0))
        else:
            last.append((marked, ord(char)))
    return "".join(primary), "\0".join(accents), "".join(case), tuple(last)


TPOT_SORT_FIELDS = re.compile(rb"[ \t]*+[^ \t]*+")
TPOT_SORT_BB_FIELDS = re.compile(rb"[ \t\n\v\f\r]*+[^ \t\n\v\f\r]*+")
TPOT_SORT_NOT_DICTIONARY = re.compile(rb"[^ \t0-9A-Za-z]++")
TPOT_SORT_BB_NOT_DICTIONARY = re.compile(rb"[^ \t\n\v\f\r0-9A-Za-z]++")
TPOT_SORT_NOT_PRINTING = re.compile(rb"[^ -~]++")
TPOT_EN_CASE = str.maketrans({**{chr(c): "1" for c in range(65, 91)}, **{chr(c): "0" for c in range(97, 123)},
                              **{chr(c): "0" for c in range(48, 58)}})


def tpot_version_key(text: bytes):
    """A key in the order of version numbers: runs of digits as numbers, other runs by their
    characters ('~' first, letters before other characters)."""
    parts = []
    for match in re.finditer(rb"([^0-9]*+)([0-9]*+)", text):
        if not match.group(0):
            continue
        chars = tuple(-1 if b == 0x7E else b if 65 <= (b | 0x20) - 0 and chr(b).isalpha() else b + 256
                      for b in match.group(1))
        parts.append((chars + (0,), int(match.group(2)[:TPOT_TEXT_DIGITS] or b"0")))
    return parts


def tpot_decimal(sign: str, whole: str, fraction: str) -> Decimal:
    whole = whole.lstrip("0")[:TPOT_TEXT_DIGITS] or "0"
    fraction = fraction[:TPOT_TEXT_DIGITS]
    try:
        return Decimal(f"{sign}{whole}.{fraction or '0'}")
    except InvalidOperation:
        return Decimal(0)


class TpotSortKey:
    """One -k of GNU sort (or the whole line): fields and characters 0-based, flags of its letters."""

    def __init__(self) -> None:
        self.sword, self.schar, self.eword, self.echar = 0, 0, None, 0
        self.flags = set()
        self.own = False


class Command_tpot_sort(TpotTextTool):
    """sort of the persona: GNU coreutils (the order of its locale) or BusyBox (byte order)."""

    tool = "sort"
    status = 2
    shorts = "bcCdfghik:mMno:rRsS:t:T:uVy:z"
    longs = {"ignore-leading-blanks": ("b", 0), "check": ("check", 2), "compress-program": ("ignore", 1),
             "debug": ("ignore0", 0), "dictionary-order": ("d", 0), "files0-from": ("files0", 1),
             "general-numeric-sort": ("g", 0), "human-numeric-sort": ("h", 0), "ignore-case": ("f", 0),
             "ignore-nonprinting": ("i", 0), "key": ("k", 1), "merge": ("m", 0), "month-sort": ("M", 0),
             "numeric-sort": ("n", 0), "output": ("o", 1), "random-sort": ("R", 0), "random-source": ("ignore", 1),
             "reverse": ("r", 0), "sort": ("sort", 1), "stable": ("s", 0), "buffer-size": ("S", 1),
             "field-separator": ("t", 1), "temporary-directory": ("T", 1), "unique": ("u", 0),
             "version-sort": ("V", 0), "zero-terminated": ("z", 0), "parallel": ("ignore", 1),
             "batch-size": ("ignore", 1), "help": ("help", 0), "version": ("version", 0)}

    @property
    def busybox_shorts(self) -> str:
        return ("ngh" if self.version >= (1, 36) else "ng") + ("MV" if self.version >= (1, 33) else "M") + \
            "ucszbrdfimS:T:o:k:t:"

    def tpot_busybox_usage(self) -> str:
        v = self.version
        if v < (1, 26):
            return ("[-nrugMcszbdfimSTokt] [-o FILE] [-k start[.offset][opts][,end[.offset][opts]] [-t CHAR] "
                    "[FILE]...\n\nSort lines of text\n\n\t-b\tIgnore leading blanks\n\t-c\tCheck whether input is "
                    "sorted\n\t-d\tDictionary order (blank or alphanumeric only)\n\t-f\tIgnore case\n\t-g\tGeneral "
                    "numerical sort\n\t-i\tIgnore unprintable characters\n\t-k\tSort key\n\t-M\tSort month\n\t-n\t"
                    "Sort numbers\n\t-o\tOutput to file\n\t-k\tSort by key\n\t-t CHAR\tKey separator\n\t-r\tReverse "
                    "sort order\n\t-s\tStable (don't sort ties alphabetically)\n\t-u\tSuppress duplicate lines\n\t-z\t"
                    "Lines are terminated by NUL, not newline\n\t-mST\tIgnored for GNU compatibility\n")
        head = ("[-nru" + ("gh" if v >= (1, 36) else "g") + "Mcszbdfiokt] [-o FILE] [-k "
                + ("START[.OFS][OPTS][,END[.OFS][OPTS]]" if v >= (1, 33) else "start[.offset][opts][,end[.offset][opts]]")
                + " [-t CHAR] [FILE]...\n\nSort lines of text\n\n\t-o FILE\tOutput to FILE\n\t-c\tCheck whether input is"
                " sorted\n\t-b\tIgnore leading blanks\n\t-f\tIgnore case\n\t-i\tIgnore unprintable characters\n\t-d\t"
                "Dictionary order (blank or alphanumeric only)\n")
        if v < (1, 33):
            return head + ("\t-g\tGeneral numerical sort\n\t-M\tSort month\n\t-n\tSort numbers\n\t-t CHAR\tField "
                           "separator\n\t-k N[,M] Sort by Nth field\n\t-r\tReverse sort order\n\t-s\tStable (don't sort "
                           "ties alphabetically)\n\t-u\tSuppress duplicate lines\n\t-z\tLines are terminated by NUL, not "
                           "newline\n")
        return head + ("\t-n\tSort numbers\n\t-g\tGeneral numerical sort\n"
                       + ("\t-h\tSort human readable numbers (2K 1G)\n" if v >= (1, 36) else "")
                       + "\t-M\tSort month\n\t-V\tSort version\n\t-t CHAR\tField separator\n\t-k N[,M] Sort by Nth "
                       "field\n\t-r\tReverse sort order\n\t-s\tStable (don't sort ties alphabetically)\n\t-u\tSuppress "
                       "duplicate lines\n\t-z\t" + ("NUL terminated input and output" if v >= (1, 35) else
                                                   "Lines are terminated by NUL, not newline") + "\n")

    def tpot_gnu_help(self) -> str:
        v = self.version
        old = v < (8, 23)
        numeric = ("  -n, --numeric-sort          compare according to string numerical value;\n"
                   "                                see full documentation for supported strings\n") if v >= (9, 5) \
            else "  -n, --numeric-sort          compare according to string numerical value\n"
        unique = ("  -u, --unique              output only the first of lines with equal keys;\n"
                  "                              with -c, check for strict ordering\n") if v >= (9, 5) else \
            ("  -u, --unique              with -c, check for strict ordering;\n"
             "                              without -c, output only the first of an equal run\n")
        units = "Y, R, Q" if v >= (9, 2) else "Y"
        stdin = "" if old else "\nWith no FILE, or when FILE is -, read standard input.\n"
        return ("Usage: sort [OPTION]... [FILE]...\n  or:  sort [OPTION]... --files0-from=F\n"
                "Write sorted concatenation of all FILE(s) to standard output.\n" + stdin + "\n"
                "Mandatory arguments to long options are mandatory for short options too.\nOrdering options:\n\n"
                "  -b, --ignore-leading-blanks  ignore leading blanks\n"
                "  -d, --dictionary-order      consider only blanks and alphanumeric characters\n"
                "  -f, --ignore-case           fold lower case to upper case characters\n"
                "  -g, --general-numeric-sort  compare according to general numerical value\n"
                "  -i, --ignore-nonprinting    consider only printable characters\n"
                "  -M, --month-sort            compare (unknown) < 'JAN' < ... < 'DEC'\n"
                "  -h, --human-numeric-sort    compare human readable numbers (e.g., 2K 1G)\n" + numeric
                + ("  -R, --random-sort           sort by random hash of keys\n" if old else
                   "  -R, --random-sort           shuffle, but group identical keys.  See shuf(1)\n")
                + "      --random-source=FILE    get random bytes from FILE\n"
                "  -r, --reverse               reverse the result of comparisons\n"
                "      --sort=WORD             sort according to WORD:\n"
                "                                general-numeric -g, human-numeric -h, month -M,\n"
                "                                numeric -n, random -R, version -V\n"
                "  -V, --version-sort          natural sort of (version) numbers within text\n\n"
                "Other options:\n\n"
                "      --batch-size=NMERGE   merge at most NMERGE inputs at once;\n"
                "                            for more use temp files\n"
                "  -c, --check, --check=diagnose-first  check for sorted input; do not sort\n"
                "  -C, --check=quiet, --check=silent  like -c, but do not report first bad line\n"
                "      --compress-program=PROG  compress temporaries with PROG;\n"
                "                              decompress them with PROG -d\n"
                "      --debug               annotate the part of the line used to sort,\n"
                "                              and warn about questionable usage to stderr\n"
                "      --files0-from=F       read input from the files specified by\n"
                "                            NUL-terminated names in file F;\n"
                "                            If F is - then read names from standard input\n"
                "  -k, --key=KEYDEF          sort via a key; KEYDEF gives location and type\n"
                "  -m, --merge               merge already sorted files; do not sort\n"
                "  -o, --output=FILE         write result to FILE instead of standard output\n"
                "  -s, --stable              stabilize sort by disabling last-resort comparison\n"
                "  -S, --buffer-size=SIZE    use SIZE for main memory buffer\n"
                "  -t, --field-separator=SEP  use SEP instead of non-blank to blank transition\n"
                "  -T, --temporary-directory=DIR  use DIR for temporaries, not $TMPDIR or /tmp;\n"
                "                              multiple options specify multiple directories\n"
                "      --parallel=N          change the number of sorts run concurrently to N\n" + unique
                + ("  -z, --zero-terminated     end lines with 0 byte, not newline\n" if old else
                   "  -z, --zero-terminated     line delimiter is NUL, not newline\n")
                + self.tpot_gnu_help_lines()
                + "\nKEYDEF is F[.C][OPTS][,F[.C][OPTS]] for start and stop position, where F is a\n"
                "field number and C a character position in the field; both are origin 1, and\n"
                "the stop position defaults to the line's end.  If neither -t nor -b is in\n"
                "effect, characters in a field are counted from the beginning of the preceding\n"
                "whitespace.  OPTS is one or more single-letter ordering options [bdfgiMhnRrV],\n"
                "which override global ordering options for that key.  If no key is given, use\n"
                + ("the entire line as the key.\n" if old else
                   "the entire line as the key.  Use --debug to diagnose incorrect key usage.\n")
                + "\nSIZE may be followed by the following multiplicative suffixes:\n"
                f"% 1% of memory, b 1, K 1024 (default), and so on for M, G, T, P, E, Z, {units}.\n"
                + ("\nWith no FILE, or when FILE is -, read standard input.\n" if old else "")
                + "\n*** WARNING ***\nThe locale specified by the environment affects sort order.\n"
                "Set LC_ALL=C to get the traditional sort order that uses\nnative byte values.\n"
                + self.tpot_gnu_footer())

    # --- reading ---------------------------------------------------------------------------

    def tpot_inputs(self, names: list[str], terminal: bytes | None):
        """The lines of the operands (stdin for none or "-"); None while the terminal is open."""
        operands = names or ["-"]
        if "-" in operands and self.tpot_stdin(terminal) is None:
            return None
        lines, size = [], 0
        for name in operands:
            if name == "-":
                kind, data = "file", self.tpot_stdin(terminal)
            else:
                kind, data, _ = self.tpot_file(name)
            if kind == "missing":
                if self.gnu:
                    raise TpotTextStop(f"sort: cannot read: {tpot_text_quotef(name)}: No such file or directory\n", 2)
                raise TpotTextStop(f"sort: {name}: No such file or directory\n", 2)
            if kind == "dir":
                if self.gnu:
                    raise TpotTextStop(f"sort: read failed: {tpot_text_quotef(name)}: Is a directory\n", 2)
                continue
            size += len(data)
            parts = data.split(self.tpot_end)
            if parts and parts[-1] == b"":
                parts.pop()
            lines += parts
            if size > TPOT_SORT_BYTES or len(lines) > TPOT_SORT_LINES:
                raise TpotTextStop("sort: memory exhausted\n" if self.gnu else "sort: out of memory\n", 2)
        return lines

    def tpot_main(self, terminal: bytes | None) -> int | None:
        return self.tpot_gnu(terminal) if self.gnu else self.tpot_busybox(terminal)

    # --- GNU -------------------------------------------------------------------------------

    def tpot_gnu_key(self, spec: str) -> TpotSortKey:
        key = TpotSortKey()

        def count(text: str, at: int, what: str):
            match = re.match(r"[0-9]++", text[at:])
            if not match:
                raise TpotTextStop(f"sort: {what}: invalid count at start of {tpot_text_quote(text[at:])}\n", 2)
            return int(match.group()[:18]), at + match.end()

        def bad(what: str):
            return TpotTextStop(f"sort: {what}: invalid field specification {tpot_text_quote(spec)}\n", 2)

        def letters(at: int, end: bool) -> int:
            while at < len(spec) and spec[at] in "bdfgiMhnRrV":
                letter = spec[at]
                key.flags.add(("B" if end else "b") if letter == "b" else letter)
                key.own = True
                at += 1
            return at

        key.sword, at = count(spec, 0, "invalid number at field start")
        if key.sword == 0:
            raise bad("field number is zero")
        key.sword -= 1
        if spec[at:at + 1] == ".":
            key.schar, at = count(spec, at + 1, "invalid number after '.'")
            if key.schar == 0:
                raise bad("character offset is zero")
            key.schar -= 1
        at = letters(at, False)
        if spec[at:at + 1] == ",":
            key.eword, at = count(spec, at + 1, "invalid number after ','")
            if key.eword == 0:
                raise bad("field number is zero")
            key.eword -= 1
            if spec[at:at + 1] == ".":
                key.echar, at = count(spec, at + 1, "invalid number after '.'")
            at = letters(at, True)
        if at < len(spec):
            raise bad("stray character in field spec")
        return key

    def tpot_offsets(self, line: bytes, blanks: bytes = b" \t") -> list[int]:
        """The ends of the fields of a line (a field: blanks, then the rest) or the places of its
        separators, once per line."""
        cache = self.__dict__.setdefault("tpot_offset_cache", {})
        key = (id(line), blanks)
        if key not in cache:
            if self.tpot_tab is not None:
                cache[key] = [m.start() for m in re.finditer(re.escape(bytes([self.tpot_tab])), line)]
            else:
                pattern = TPOT_SORT_FIELDS if blanks == b" \t" else TPOT_SORT_BB_FIELDS
                cache[key] = [m.end() for m in pattern.finditer(line) if m.end() > m.start()]
            cache[key + ("line",)] = line
        return cache[key]

    def tpot_field_text(self, line: bytes, key: TpotSortKey) -> bytes:
        """begfield and limfield of GNU sort."""
        tab, end = self.tpot_tab, len(line)
        offsets = self.tpot_offsets(line)

        def skip_fields(at: int, fields: int, stop_after_tab: bool) -> int:
            if fields <= 0:
                return at
            if fields > len(offsets):
                return end
            if tab is not None:
                return offsets[fields - 1] + (1 if stop_after_tab else 0)
            return offsets[fields - 1]

        start = skip_fields(0, key.sword, True)
        if "b" in key.flags:
            while start < end and line[start] in (32, 9):
                start += 1
        start = min(end, start + key.schar)
        if key.eword is None:
            stop = end
        else:
            fields = key.eword + (1 if key.echar == 0 else 0)
            stop = skip_fields(0, fields, key.echar != 0)
            if key.echar:
                if "B" in key.flags:
                    while stop < end and line[stop] in (32, 9):
                        stop += 1
                stop = min(end, stop + key.echar)
        return line[start:max(start, stop)]

    def tpot_gnu_value(self, text: bytes, flags: set, salt: bytes):
        """The value of a key text that orders it by the key's type."""
        if "b" in flags and not self.tpot_keys_given:
            text = text.lstrip(b" \t")
        if "d" in flags:
            text = TPOT_SORT_NOT_DICTIONARY.sub(b"", text)
        if "i" in flags:
            text = TPOT_SORT_NOT_PRINTING.sub(b"", text)
        if "f" in flags:
            text = text.upper()
        if "n" in flags or "h" in flags:
            word = text.decode("latin-1")
            match = TPOT_TEXT_NUMBER.match(word.replace(",", "") if self.collate.startswith("en") else word)
            sign, whole, fraction = match.group(1), match.group(2), match.group(3) or ""
            if not whole and not fraction:
                number = Decimal(0)
            else:
                number = tpot_decimal(sign, whole, fraction)
            if "h" not in flags:
                return number
            rest = (word.replace(",", "") if self.collate.startswith("en") else word)[match.end():match.end() + 1]
            order = "KMGTPEZYRQ".find(rest.upper()) + 1 if rest and (rest in "kKMGTPEZYRQ") else 0
            return (-order if sign else order), number
        if "g" in flags:
            value, _ = tpot_strtod(text.decode("latin-1"))
            if value is None:
                return 0, 0.0
            if value != value:
                return 1, 0.0
            return 2, value
        if "M" in flags:
            head = text.lstrip(b" \t")[:3].upper().decode("latin-1")
            return TPOT_TEXT_MONTHS.index(head) + 1 if head in TPOT_TEXT_MONTHS else 0
        if "V" in flags:
            return tpot_version_key(text)
        if "R" in flags:
            import hashlib
            return hashlib.blake2b(salt + text, digest_size=16).digest()
        return self.tpot_text_value(text, "f" in flags)

    def tpot_text_value(self, text: bytes, folded: bool = False):
        """The order of a text in the persona's locale; Red Hat's coreutils compare a text folded
        by -f byte by byte (text_fold bytes)."""
        if self.collate.startswith("en") and not (folded and tpot_text("text_fold") == "bytes"):
            return tpot_en_key(text.decode("utf-8", "surrogateescape"), old=self.collate == "en-2.17")
        return text

    def tpot_gnu(self, terminal: bytes | None) -> int | None:
        options, names = self.tpot_gnu_options(list(self.args))
        flags, keys, check, unique, stable, tab, zero, files0 = set(), [], None, False, False, None, False, None
        for name, value in options:
            if name == "help":
                self.tpot_emit(self.tpot_gnu_help())
                return 0
            if name == "version":
                self.tpot_emit(self.tpot_gnu_version("Mike Haertel and Paul Eggert."))
                return 0
            if name in "bdfgiMhnRrV" and len(name) == 1:
                flags |= {"b", "B"} if name == "b" else {name}
            elif name == "sort":
                words = {"general-numeric": "g", "human-numeric": "h", "month": "M", "numeric": "n", "random": "R",
                         "version": "V"}
                found = [w for w in words if w.startswith(value)] if value else []
                if value in words:
                    found = [value]
                if len(found) != 1:
                    raise TpotTextStop(f"sort: invalid argument {tpot_text_quote(value)} for '--sort'\n"
                                       "Valid arguments are:\n  - 'general-numeric'\n  - 'human-numeric'\n"
                                       "  - 'month'\n  - 'numeric'\n  - 'random'\n  - 'version'\n" + self.tpot_try(), 2)
                flags.add(words[found[0]])
            elif name == "k":
                if len(keys) >= TPOT_SORT_KEYS:
                    raise TpotTextStop("sort: memory exhausted\n", 2)
                keys.append(self.tpot_gnu_key(value))
            elif name == "c":
                check = "diagnose"
            elif name == "C":
                check = "quiet"
            elif name == "check":
                choices = {"diagnose-first": "diagnose", "quiet": "quiet", "silent": "quiet"}
                found = [w for w in choices if w.startswith(value)] if value else ["diagnose-first"]
                if value in choices:
                    found = [value]
                if len(found) != 1:
                    raise TpotTextStop(f"sort: invalid argument {tpot_text_quote(value)} for '--check'\n"
                                       "Valid arguments are:\n  - 'diagnose-first'\n  - 'quiet', 'silent'\n"
                                       + self.tpot_try(), 2)
                check = choices[found[0]]
            elif name == "u":
                unique = True
            elif name == "s":
                stable = True
            elif name == "z":
                zero = True
            elif name == "t":
                if value == "":
                    raise TpotTextStop("sort: empty tab\n", 2)
                if value == "\\0":
                    value = "\0"
                if len(value.encode("utf-8", "surrogateescape")) != 1:
                    raise TpotTextStop(f"sort: multi-character tab {tpot_text_quote(value)}\n", 2)
                tab = value.encode("utf-8", "surrogateescape")[0]
            elif name == "files0":
                files0 = value
            elif name == "o":
                self.tpot_output = value
        self.tpot_keys_given = bool(keys)
        if not keys:
            # the whole line with the global options
            whole = TpotSortKey()
            whole.flags = set(flags)
            keys = [whole]
        for key in keys:
            if not key.own:
                key.flags |= flags
            kinds = sum(1 for k in "nghM" if k in key.flags) + (1 if key.flags & {"V", "R", "d", "i"} else 0)
            if kinds > 1:
                letters = "".join(k for k in "bdfghiMnRrV" if k in key.flags or (k == "b" and "B" in key.flags))
                raise TpotTextStop(f"sort: options '-{letters}' are incompatible\n", 2)
        if files0 is not None:
            kind, data, _ = self.tpot_file(files0) if files0 != "-" else ("file", self.tpot_stdin(terminal), 0)
            if data is None:
                return None
            if kind != "file":
                raise TpotTextStop(f"sort: cannot open {tpot_text_quote(files0)} for reading: No such file or "
                                   "directory\n", 2)
            names = [n.decode("utf-8", "surrogateescape") for n in data.split(b"\0") if n]
        if check and len(names) > 1:
            raise TpotTextStop(f"sort: extra operand {tpot_text_quote(names[1])} not allowed with -{check[0] if check == 'diagnose' else 'C'}\n"
                               .replace("-d\n", "-c\n").replace("-q\n", "-C\n") + self.tpot_try(), 2)
        self.tpot_tab, self.tpot_end = tab, b"\0" if zero else b"\n"
        lines = self.tpot_inputs(names, terminal)
        if lines is None:
            return None
        if len(lines) * len(keys) > TPOT_SORT_WORK:
            raise TpotTextStop("sort: memory exhausted\n", 2)
        reverse = "r" in flags
        import os
        salt = os.urandom(16)
        values = []
        for key in keys:
            column = []
            for at, line in enumerate(lines):
                if not at & 1023 and self.tpot_late():
                    raise TpotTextStop("sort: memory exhausted\n", 2)
                column.append(self.tpot_gnu_value(self.tpot_field_text(line, key) if self.tpot_keys_given else line,
                                                  key.flags, salt))
            values.append(column)
        last = None if (stable or unique) else [self.tpot_text_value(line) for line in lines]

        def composite(i: int):
            return tuple(column[i] for column in values)

        if check:
            for i in range(1, len(lines)):
                order = self.tpot_compare(i - 1, i, keys, values, last, reverse)
                if order > 0 or (unique and order == 0):
                    if check == "diagnose":
                        name = names[0] if names else "-"
                        self.tpot_err.append(f"sort: {name}:{i + 1}: disorder: "
                                             f"{lines[i].decode('utf-8', 'surrogateescape')}\n")
                    return 1
            return 0
        order = list(range(len(lines)))
        if last is not None:
            order.sort(key=last.__getitem__, reverse=reverse)
        for key, column in reversed(list(zip(keys, values))):
            order.sort(key=column.__getitem__, reverse="r" in key.flags)
        end = self.tpot_end
        previous, result = None, []
        for i in order:
            if unique:
                current = composite(i)
                if previous is not None and current == previous:
                    continue
                previous = current
            result.append(lines[i] + end)
        return self.tpot_result(result)

    def tpot_result(self, result: list[bytes]) -> int:
        """The sorted lines on stdout or into the file of -o."""
        target = self.__dict__.get("tpot_output")
        if target is None:
            for line in result:
                if not self.tpot_emit(line):
                    break
            return 0
        error = self.tpot_write_file(target, b"".join(result))
        if error:
            if self.gnu:
                raise TpotTextStop(f"sort: open failed: {tpot_text_quotef(target)}: {error}\n", 2)
            raise TpotTextStop(f"sort: can't open '{target}': {error}\n", 2)
        return 0

    def tpot_compare(self, a: int, b: int, keys, values, last, reverse) -> int:
        for key, column in zip(keys, values):
            x, y = column[a], column[b]
            if x != y:
                return (1 if x > y else -1) * (-1 if "r" in key.flags else 1)
        if last is None:
            return 0
        x, y = last[a], last[b]
        return 0 if x == y else (1 if x > y else -1) * (-1 if reverse else 1)

    # --- BusyBox ---------------------------------------------------------------------------

    def tpot_bb_key(self, spec: str):
        """A -k of BusyBox sort: [start word, start char, end word, end char] and its flags."""
        allowed = set("nghMbrdfi")
        ranges, flags, at, part = [0, 0, 0, 0], set(), 0, 0

        def number(at: int):
            match = re.match(r"[0-9]++", spec[at:])
            if not match or int(match.group()) == 0 or int(match.group()) > 0x7FFFFFFF:
                raise TpotTextStop("sort: bad field specification\n", 2)
            return int(match.group()), at + match.end()

        while at < len(spec):
            ranges[2 * part], at = number(at)
            if spec[at:at + 1] == ".":
                ranges[2 * part + 1], at = number(at + 1)
            while at < len(spec):
                letter = spec[at]
                if letter == "," and part == 0:
                    part, at = 1, at + 1
                    break
                if letter not in self.busybox_shorts.replace(":", ""):
                    raise TpotTextStop("sort: unknown key option\n", 2)
                if letter not in allowed:
                    raise TpotTextStop("sort: unknown sort type\n", 2)
                flags.add("B" if (part and letter == "b") else letter)
                at += 1
        return ranges, flags

    def tpot_bb_field(self, line: bytes, ranges, flags) -> bytes:
        """get_key of BusyBox sort."""
        if ranges == [1, 0, 0, 0] and not flags & set("bdfiB"):
            return line
        length, start, end = len(line), 0, 0
        offsets = self.tpot_offsets(line, b" \t\n\v\f\r")
        for j in (0, 1):
            if not ranges[2 * j]:
                end = length
            else:
                # the fields get_key skips (each at once; past the last one the line is used up)
                fields = ranges[2 * j] + j - 1
                if fields <= 0:
                    end = 0
                elif fields > len(offsets):
                    end = length
                elif self.tpot_tab is not None:
                    end = offsets[fields - 1] + 1
                    if j:
                        end -= 1
                else:
                    end = offsets[fields - 1]
            if not j:
                start = end
        if "b" in flags:
            while start < length and line[start] in b" \t\n\v\f\r":
                start += 1
        if "B" in flags:
            while end > start and line[end - 1] in b" \t\n\v\f\r":
                end -= 1
        if ranges[3]:
            end = min(ranges[3], length)
        if ranges[1]:
            start = min(start + ranges[1] - 1, length)
        text = line[start:max(start, end)]
        if "d" in flags:
            text = TPOT_SORT_BB_NOT_DICTIONARY.sub(b"", text)
        if "i" in flags:
            text = TPOT_SORT_NOT_PRINTING.sub(b"", text)
        if "f" in flags:
            text = text.upper()
        return text

    def tpot_bb_value(self, text: bytes, flags: set):
        word = text.decode("latin-1")
        if "n" in flags:
            value, _ = tpot_strtod(word)
            return 0.0 if value is None or value != value else value
        if "g" in flags or "h" in flags:
            value, rest = tpot_strtod(word)
            if value is None:
                return (0,)
            if value != value:
                return (1,)
            if "h" in flags:
                suffix = "kmgtpezy".find(rest[:1].lower()) if rest else -1
                if suffix > 0 and rest[:1] >= "a":
                    suffix = -1
                return 2, suffix, value
            return 2, value
        if "M" in flags:
            head = word.lstrip(" \t\n\v\f\r")[:3].upper()
            return TPOT_TEXT_MONTHS.index(head) if head in TPOT_TEXT_MONTHS else -1
        if "V" in flags:
            return tpot_version_key(text)
        return text

    def tpot_busybox(self, terminal: bytes | None) -> int | None:
        options, names = self.tpot_busybox_options(list(self.args))
        flags, keys, tab = set(), [], None
        for name, value in options:
            if name == "k":
                if len(keys) >= TPOT_SORT_KEYS:
                    raise TpotTextStop("sort: out of memory\n", 2)
                keys.append(value)
            elif name == "t":
                tab = value
            elif name == "o":
                self.tpot_output = value
            elif name not in ("S", "T"):
                flags.add(name)
        if "b" in flags:
            flags.add("B")
        if tab is not None:
            data = tab.encode("utf-8", "surrogateescape")
            if len(data) != 1:
                raise TpotTextStop("sort: bad -t parameter\n", 2)
            tab = data[0]
        parsed = [self.tpot_bb_key(spec) for spec in keys] or [([1, 0, 0, 0], set())]
        self.tpot_tab, self.tpot_end = tab, b"\0" if "z" in flags else b"\n"
        lines = self.tpot_inputs(names, terminal)
        if lines is None:
            return None
        if len(lines) * len(parsed) > TPOT_SORT_WORK:
            raise TpotTextStop("sort: out of memory\n", 2)
        values = []
        for ranges, own in parsed:
            use = own or flags
            column = []
            for at, line in enumerate(lines):
                if not at & 1023 and self.tpot_late():
                    raise TpotTextStop("sort: out of memory\n", 2)
                column.append(self.tpot_bb_value(self.tpot_bb_field(line, ranges, use), use))
            values.append((column, "r" in use))
        stable = "s" in flags
        unique = "u" in flags
        reverse = "r" in flags
        if "c" in flags:
            for i in range(1, len(lines)):
                order = 0
                for column, backwards in values:
                    if column[i - 1] != column[i]:
                        order = (1 if column[i - 1] > column[i] else -1) * (-1 if backwards else 1)
                        break
                if order == 0 and not stable:
                    order = (lines[i - 1] > lines[i]) - (lines[i - 1] < lines[i])
                    order = -order if reverse else order
                if order > (-1 if unique else 0):
                    self.tpot_err.append(f"Check line {i}\n")
                    return 1
            return 0
        order = list(range(len(lines)))
        if not stable:
            order.sort(key=lines.__getitem__, reverse=reverse)
        for column, backwards in reversed(values):
            order.sort(key=column.__getitem__, reverse=backwards)
        previous, result = None, []
        for i in order:
            if unique:
                current = tuple(column[i] for column, _ in values)
                if previous is not None and current == previous:
                    continue
                previous = current
            result.append(lines[i] + self.tpot_end)
        return self.tpot_result(result)


commands["/usr/bin/wc"] = Command_tpot_wc
commands["/bin/wc"] = Command_tpot_wc
commands["wc"] = Command_tpot_wc
commands["/usr/bin/sort"] = Command_tpot_sort
commands["/bin/sort"] = Command_tpot_sort
commands["sort"] = Command_tpot_sort
commands["/usr/bin/seq"] = Command_tpot_seq
commands["/bin/seq"] = Command_tpot_seq
commands["seq"] = Command_tpot_seq
'''


def patch_text_tools(cowrie_root):
    """wc, sort and seq of the persona (TEXT_COMMAND after the commands of wc.py): GNU coreutils or
    BusyBox, files of the emulated file system, a pipe or the terminal."""
    path = cowrie_root / "src" / "cowrie" / "commands" / "wc.py"
    replace_once(path, 'commands["/usr/bin/wc"] = Command_wc\ncommands["/bin/wc"] = Command_wc\n'
                       'commands["wc"] = Command_wc\n',
                 'commands["/usr/bin/wc"] = Command_wc\ncommands["/bin/wc"] = Command_wc\n'
                 'commands["wc"] = Command_wc\n' + TEXT_COMMAND)


def patch_prompt(cowrie_root):
    """[shell] prompt_style = redhat gives the bash PS1 of RHEL / CentOS,
    [\\u@\\h \\W]\\$ (short host name, basename of the cwd, ~ for home); anything
    else keeps Cowrie's Debian prompt \\u@\\h:\\w\\$."""
    path = cowrie_root / "src" / "cowrie" / "shell" / "honeypot.py"
    replace_once(
        path,
        """            prompt = f"{username}@{self.protocol.hostname}:{cwd}"
""",
        """            if CowrieConfig.get("shell", "prompt_style", fallback="debian") == "redhat":
                if self.cwd == home:
                    base = "~"
                elif self.cwd == "/":
                    base = "/"
                else:
                    base = posixpath.basename(self.cwd.rstrip("/"))
                short = self.protocol.hostname.split(".")[0]
                prompt = f"[{username}@{short} {base}]"
            else:
                prompt = f"{username}@{self.protocol.hostname}:{cwd}"
""",
    )


def patch_shell_errors(cowrie_root):
    """The shell names itself as the persona's login shell ([shell] shell_name: -ash, -sh, -vbash,
    -bash) and says a missing command as it does ([shell] not_found_style: ash "not found",
    bash "command not found"); BusyBox ash names no line under -c. Without the keys bash stays."""
    path = cowrie_root / "src" / "cowrie" / "shell" / "honeypot.py"
    replace_once(
        path,
        """            self.name = "-bash" if interactive else "bash"
""",
        """            login = CowrieConfig.get("shell", "shell_name", fallback="bash")
            self.name = f"-{login}" if interactive else login
""",
    )
    replace_once(
        path,
        """        if self.line_numbers:
            return f"{self.name}: line {self.current_line}: "
""",
        """        ash = CowrieConfig.get("shell", "not_found_style", fallback="bash") == "ash"
        if self.line_numbers and not (ash and self.syntax_label == "-c"):
            return f"{self.name}: line {self.current_line}: "
""",
    )
    replace_once(
        path,
        """        return f"{self.error_prefix()}{cmd}: command not found\\n"
""",
        """        if CowrieConfig.get("shell", "not_found_style", fallback="bash") == "ash":
            return f"{self.error_prefix()}{cmd}: not found\\n"
        return f"{self.error_prefix()}{cmd}: command not found\\n"
""",
    )


def patch_telnet_banner(cowrie_root):
    """The telnet banner expands the agetty escapes of /etc/issue.net (\\S, \\r, \\m ...),
    as a Red Hat login shows them; `cat /etc/issue.net` in the shell stays raw.
    Device personas have no /etc/issue.net: upstream already sends an empty
    telnet banner then, but logs a traceback on every start."""
    path = cowrie_root / "src" / "cowrie" / "telnet" / "factory.py"
    replace_once(path, "\nimport time\n", "\nimport re\nimport time\n")
    replace_once(
        path,
        "from cowrie.shell.honeyfs import read_honeyfs_bytes\n",
        "from cowrie.core.config import CowrieConfig\n"
        "from cowrie.shell.honeyfs import read_honeyfs_bytes\n",
    )
    replace_once(
        path,
        """

class HoneyPotTelnetFactory(protocol.ServerFactory):
""",
        """

def tpot_os_release() -> dict[str, str]:
    \"\"\"T-Pot: the emulated os-release as agetty reads it, empty without one.\"\"\"
    for relpath in ("etc/os-release", "usr/lib/os-release"):
        try:
            text = read_honeyfs_bytes(relpath).decode("utf-8", errors="replace")
        except FileNotFoundError:
            continue
        values = {}
        for line in text.splitlines():
            key, sep, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if not sep or not key or key.startswith("#"):
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\\"'":
                value = value[1:-1]
            values[key] = value
        return values
    return {}


def tpot_expand_issue(text: str) -> str:
    \"\"\"T-Pot: the agetty escapes of /etc/issue.net in the telnet banner. Unknown
    escapes stay as they are.\"\"\"
    os_release: dict[str, str] | None = None

    def escape(match: re.Match[str]) -> str:
        nonlocal os_release
        code = match.group(1)
        if code[0] == "S":
            if os_release is None:
                os_release = tpot_os_release()
            if match.group(2) is not None:
                return os_release.get(match.group(2), "")
            return os_release.get("PRETTY_NAME") or "Linux"
        values = {
            "\\\\": lambda: "\\\\",
            "s": lambda: "Linux",
            "r": lambda: CowrieConfig.get("shell", "kernel_version", fallback="5.10.0"),
            "v": lambda: CowrieConfig.get("shell", "kernel_build_string", fallback="#1 SMP"),
            "m": lambda: CowrieConfig.get("shell", "hardware_platform", fallback="x86_64"),
            "n": lambda: CowrieConfig.get("honeypot", "hostname", fallback="localhost"),
            "l": lambda: "pts/0",
        }
        if code in values:
            return values[code]()
        return match.group(0)

    return re.sub(r"\\\\(S\\{([A-Za-z0-9_]+)\\}|.)", escape, text, flags=re.DOTALL)


class HoneyPotTelnetFactory(protocol.ServerFactory):
""",
    )
    replace_once(
        path,
        """        try:
            self.banner = (
                read_honeyfs_bytes("etc/issue.net")
                .decode("utf-8", errors="replace")
                .encode("utf-8")
            )
        except FileNotFoundError:
            self._log.failure("ERROR: Failed to load /etc/issue.net")
            self.banner = b""
""",
        """        try:
            self.banner = tpot_expand_issue(
                read_honeyfs_bytes("etc/issue.net")
                .decode("utf-8", errors="replace")
            ).encode("utf-8")
        except FileNotFoundError:
            self._log.info("No /etc/issue.net, empty telnet banner")
            self.banner = b""
""",
    )


def patch_ssh_userauth(cowrie_root):
    """Device personas have no /etc/issue.net: upstream then sends no SSH banner, but logs a
    traceback for every connection; an info line instead (as patch_telnet_banner)."""
    path = cowrie_root / "src" / "cowrie" / "ssh" / "userauth.py"
    replace_once(
        path,
        """        except FileNotFoundError:
            self._log.failure("ERROR: Failed to load /etc/issue.net")
            return
""",
        """        except FileNotFoundError:
            self._log.info("No /etc/issue.net, no SSH banner")
            return
""",
    )


def main():
    parser = argparse.ArgumentParser(description="Patch Cowrie v3.1 persona command support.")
    parser.add_argument("--cowrie-root", required=True, type=Path)
    parser.add_argument("--self-test", action="store_true",
                        help="check the grep matcher of an already patched Cowrie in this Python")
    args = parser.parse_args()
    cowrie_root = args.cowrie_root.resolve()
    if args.self_test:
        grep_self_test(cowrie_root)
        return
    patch_protocol(cowrie_root)
    patch_fs_links(cowrie_root)
    patch_busybox(cowrie_root)
    patch_perl(cowrie_root)
    patch_python(cowrie_root)
    patch_git(cowrie_root)
    patch_sudo(cowrie_root)
    patch_apt(cowrie_root)
    patch_yum(cowrie_root)
    patch_dnf(cowrie_root)
    patch_which(cowrie_root)
    patch_ssh(cowrie_root)
    patch_ssh_factory(cowrie_root)
    patch_netstat(cowrie_root)
    patch_net_tools(cowrie_root)
    patch_service(cowrie_root)
    patch_systemctl(cowrie_root)
    patch_free(cowrie_root)
    patch_protocol_load(cowrie_root)
    patch_uptime(cowrie_root)
    patch_ps_top(cowrie_root)
    patch_grep(cowrie_root)
    patch_df(cowrie_root)
    patch_awk(cowrie_root)
    patch_text_tools(cowrie_root)
    patch_prompt(cowrie_root)
    patch_shell_errors(cowrie_root)
    patch_telnet_banner(cowrie_root)
    patch_ssh_userauth(cowrie_root)


if __name__ == "__main__":
    main()
