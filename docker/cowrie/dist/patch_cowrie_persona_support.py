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


def tpot_systemctl(cmd, args: list[str]) -> int:
    # one systemctl call on cmd (a HoneyPotCommand); returns the exit status
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
    if lines is not None and not lines.isdigit():
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
    found = {part: {str(k): str(v) for k, v in data.get(part, {}).items()} if isinstance(data.get(part), dict)
             else {} for part in ("installed", "available")}
    programs = data.get("programs") if isinstance(data.get("programs"), dict) else {}
    found["programs"] = {str(name): [str(path) for path in paths] for name, paths in programs.items()
                         if isinstance(paths, list)}
    return found


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
        """        # T-Pot: package names as apt takes them (Debian's form, an architecture after the colon), a
        # path is a .deb file; an installed package of the persona is already the newest version,
        # another one gets a version that fits the release
        names, errors = [], []
        for word in self.tpot_words[1:]:
            found = re.fullmatch(r"([a-z0-9][a-z0-9+.-]+)(?::[a-z0-9-]+)?", word)
            if "/" in word or word.endswith(".deb"):
                errors.append(f"E: Unsupported file {word} given on commandline\\n")
            elif not found:
                errors.append(f"E: Unable to locate package {word}\\n")
            elif found.group(1) not in names:
                names.append(found.group(1))
        known = tpot_apt_known()
        for y in names:
            if y and y not in known:
                self.packages[y] = {"version": tpot_apt_version(y), "size": tpot_apt_size(y)}
        totalsize: int = sum(self.packages[x]["size"] for x in self.packages)

        self.write("Reading package lists... Done\\n")
        self.write("Building dependency tree... Done\\n" if tpot_apt("apt_version", "") else "Building dependency tree\\n")
        self.write("Reading state information... Done\\n")
        if errors:
            for error in errors:
                self.errorWrite(error)
            self.exit(100)
            return
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
            # the programs of the package; one Cowrie has a command for answers with it
            for program in tpot_apt_data()["programs"].get(p) or [f"/usr/bin/{p}"]:
                name = program.rsplit("/", 1)[-1]
                self.fs.mkfile(
                    program,
                    self.user["uid"],
                    self.user["gid"],
                    random.randint(10000, 90000),
                    33188,
                )
                tpot_mark_installed(self.protocol, name, self.packages[p]["version"])
                if name not in self.protocol.commands:
                    self.protocol.commands[program] = Command_faked_package_class_factory.getCommand(name)
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
    if not isinstance(data.get("repos"), list):
        data["repos"] = []
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
                for program in entry.get("programs", []):
                    tpot_dnf_mark(self.protocol, program.rsplit("/", 1)[-1], entry["evr"])
                session["installed"][name] = {"evr": entry["evr"], "arch": entry["arch"],
                                              "from": "@" + entry["repo"], "summary": entry.get("summary", ""),
                                              "size": entry.get("size", 0), "programs": entry.get("programs", [])}
                session["removed"].discard(name)
        for program in made:
            # one Cowrie has a command for answers with it
            if program.rsplit("/", 1)[-1] not in self.protocol.commands:
                self.protocol.commands[program] = Command_faked_package_class_factory.getCommand(program.rsplit("/", 1)[-1])
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
        self.exit_code = self.tpot_rpm()

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


class Command_top(HoneyPotCommand):
    """T-Pot: top of the persona (see TOP_COMMAND)."""

    def start(self) -> None:
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
                    if letter == "w" and (not args or not args[0].isdigit()):
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
            try:
                count = int(value)
            except ValueError:
                count = -1
            if count < 0:
                self.errorWrite(f"top: bad iterations argument '{value}'\n")
                self.exit(1)
                return False
            self.limit = count
        elif letter == "d":
            try:
                delay = float(value.replace(",", "."))
            except ValueError:
                delay = -1.0
            if delay < 0 or delay != delay:
                self.errorWrite(f"top: bad delay interval '{value}'\n")
                self.exit(1)
                return False
            self.delay = delay
        elif letter == "p":
            for item in value.split(","):
                if item.strip().isdigit():
                    self.pids.add(int(item))
        elif letter in ("u", "U"):
            self.users.add(value)
        return True

    # --- frames -----------------------------------------------------------------------------

    def tpot_tick(self) -> None:
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
    patch_service(cowrie_root)
    patch_systemctl(cowrie_root)
    patch_free(cowrie_root)
    patch_protocol_load(cowrie_root)
    patch_uptime(cowrie_root)
    patch_ps_top(cowrie_root)
    patch_grep(cowrie_root)
    patch_prompt(cowrie_root)
    patch_shell_errors(cowrie_root)
    patch_telnet_banner(cowrie_root)
    patch_ssh_userauth(cowrie_root)


if __name__ == "__main__":
    main()
