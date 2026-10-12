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

        def skip_python_command(name):
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
        """        relpath = path.lstrip("/")
        txtcmds_path = CowrieConfig.get("honeypot", "txtcmds_path", fallback="")
        if txtcmds_path:
            operator_path = Path(txtcmds_path) / relpath
            if operator_path.is_file():
                return self.txtcmd(operator_path.read_bytes())

        # T-Pot: the spellings of path through the directory links of the tree
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
        if txtcmds_path:
            for spelling in spellings[1:]:
                operator_path = Path(txtcmds_path) / spelling.lstrip("/")
                if operator_path.is_file():
                    return self.txtcmd(operator_path.read_bytes())
""",
    )
    replace_once(
        path,
        """        if path in self.commands:
            return self.commands[path]
""",
        """        for spelling in spellings:
            if spelling in self.commands and not skip_python_command(spelling):
                return self.commands[spelling]
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
TPOT_PERL_STOP = re.compile(r"[/`#<]|(?<![\w$@%&])(?:qq|qw|qx|qr|tr|q|m|s|y)\s*+[^\w\s,;)=]")
TPOT_PERL_ITEM = r"""(?:"[^"\\]*+(?:\\.[^"\\]*+)*+"|'[^'\\]*+(?:\\.[^'\\]*+)*+'|-?(?:0|[1-9]\d{0,14})(?:\.\d{1,15})?|\$\w+|\$\{\w+\})"""
# possessive quantifiers (*+): long whitespace is read once, never backtracked
TPOT_PERL_LIST = re.compile(
    rf"\s*+(\(\s*+)?((?:{TPOT_PERL_ITEM}(?:\s*+,\s*+{TPOT_PERL_ITEM})*+\s*+,?)?)\s*+(?(1)\))\s*+", re.S
)
TPOT_PERL_ITEMS = re.compile(TPOT_PERL_ITEM, re.S)
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
    cmd.writeBytes(out)
    cmd.errorWrite(err)
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
    if out:
        cmd.write(out)
    if err:
        cmd.errorWrite(err)
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
    return tpot_python_statements(module.body, python2 and not future)


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


def tpot_python_statements(body, print_tuple=False) -> tuple[str, str, int]:
    """print_tuple: print(a, b) prints the tuple (a, b), as a Python 2 print statement."""
    names: dict = {}
    out: list[str] = []
    err: list[str] = []
    budget = [TPOT_OUTPUT_LIMIT]
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
                    (err if printed[1] else out).append(printed[0])
            elif func in ("sys.stdout.write", "sys.stderr.write") and len(call.args) == 1 and not call.keywords:
                value = tpot_literal(call.args[0], names)
                if isinstance(value, str):
                    (err if func == "sys.stderr.write" else out).append(tpot_text(value, budget))
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
    """(text, to stderr) of a print() call with literal arguments, None otherwise."""
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
    return sep.join(parts) + end, stderr
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
    """git --version names [shell] git_version (upstream's 2.34.1 without it)."""
    path = cowrie_root / "src" / "cowrie" / "commands" / "git.py"
    replace_once(path, *CONFIG_IMPORT)
    replace_once(
        path,
        """            self.write("git version 2.34.1\\n")
""",
        """            version = CowrieConfig.get("shell", "git_version", fallback="2.34.1")
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


def patch_netstat(cowrie_root):
    path = cowrie_root / "src" / "cowrie" / "commands" / "netstat.py"
    replace_all(path, "@/com/ubuntu/upstart", "/run/systemd/private")
    replace_once(
        path,
        "Fred Baumgarten, Alan Cox, Bernd Eckenfels, Phil Blundell, Tuan Hoang and others\\n",
        "Fred Baumgarten, Alan Cox, Bernd Eckenfels, Tuan Hoang and others\\n",
    )


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


def tpot_list_units(cmd, data: dict, every: bool, legend: bool, kind: str | None) -> None:
    version = int(data.get("version", 252))
    units = [u for u in data["units"] if kind is None or u["name"].endswith("." + kind)]
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


def tpot_list_unit_files(cmd, data: dict, legend: bool) -> None:
    version = int(data.get("version", 252))
    units = sorted(data["units"], key=lambda u: u["name"].lower())
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


def tpot_systemctl(cmd, args: list[str]) -> int:
    # one systemctl call on cmd (a HoneyPotCommand); returns the exit status
    data = tpot_units()
    version = int(data.get("version", 252))
    words, every, legend, quiet, kind = [], False, True, False, None
    items = list(args)
    while items:
        arg = items.pop(0)
        if arg == "--version":
            cmd.write(f"systemd {version}" + (f" ({data['full']})" if data.get("full") else "") + "\n"
                      + (data.get("features", "") + "\n" if data.get("features") else ""))
            return 0
        if arg in ("-h", "--help"):
            cmd.write("systemctl [OPTIONS...] COMMAND ...\n\nQuery or send control commands to the system manager.\n")
            return 0
        if arg in ("-t", "--type") and items:
            kind = items.pop(0)
        elif arg.startswith("--type=") or (arg.startswith("-t") and len(arg) > 2):
            kind = arg.split("=", 1)[1] if arg.startswith("--type=") else arg[2:]
        elif arg.startswith("--state=") or arg.startswith("--output=") or arg.startswith("--lines=") \
                or arg.startswith("-n") or arg.startswith("-o"):
            continue
        elif arg in TPOT_SYSTEMCTL_FLAGS:
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
    verb, names = (words[0], words[1:]) if words else ("list-units", [])
    boot, now = cmd.protocol.boot_time(), time.time()
    host = cmd.protocol.hostname.split(".")[0]
    if verb == "list-units":
        tpot_list_units(cmd, data, every, legend, kind)
        return 0
    if verb == "list-unit-files":
        tpot_list_unit_files(cmd, data, legend)
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
                blocks.append("".join(line + "\n" for line in tpot_status_unit(cmd, data, unit, boot, now, host)))
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
        status = 0
        for name in names:
            name, unit = tpot_unit(data, name)
            if verb == "is-active":
                answer, ok = ("active", True) if unit else ("inactive", False)
            elif verb == "is-failed":
                answer, ok = ("active", False) if unit else ("inactive", False)
            elif unit is None:
                cmd.errorWrite(f"Failed to get unit file state for {name}: No such file or directory\n")
                status = 1
                continue
            else:
                answer, ok = unit.get("state", "enabled"), True
            if not quiet:
                cmd.write(answer + "\n")
            if not ok:
                status = 1 if verb == "is-failed" else 3
        return status
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
    replace_once(path, "import getopt\n", "import getopt\nimport json\nimport time\n")
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
    replace_once(path, "import getopt\n", "import getopt\nimport json\nfrom pathlib import Path\n")
    replace_once(
        path,
        """        try:
            optlist, _args = getopt.getopt(
                self.args[0:start_value], "bEeHhKknPSVva:C:g:i:l:p:r:s:t:U:u:"
            )
""",
        """        if any(arg in ("-l", "-ll", "--list") or (arg.startswith("-") and not arg.startswith("--")
                                                     and "l" in arg[1:] and arg.strip("-").isalpha())
               for arg in self.args[0:start_value]):
            self.tpot_list(parsed_arguments)
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
    """apt / apt-get of the persona: the version line ([shell] apt_version, deb_arch),
    `update` as root fetches the sources of the persona ([shell] apt_sources: "url suite"
    separated by commas), install takes them as its mirror and the arch of the persona,
    the versions of its packages ([shell] apt_packages) and for other packages one that
    fits the release ([shell] apt_fallback); options (-y, -q) are no packages. Without the
    keys upstream's text stays."""
    path = cowrie_root / "src" / "cowrie" / "commands" / "apt.py"
    replace_once(path, *CONFIG_IMPORT)
    replace_once(path, "import random\n", "import functools\nimport hashlib\nimport json\nimport random\n")
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
    return {part: {str(k): str(v) for k, v in data.get(part, {}).items()} if isinstance(data.get(part), dict)
            else {} for part in ("installed", "available")}


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
        # T-Pot: options (-y, -q, --no-install-recommends) are no command and no package
        self.tpot_words = [arg for arg in self.args if not arg.startswith("-")]
        if len(self.args) == 0:
            self.do_help()
        elif self.args[0] in ("-v", "--version"):
            self.do_version()
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
        """        # T-Pot: package names as apt takes them; an installed package of the persona is already
        # the newest version, another one gets a version that fits the release
        names = list(dict.fromkeys(re.sub("[^a-z0-9.+-]", "", x.lower()) for x in self.tpot_words[1:]))
        known = tpot_apt_known()
        for y in names:
            if y and y not in known:
                self.packages[y] = {"version": tpot_apt_version(y), "size": tpot_apt_size(y)}
        totalsize: int = sum(self.packages[x]["size"] for x in self.packages)

        self.write("Reading package lists... Done\\n")
        self.write("Building dependency tree... Done\\n" if tpot_apt("apt_version", "") else "Building dependency tree\\n")
        self.write("Reading state information... Done\\n")
        for y in names:
            if y in known:
                self.write(f"{y} is already the newest version ({known[y]}).\\n")
        if not self.packages:
            self.write("0 upgraded, 0 newly installed, 0 to remove and 259 not upgraded.\\n")
            self.exit()
            return
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
            yield self.sleep(1, 2)
        self.write(f"Fetched {totalsize} kB in 1s ({totalsize} kB/s)\\n")
        yield self.sleep(1, 2)
        for number, p in enumerate(self.packages):
            version = self.packages[p]["version"]
            self.write(f"Selecting previously unselected package {p}.\\n")
            if number == 0:
                self.write("(Reading database ... 177887 files and directories currently installed.)\\n")
            self.write(f"Preparing to unpack .../{p}_{version.replace(':', '%3a')}_{arch}.deb ...\\n")
            self.write(f"Unpacking {p} ({version}) ...\\n")
            yield self.sleep(1, 2)
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
        self.write(f"Processing triggers for man-db ({tpot_apt_version('man-db')}) ...\\n")
        self.exit()""",
    )


def patch_yum(cowrie_root):
    """yum 3 of CentOS 7: the plugins of the persona ([shell] yum_plugins), without
    the lines of plugins it does not load (priorities, kernel-module); -y installs
    without asking, options are no packages; `update` as root finds nothing to do."""
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
        """        if len(self.args) <= 1:
""",
        """        if len(self.tpot_words) <= 1:
""",
    )
    replace_once(
        path,
        """        for y in [re.sub("[^A-Za-z0-9]", "", x) for x in self.args[1:]]:
""",
        """        for y in [re.sub("[^A-Za-z0-9]", "", x) for x in self.tpot_words[1:]]:
""",
    )
    replace_once(
        path,
        """        self.write(
            f"{random.randint(200, 300)} packages excluded due to repository priority protections\\n"
        )
""",
        """        if "priorities" in tpot_yum_plugins():
            self.write(
                f"{random.randint(200, 300)} packages excluded due to repository priority protections\\n"
            )
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
    patch_which(cowrie_root)
    patch_ssh(cowrie_root)
    patch_netstat(cowrie_root)
    patch_service(cowrie_root)
    patch_systemctl(cowrie_root)
    patch_free(cowrie_root)
    patch_grep(cowrie_root)
    patch_prompt(cowrie_root)
    patch_shell_errors(cowrie_root)
    patch_telnet_banner(cowrie_root)


if __name__ == "__main__":
    main()
