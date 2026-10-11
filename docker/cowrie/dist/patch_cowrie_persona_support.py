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
        def tpot_real(name):
            parts = [part for part in name.split("/") if part]
            real = []
            for part in parts[:-1]:
                node = self.fs.getfile("/" + "/".join(real + [part]), follow_symlinks=False)
                if node is not None and node[fs.A_TYPE] == fs.T_LINK and node[fs.A_TARGET]:
                    real = [x for x in node[fs.A_TARGET].split("/") if x and x != "."]
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


def patch_busybox(cowrie_root):
    """busybox prints the help of the persona's BusyBox ([shell] busybox_help, a
    file of the runtime folder) on stdout, as busybox does, for no arguments and
    --help; applets run and `busybox ECCHI` says "ECCHI: applet not found" as
    upstream. Without the key upstream's help stays."""
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
TPOT_PERL_STOP = re.compile(r"[/`#<]|(?<![\w$@%&])(?:qq|qw|qx|qr|tr|q|m|s|y)\s*[^\w\s,;)=]")
TPOT_PERL_ITEM = r"""(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|-?(?:0|[1-9]\d{0,14})(?:\.\d{1,15})?|\$\w+|\$\{\w+\})"""
TPOT_PERL_LIST = re.compile(rf"\s*(\(\s*)?((?:{TPOT_PERL_ITEM}(?:\s*,\s*{TPOT_PERL_ITEM})*\s*,?)?)\s*(?(1)\))\s*", re.S)
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
    out, err, status = tpot_perl_code("\n".join(code), flags)
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
    out: list[bytes] = []
    err: list[str] = []
    budget = TPOT_OUTPUT_LIMIT
    for offset, text in statements:
        match = re.fullmatch(r"\s*(print|say|exit|die)\b(?:\s*(STDOUT|STDERR)\b(?!\s*[,(]))?(.*)", text, re.S)
        if not match:
            assign = re.fullmatch(r"\s*(?:(?:my|our|local)\s+)?\$(\w+)\s*=(?![=~])(.*)", text, re.S)
            if assign:
                try:
                    (names[assign.group(1)],) = tpot_perl_list(assign.group(2), names)
                except (TpotPerlUnknown, ValueError):
                    names.pop(assign.group(1), None)
            continue
        word, stream, rest = match.groups()
        if word == "exit":
            status = re.fullmatch(r"\s*\(?\s*(\d{1,9})?\s*\)?\s*", rest)
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
TPOT_PY2_PRINT = re.compile(r"(^|[;\n])([ \t]*)print[ \t]+(?![(=])([^;\n]*)")


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
    out, err, status = tpot_python_code(code, tpot_python_version())
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
    return tpot_python_statements(module.body)


def tpot_py2_print(match) -> str:
    return f"{match.group(1)}{match.group(2)}print({match.group(3).rstrip().rstrip(',')})"


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
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = tpot_dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def tpot_literal(node, names):
    if isinstance(node, ast.Name) and node.id in names:
        return names[node.id]
    try:
        return ast.literal_eval(node)
    except Exception:  # noqa: BLE001 - anything that is no literal
        return TPOT_UNKNOWN


def tpot_python_statements(body) -> tuple[str, str, int]:
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
            if func == "print":
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
    tpot_text(sep * max(0, len(parts) - 1) + end, budget)
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
    """which prints the first match on PATH (which without -a; /usr/bin and /bin are one
    directory of a merged /usr tree) and fails with 1 when a name is not found, as
    `which wget || which curl` of a bot expects."""
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
        """        # Look up each file
        missing = False
        for f in self.args:
            for path in self.environ["PATH"].split(":"):
                resolved = self.fs.resolve_path(f, path)

                if self.fs.exists(resolved):
                    self.write(f"{path}/{f}\\n")
                    break
            else:
                missing = True
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


def patch_sudo(cowrie_root):
    """sudo -V names [shell] sudo_version (1.9 adds the audit plugin); /usr/bin/sudo
    is the command too."""
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


def patch_apt(cowrie_root):
    """apt / apt-get of the persona: the version line ([shell] apt_version, deb_arch),
    `update` as root fetches the sources of the persona ([shell] apt_sources: "url suite"
    separated by commas), install takes them as its mirror and the arch of the persona;
    options (-y, -q) are no packages. Without the keys upstream's text stays."""
    path = cowrie_root / "src" / "cowrie" / "commands" / "apt.py"
    replace_once(path, *CONFIG_IMPORT)
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
        elif self.tpot_words[:1] == ["update"] and self.user["uid"] == 0:
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
    replace_once(
        path,
        """        for y in [re.sub("[^A-Za-z0-9]", "", x) for x in self.args[1:]]:
""",
        """        for y in [re.sub("[^A-Za-z0-9]", "", x) for x in self.tpot_words[1:]]:
""",
    )
    replace_once(
        path,
        """                f"Get:{i} http://ftp.debian.org stable/main {p} {self.packages[p]['version']} [{self.packages[p]['size']}.2kB]\\n"
""",
        """                f"Get:{i} {tpot_apt_sources()[0][0]} {tpot_apt_sources()[0][1]}/main "
                f"{tpot_apt('deb_arch', 'i386')} {p} {self.packages[p]['version']} [{self.packages[p]['size']}.2kB]\\n"
""",
    )
    replace_once(
        path,
        """                f"Unpacking {p} (from .../archives/{p}_{self.packages[p]['version']}_i386.deb) ...\\n"
""",
        """                f"Unpacking {p} (from .../archives/{p}_{self.packages[p]['version']}_"
                f"{tpot_apt('deb_arch', 'i386')}.deb) ...\\n"
""",
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
            case ["update" | "upgrade" | "check-update", *_] if self.user["uid"] == 0:
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


def patch_grep(cowrie_root):
    """grep as GNU grep for the options bots use: -c prints the number of selected
    lines (name:count per file for several files; `grep -c processor /proc/cpuinfo`),
    -v inverts, -i ignores case, -q prints nothing; -m counts per file. The exit
    status is 0 for a selected line, 1 for none and 2 for an error (missing file,
    directory, invalid option), with -q a selected line wins over an error.
    The empty string after the last newline is no line."""
    path = cowrie_root / "src" / "cowrie" / "commands" / "fs.py"
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
    ignore_case: bool = False
    quiet: bool = False
    failed: bool = False

    def write_count(self, label: str | None = None) -> None:
        if self.quiet:
            return
        prefix = f"{label}:" if label is not None else ""
        self.write(f"{prefix}{self.match_count}\\n")

    def exit_status(self) -> int:
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
        """        return re.compile(bmatch)
""",
        """        return re.compile(bmatch, re.IGNORECASE if self.ignore_case else 0)
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
        """        lines = contents.split(b"\\n")
        if lines[-1] == b"":
            lines.pop()
        for line in lines:
            if self.max_count is not None and self.match_count >= self.max_count:
                break
            if (matcher.search(line) is not None) != self.invert:
                self.matched = True
                self.match_count += 1
                if not self.count_only and not self.quiet:
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
                pnames = self.check_arguments("grep", [arg])
                if not pnames:
                    self.failed = True
                for pname in pnames:
                    self.grep_get_contents(pname, self.match, arg, len(files) > 1)
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
        """                    self.grep_application(f.read(), self.match)
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
    args = parser.parse_args()
    cowrie_root = args.cowrie_root.resolve()
    patch_protocol(cowrie_root)
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
    patch_free(cowrie_root)
    patch_grep(cowrie_root)
    patch_prompt(cowrie_root)
    patch_telnet_banner(cowrie_root)


if __name__ == "__main__":
    main()
