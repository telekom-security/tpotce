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


@functools.cache
def tpot_builtins() -> frozenset[str] | None:
    \"\"\"T-Pot: the builtins of the persona's shell ([shell] builtins) on a firmware whose programs
    are only those of its image: a Python command answers a bare name only as one of them or as a
    program found on PATH. None without the key (upstream: every Python command answers).\"\"\"
    value = CowrieConfig.get("shell", "builtins", fallback=None)
    if value is None:
        return None
    return frozenset(name.strip() for name in value.split(",") if name.strip())
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

        builtins = tpot_builtins()
        if cmd in self.commands and not skip_python_command(cmd) and (builtins is None or cmd in builtins):
            return self.commands[cmd]
        # T-Pot: a builtin of the firmware's shell that Cowrie has no command of answers with the
        # txtcmd of its name (bin/<name>), without a program on PATH
        if builtins is not None and cmd in builtins and "/" not in cmd:
            txtcmds_dir = CowrieConfig.get("honeypot", "txtcmds_path", fallback="")
            builtin_path = Path(txtcmds_dir) / "bin" / cmd if txtcmds_dir else None
            if builtin_path is not None and builtin_path.is_file():
                return self.txtcmd(builtin_path.read_bytes())
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
        # T-Pot: a program of the firmware's PATH that Cowrie's Python answers by its name
        name = posixpath.basename(path)
        if builtins is not None and name in self.commands and not skip_python_command(name):
            return self.commands[name]
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
from cowrie.shell.command import tpot_metered

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
        # T-Pot: a one-liner counts for the session's time (TPOT_BUDGET)
        with tpot_metered(getattr(self, "protocol", None)):
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
from cowrie.shell.command import tpot_metered

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
        # T-Pot: a one-liner counts for the session's time (TPOT_BUDGET)
        with tpot_metered(getattr(self, "protocol", None)):
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
    with 1 when a name is not found, as `which wget || which curl` of a bot expects. On a device
    with folders of its own ([shell] which_dirs: program -> folder) a program of the table is named
    once, in its folder."""
    path = cowrie_root / "src" / "cowrie" / "commands" / "which.py"
    replace_once(
        path,
        "from cowrie.shell.command import HoneyPotCommand\n",
        """import functools
import time
from pathlib import Path

from cowrie.core.config import CowrieConfig
from cowrie.shell.command import HoneyPotCommand, tpot_deadline

# T-Pot: the folders of PATH and the names of one call which looks through; past them, or past the session's
#   time (TPOT_BUDGET), a name is not found
TPOT_PATH_ENTRIES = 256
TPOT_LOOKUP_NAMES = 256


@functools.cache
def tpot_which_dirs() -> dict[str, str]:
    \"\"\"T-Pot: {program: folders} of [shell] which_dirs (a file of "name folder,folder" lines), {}
    without.\"\"\"
    path = CowrieConfig.get("shell", "which_dirs", fallback="")
    if not path:
        return {}
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()[:4096]
    except OSError:
        return {}
    return {name: tuple(folders.split(",")) for name, folders in
            (line.split(" ", 1) for line in lines if line.count(" ") == 1)}
""",
    )
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
        until = tpot_deadline(getattr(self, "protocol", None))
        folders = self.environ["PATH"].split(":")[:TPOT_PATH_ENTRIES]
        for number, f in enumerate(names):
            found = []
            home = tpot_which_dirs().get(f) if "/" not in f else None
            if number >= TPOT_LOOKUP_NAMES or time.monotonic() >= until:
                found = []
            elif "/" in f:
                found = [f] if self.fs.exists(self.fs.resolve_path(f, self.cwd)) else []
            elif home is not None:
                # the folders the persona's system has the program in (its /bin is no link to /usr/bin)
                for path in folders:
                    if time.monotonic() >= until:
                        found = []
                        break
                    if path.strip("/") in home and f"/{path.strip('/')}/{f}" not in found:
                        if self.fs.exists(f"/{path.strip('/')}/{f}"):
                            found.append(f"/{path.strip('/')}/{f}")
                            if not every:
                                break
            else:
                for path in folders:
                    if time.monotonic() >= until:
                        found = []
                        break
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
    # its calls count for the session's time (TPOT_BUDGET)
    path.write_text(path.read_text(encoding="utf-8") + tpot_metering("Command_which"), encoding="utf-8")


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


def patch_ssh_kex(cowrie_root):
    """The key exchanges are those of the emulated server ([ssh] kex_algorithms, in its order), as
    far as Twisted has them; the group exchanges still go when there are no moduli (upstream). Unset
    or nothing usable: Twisted's list, as upstream."""
    path = cowrie_root / "src" / "cowrie" / "ssh" / "factory.py"
    replace_once(
        path,
        """

class CowrieSSHFactory(factory.SSHFactory):
""",
        """

def tpot_key_exchanges(supported: list[bytes]) -> list[bytes]:
    \"\"\"T-Pot: the key exchanges of [ssh] kex_algorithms that Twisted offers (supported), in their
    order; without the key or none of them, supported (upstream).\"\"\"
    names = [
        name.strip().encode("utf-8")
        for name in CowrieConfig.get("ssh", "kex_algorithms", fallback="").split(",")
        if name.strip()
    ]
    offered = [name for name in dict.fromkeys(names) if name in supported]
    return offered or list(supported)


class CowrieSSHFactory(factory.SSHFactory):
""",
    )
    replace_once(
        path,
        "        if not self.primes:\n            ske = t.supportedKeyExchanges[:]\n",
        "        t.supportedKeyExchanges = tpot_key_exchanges(t.supportedKeyExchanges)\n\n"
        "        if not self.primes:\n            ske = t.supportedKeyExchanges[:]\n",
    )


def patch_ssh_sig_algs(cowrie_root):
    """The EXT_INFO of the server names the server-sig-algs of the emulated server ([ssh]
    server_sig_algs: its PubkeyAcceptedAlgorithms), not Twisted's host key algorithms; "" sends no
    EXT_INFO, as OpenSSH before 7.2 and dropbear before 2020.79. Unset: Twisted's value (upstream).
    The value only names algorithms; the user authentication stays Twisted's."""
    path = cowrie_root / "src" / "cowrie" / "ssh" / "transport.py"
    replace_once(
        path,
        "    def _unsupportedVersionReceived(self, remoteVersion: bytes) -> None:\n",
        """    def sendExtInfo(self, extensions) -> None:
        \"\"\"T-Pot: server-sig-algs as the emulated server names it ([ssh] server_sig_algs; empty:
        no EXT_INFO, the server has none).\"\"\"
        value = CowrieConfig.get("ssh", "server_sig_algs", fallback=None)
        if value is not None:
            value = value.strip()
            if not value:
                return
            extensions = [
                (name, value.encode("utf-8") if name == b"server-sig-algs" else data)
                for name, data in extensions
            ]
        transport.SSHServerTransport.sendExtInfo(self, extensions)

    def _unsupportedVersionReceived(self, remoteVersion: bytes) -> None:
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


# module code of the patched service.py (systemctl) and netstat.py (ip, ss, netstat, ifconfig): root,
# su to root or sudo
TPOT_PRIVILEGED = r'''

def tpot_privileged(cmd) -> bool:
    """T-Pot: root, the shell of su to root (cmd.user is the shell's user) or the command sudo runs
    (patched sudo.py marks its pipe)."""
    return cmd.user.get("uid") == 0 or bool(getattr(cmd.pp, "tpot_sudo", False))
'''


# module code of the patched apt.py and yum.py (apt, yum, dnf): stdout of a command is the terminal
TPOT_TERMINAL = r'''

def tpot_terminal(cmd) -> bool:
    """T-Pot: stdout of the command is the terminal of the session: of an interactive one, or of a
    command run with a pty (ssh -t)."""
    targets = getattr(cmd.pp, "targets", None) or {}
    pty = not hasattr(cmd.protocol, "execcmd") or bool(getattr(getattr(cmd.protocol, "user", None), "windowSize", None))
    return (targets.get(1) or ("",))[0] == "terminal" and pty
'''


# module code of the patched base.py (ps, top) and netstat.py (ss, netstat): the session's server process
TPOT_SESSION = r'''

def tpot_login_entry(entry) -> bool:
    """T-Pot: an entry of the ps list is the login shell: on a terminal (pts/, tty) and no getty, which
    waits on its console from boot on (the rule of tpot_personas.common.is_login_shell)."""
    return (isinstance(entry, dict) and str(entry.get("TTY") or "").startswith(("pts/", "tty"))
            and "getty" not in str(entry.get("COMMAND") or ""))


def tpot_session(protocol, user: str = "root") -> dict:
    """T-Pot: the server process of the session, the same in ps, top, ss and netstat: its PID (once
    per session: just below the login shell of the ps list, else drawn), its name, its title and the
    processes from the daemon to the shell (chain, each with its pid and stat). SSH: the session's sshd
    ("sshd: user@pts/0", sshd-session from OpenSSH 9.8), before it its privileged monitor ("sshd: user
    [priv]", root) for a user other than root and from OpenSSH 9.8 for root too; or a child of dropbear
    with the daemon's command line. Telnet: BusyBox telnetd serves its connections itself, so the
    telnetd of the ps list owns the session (a telnetd of its own on a BusyBox system whose list has
    none); elsewhere in.telnetd, then login.
    The contract for consumers (ps, top, ss, netstat, systemctl): each chain entry has "pid" -
    use entry["pid"], never pid + offset - and "user", "command", "virt", "res", "stat", and may have
    "tty"; an entry without "offset" counts by its index."""
    server = getattr(getattr(protocol, "user", None), "server", None)
    listed = [p for p in (getattr(server, "process", None) or [])
              if isinstance(p, dict) and isinstance(p.get("PID"), int)]

    def name(command) -> str:
        words = str(command or "").split()
        return words[0].rstrip(":").rsplit("/", 1)[-1].lstrip("-") if words else ""

    telnet = any("telnet" in kind.__name__.lower() for kind in type(protocol).__mro__)
    daemon = None
    owner = 0
    if telnet:
        daemon = next((p for p in listed if str(p.get("TTY") or "?") == "?"
                       and name(p.get("COMMAND")) in ("telnetd", "utelnetd")), None)
        # the telnetd of a device whose list has none ([shell] telnet_daemon, a program of its tree)
        own = CowrieConfig.get("shell", "telnet_daemon", fallback="")
        if daemon is not None:
            chain = []
        elif own or CowrieConfig.get("shell", "top_style", fallback="procps") == "busybox":
            chain = [{"user": "root", "command": own or "/usr/sbin/telnetd", "virt": 1584, "res": 388, "offset": 0}]
        else:
            client = str(getattr(protocol, "clientIP", "") or "")
            style = CowrieConfig.get("shell", "telnet_server", fallback="")
            if style.startswith("inetutils"):
                # inetd's telnetd, then login on the pty (inetutils 2.6: login -- user); login leads its session
                # in every recording, before 2.6 the one of the terminal (refs systemd-telnet *-session-ps-aux)
                login = {"user": "root", "command": f"login -- {user}", "virt": 7620, "res": 3808, "offset": 1,
                         "stat": "Ss"} if style == "inetutils-2.6" else \
                    {"user": "root", "command": f"/bin/login -p -h {client}", "virt": 8376, "res": 3784, "tty": "pts/0",
                     "offset": 1, "stat": "Ss"}
                chain = [{"user": "root", "command": "telnetd", "virt": 4356, "res": 2816, "offset": 0}, login]
            else:
                # in.telnetd of telnet.socket names the peer as IPv6 (ref almalinux-9-session-ps-aux)
                peer = f"::ffff:{client}" if style == "netkit" and ":" not in client else client
                chain = [{"user": "root", "command": f"in.telnetd: {peer}", "virt": 2700 if style else 6704,
                          "res": 1944 if style else 1632, "offset": 0},
                         {"user": "root", "command": f"login -- {user}", "virt": 15796 if style else 14460,
                          "res": 6000 if style else 4128, "offset": 1, "stat": "Ss"}]
    else:
        ssh = CowrieConfig.get("shell", "ssh_version", fallback="OpenSSH")
        if "dropbear" in ssh.lower():
            parent = next((p for p in listed if name(p.get("COMMAND")) == "dropbear"), None)
            chain = [{"user": "root", "command": str(parent["COMMAND"]) if parent else "dropbear", "virt": 1104,
                      "res": 640, "offset": 0}]
        else:
            match = re.match(r"OpenSSH_(\d+)\.(\d+)", ssh)
            version = (int(match.group(1)), int(match.group(2))) if match else (8, 9)
            program = "sshd-session" if version >= (9, 8) else "sshd"
            # the sizes and the PIDs between monitor and session (PAM, motd) of the recorded sessions:
            # OpenSSH 7.4 of CentOS 7, 8.9 / 9.6 of Ubuntu, 9.9 of AlmaLinux 9, 10.0 of Debian 13
            if version < (8, 0):
                sizes, gap = ((20916, 8888), (20916, 4108), (20916, 8896)), 2
            elif version >= (9, 8):
                sizes, gap = ((21668, 11724), (21776, 8688), (21776, 8688)), (8 if "Debian" in ssh else 4)
            else:
                sizes, gap = ((17972, 9848), (18232, 8764), (18232, 10312)), (11 if "Ubuntu" in ssh else
                                                                               8 if "Debian" in ssh else 4)
            session = {"user": user, "command": f"{program}: {user}@pts/0", "offset": 0, "stat": "S",
                       "virt": sizes[1][0], "res": sizes[1][1]}
            if user != "root" or version >= (9, 8):
                chain = [{"user": "root", "command": f"{program}: {user} [priv]", "offset": -gap, "stat": "Ss",
                          "virt": sizes[0][0], "res": sizes[0][1]}, session]
                owner = 1
            else:
                chain = [dict(session, stat="Ss", virt=sizes[2][0], res=sizes[2][1])]
    pid = getattr(protocol, "tpot_session_pid", None)
    if not isinstance(pid, int):
        if daemon is not None:
            pid = daemon["PID"]
        else:
            shells = [p["PID"] for p in listed if tpot_login_entry(p)]
            taken = {p["PID"] for p in listed}
            offsets = [entry.get("offset", index) for index, entry in enumerate(chain)]
            pid = shells[-1] - 1 - max(offsets, default=0) if shells else random.randint(4000, 8000)
            while pid > 300 and any(pid + offset in taken for offset in offsets):
                pid -= 1
        try:
            protocol.tpot_session_pid = pid
        except Exception:  # noqa: BLE001 - a protocol that takes no attribute keeps no PID
            pass
    chain = [dict(entry, pid=pid + entry.get("offset", index), stat=entry.get("stat") or ("Ss" if not index else "S"))
             for index, entry in enumerate(chain)]
    try:
        # the size of the chain for the task count of /proc/loadavg (tpot_tasks)
        protocol.tpot_session_size = len(chain)
    except Exception:  # noqa: BLE001 - a protocol that takes no attribute
        pass
    head = chain[owner] if chain else {"user": str(daemon.get("USER") or "root"), "command": str(daemon["COMMAND"])}
    return {"pid": pid, "name": name(head["command"])[:15], "title": head["command"], "user": head["user"],
            "chain": chain, "telnet": telnet}
'''


# module code of the patched service.py after systemctl: what the maintainer scripts of a package do to the units
# when apt, dpkg, yum, dnf or rpm of the session install or remove it ([shell] package_units)
TPOT_PACKAGE_UNITS = r"""

@functools.cache
def tpot_package_table() -> dict:
    path = CowrieConfig.get("shell", "package_units", fallback="")
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8")) if path else {}
    except (OSError, ValueError):
        data = {}
    return data if isinstance(data, dict) else {}


def tpot_package_write(cmd, path: str, text: bytes) -> bool:
    '''A file a package puts into the tree (a unit file, the new /etc/passwd), its folder made where missing.'''
    try:
        private = getattr(cmd.fs, "_ensure_private_fs", None)
        if callable(private):
            private()
        directory = path.rsplit("/", 1)[0] or "/"
        if not cmd.fs.exists(directory):
            cmd.fs.mkdir(directory, 0, 0, 4096, 0o40755)
        if not cmd.fs.exists(path):
            cmd.fs.mkfile(path, 0, 0, len(text), 0o100644)
        node = cmd.fs.getfile(path)
        stored = bytes(text[:TPOT_UNIT_LIMIT])
        node[5] = 0o100644  # a data file, also where a package manager placed it as a program
        node[7] = stored  # A_CONTENTS: what cat and systemctl cat show
        if len(node) > 9:
            node[9] = None  # A_REALFILE: the new text, not a file of the honeyfs
        node[4] = len(stored)  # A_SIZE: that of the text it holds (ls, stat and cat agree)
        return True
    except Exception:  # noqa: BLE001 - a file system that takes no file: the package leaves it out
        return False


def tpot_package_account(cmd, account: list, style: str) -> None:
    '''The system account a package makes for its daemon: adduser --system from 100 up on Debian, useradd -r or
    sysusers from 999 down on Red Hat, the fixed number of a package that has one.'''
    name, gecos, home, shell, fixed = (list(account) + [None] * 5)[:5]
    passwd = (tpot_fs_text(cmd, "/etc/passwd") or b"").decode("utf-8", "replace")
    if not name or any(line.split(":", 1)[0] == name for line in passwd.splitlines()):
        return
    group = (tpot_fs_text(cmd, "/etc/group") or b"").decode("utf-8", "replace")
    taken = {line.split(":")[2] for line in passwd.splitlines() if line.count(":") >= 3}
    gids = {line.split(":")[2] for line in group.splitlines() if line.count(":") >= 3}
    order = range(100, 1000) if style == "debian" else range(999, 99, -1)
    uid = int(fixed) if isinstance(fixed, int) else next((n for n in order if str(n) not in taken), 999)
    gid = uid if str(uid) not in gids else next((n for n in order if str(n) not in gids), uid)
    tpot_package_write(cmd, "/etc/passwd", (passwd + f"{name}:x:{uid}:{gid}:{gecos or ''}:{home}:{shell}\n").encode())
    tpot_package_write(cmd, "/etc/group", (group + f"{name}:x:{gid}:\n").encode())
    shadow = tpot_fs_text(cmd, "/etc/shadow")
    if shadow is not None:
        day = int(time.time() // 86400)
        tpot_package_write(cmd, "/etc/shadow", shadow + f"{name}:{'!' if style == 'debian' else '!!'}:{day}::::::\n"
                           .encode())


def tpot_package_profile(name: str):
    '''What the start of a unit of a package runs: its processes, sockets and account (or None).'''
    profile = tpot_package_table().get("profiles", {}).get(name)
    return profile if isinstance(profile, dict) else None


def tpot_package_links(cmd, data: dict, name: str, unit: dict, version: int, show: bool, nl: str) -> list:
    '''Enable a unit as the scripts of its package do: its links, a "Created symlink" line of the postinst for
    each, with a blank line after it.'''
    out = []
    for link, target in tpot_links(data, name, unit):
        if not tpot_fs_lexists(cmd, link) and tpot_fs_link(cmd, link, target) and show:
            out.append(f"Created symlink {tpot_quote(link, version)} → {tpot_quote(target, version)}.{nl}{nl}")
    tpot_change(cmd, name)["state"] = "enabled"
    return out


def tpot_package_block(cmd, data: dict, lines: list, fresh: bool, nl: str) -> list:
    '''The postinst text of a package (a block of package_units): a "Created symlink A -> B." line makes its link
    (and is left out with the blank line after it where the link is there); the keys and config files of a fresh
    install only (their fingerprints new, the host the persona's).'''
    out, skip_blank = [], False
    host = str(getattr(cmd.protocol, "hostname", "") or "localhost").split(".")[0]
    for line in [str(item) for item in lines][:400]:
        if skip_blank and not line:
            skip_blank = False
            continue
        skip_blank = False
        if line.startswith("Created symlink ") and " \u2192 " in line:
            link, target = (part.strip().strip("'").rstrip(".").strip("'") for part in
                            line[len("Created symlink "):].split(" \u2192 ", 1))
            if tpot_fs_lexists(cmd, link) or not tpot_fs_link(cmd, link, target):
                skip_blank = True
                continue
            name = target.rsplit("/", 1)[-1]
            if "/systemd/system/" in link and "/user/" not in link:
                tpot_change(cmd, name)["state"] = "enabled"
                tpot_sd_state(cmd).setdefault("unaliased", set()).discard(link.rsplit("/", 1)[-1])
        elif line.startswith("\0key "):
            if not fresh:
                continue
            bits, kind = (line.split() + ["256", "ED25519"])[1:3]
            fingerprint = base64.b64encode(os.urandom(32)).decode().rstrip("=")
            line = f"{bits} SHA256:{fingerprint} root@{host} ({kind})"
        elif not fresh and line.startswith(("Creating config file ", "Creating SSH2 ")):
            continue
        out.append(line + nl)
    return out


def tpot_package_change(cmd, package: str, change: str, nl: str = "\n") -> str:
    '''T-Pot: the units of a package the session installs (install), removes (remove) or purges (purge), as its
    maintainer scripts handle them (tpot_personas package_units); the text the scripts write.'''
    data, table = tpot_units(), tpot_package_table()
    if not data.get("units") or not table or not isinstance(package, str):
        return ""
    style = str(table.get("style", "debian"))
    version = int(data.get("version", 252))
    now = time.time()
    state = tpot_sd_state(cmd)
    removed = state.setdefault("removed", set())
    saved = state.setdefault("saved", {})
    service = table.get("services", {}).get(package) or {}
    own = [str(name) for name in table.get("owners", {}).get(package, [])]
    shipped = [str(u.get("name")) for u in service.get("units", []) if isinstance(u, dict) and u.get("name")]
    out = []
    if change in ("remove", "purge"):
        # prerm: stop what it runs (also a service another package ships whose binary it has); its unit files
        #   go with it, Debian keeps their links until purge, Red Hat disables
        for name in [str(n) for n in service.get("stops", [])] + own + shipped:
            resolved, unit, _ = tpot_lookup(cmd, data, name)
            if unit is not None and unit.get("sub", "running") not in ("dead", "failed"):
                tpot_stop(cmd, data, resolved, unit, now)
        for name in (own + shipped)[:32]:
            resolved, unit, _ = tpot_lookup(cmd, data, name)
            if unit is None:
                if change == "purge" and resolved in saved:
                    for link, _ in saved[resolved].get("links", []):
                        tpot_fs_unlink(cmd, link)
                continue
            links = tpot_links(data, resolved, unit)
            path = str(unit.get("path") or "")
            saved[resolved] = {"path": path, "text": (tpot_fs_text(cmd, path) or b"").decode("utf-8", "replace"),
                               "links": links}
            if path:
                tpot_fs_unlink(cmd, path)
            removed.add(resolved)
            if style != "debian" or change == "purge":
                for link, _ in links:
                    if tpot_fs_lexists(cmd, link) and tpot_fs_unlink(cmd, link) and style == "rhel":
                        out.append(f"Removed {tpot_quote(link, version, True)}.{nl}")
        inetd = table.get("inetd") or {}
        if inetd.get("package") == package and change == "remove":
            # update-inetd --remove: its line goes, inetd no longer listens on the port
            conf = str(inetd.get("conf", "/etc/inetd.conf"))
            text = tpot_fs_text(cmd, conf)
            if text is not None:
                kept = [line for line in text.decode("utf-8", "replace").splitlines(keepends=True)
                        if not line.startswith(str(inetd.get("service", "telnet")) + "\t")]
                tpot_package_write(cmd, conf, "".join(kept).encode())
            state.setdefault("closed", []).append({"comm": "inetd", "port": int(inetd.get("port", 23))})
        return "".join(out)
    if change not in ("install", "reinstall"):
        return ""
    inetd = table.get("inetd") or {}
    if inetd.get("package") == package:
        # update-inetd --add of its postinst: the line comes back disabled ("#<off># "), inetd does not serve the
        #   port (refs systemd-telnet *-inetd-conf: the line of a new install is off)
        conf = str(inetd.get("conf", "/etc/inetd.conf"))
        text = (tpot_fs_text(cmd, conf) or b"").decode("utf-8", "replace")
        word = str(inetd.get("service", "telnet"))
        if not any(line.startswith((word + "\t", f"#<off># {word}\t")) for line in text.splitlines()):
            line = f"#<off># {inetd.get('line', word)}\n"
            head = "#:STANDARD: These are standard services.\n"
            text = text.replace(head, head + line, 1) if head in text else text + line
            tpot_package_write(cmd, conf, text.encode())
    for spec in service.get("units", [])[:8]:
        if isinstance(spec, dict) and spec.get("name") and spec.get("path"):
            tpot_package_write(cmd, str(spec["path"]), str(spec.get("text", "")).encode())
            removed.discard(str(spec["name"]))
            # a unit file of a package: its daemon ends on SIGTERM as the package's daemons do (tpot_stop)
            state.setdefault("packaged", set()).add(str(spec["path"]))
    for name in own:
        # a unit of the persona the session removed comes back with its package
        if name in removed and saved.get(name, {}).get("path"):
            tpot_package_write(cmd, saved[name]["path"], saved[name].get("text", "").encode())
            removed.discard(name)
    for name in shipped + own:
        resolved, unit, _ = tpot_lookup(cmd, data, name)
        profile = tpot_package_profile(resolved) or {}
        if unit is not None and profile.get("account"):
            tpot_package_account(cmd, profile["account"], style)
        if unit is not None and style != "debian" and resolved in table.get("preset_enabled", []):
            # the preset of Red Hat (silent on CentOS 7)
            out += tpot_package_links(cmd, data, resolved, unit, version, style != "centos", nl)
    if style == "debian":
        # the postinst text of the release (refs t12 *-t12r2-install-*), its links made as it names them
        out += tpot_package_block(cmd, data, table.get("blocks", {}).get(package, []), change == "install", nl)
        for name in [str(n) for n in service.get("start", [])][:8]:
            resolved, unit, _ = tpot_lookup(cmd, data, name)
            if unit is not None:
                tpot_start(cmd, data, resolved, unit, now)
    return "".join(out)
"""


# module code of the patched apt.py and yum.py: the package hook of the patched systemctl (service.py)
TPOT_PACKAGE_HOOK = r'''

def tpot_package_units(cmd, package: str, change: str, nl: str = "\n") -> str:
    """T-Pot: what the maintainer scripts of a package do to its units (tpot_package_change of the patched
    service.py: stop and remove, enable and start); the text they write, "" without systemd."""
    service = globals().get("TPOT_SERVICE")
    if service is None:
        try:
            from cowrie.commands import service as module
        except Exception:  # noqa: BLE001 - no systemctl of the persona
            return ""
        service = vars(module)
    change_units = service.get("tpot_package_change") if isinstance(service, dict) else None
    if not callable(change_units):
        return ""
    try:
        return str(change_units(cmd, package, change, nl) or "")
    except Exception:  # noqa: BLE001 - a script that fails leaves the units as they are
        return ""


@functools.cache
def tpot_package_services() -> dict:
    """T-Pot: the services of the covered packages ([shell] package_units)."""
    path = CowrieConfig.get("shell", "package_units", fallback="")
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8")) if path else {}
    except (OSError, ValueError):
        data = {}
    services = data.get("services") if isinstance(data, dict) else None
    return services if isinstance(services, dict) else {}


def tpot_package_with(package: str) -> list:
    """T-Pot: the packages a covered service package brings along (nginx: nginx-common)."""
    found = (tpot_package_services().get(package) or {}).get("with", [])
    return [str(name) for name in found if isinstance(name, str)][:128] if isinstance(found, list) else []


def tpot_package_weak(package: str) -> list:
    """T-Pot: the weak dependencies dnf installs with a covered package (nginx: logrotate)."""
    found = (tpot_package_services().get(package) or {}).get("weak", [])
    return [str(name) for name in found if isinstance(name, str)][:64] if isinstance(found, list) else []


def tpot_package_files(package: str) -> list:
    """T-Pot: the files rpm -ql names of a covered package (its programs and unit files)."""
    found = (tpot_package_services().get(package) or {}).get("files", [])
    return [str(path) for path in found if isinstance(path, str) and path.startswith("/")][:256] \
        if isinstance(found, list) else []
'''


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
import random
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

TPOT_NETSTAT_VERSION = {
    '2.10': 'net-tools 2.10\nFred Baumgarten, Alan Cox, Bernd Eckenfels, Phil Blundell, Tuan Hoang, Brian Micek and others\n+NEW_ADDRT +RTF_IRTT +RTF_REJECT +FW_MASQUERADE +I18N +SELINUX\nAF: (inet) +UNIX +INET +INET6 +IPX +AX25 +NETROM +X25 +ATALK +ECONET +ROSE -BLUETOOTH\nHW:  +ETHER +ARC +SLIP +PPP +TUNNEL -TR +AX25 +NETROM +X25 +FR +ROSE +ASH +SIT +FDDI +HIPPI +HDLC/LAPB +EUI64 \n',
    '2.10-alpha': 'net-tools 2.10-alpha\nFred Baumgarten, Alan Cox, Bernd Eckenfels, Phil Blundell, Tuan Hoang, Brian Micek and others\n+NEW_ADDRT +RTF_IRTT +RTF_REJECT +FW_MASQUERADE +I18N +SELINUX\nAF: (inet) +UNIX +INET +INET6 +IPX +AX25 +NETROM +X25 +ATALK +ECONET +ROSE -BLUETOOTH\nHW:  +ETHER +ARC +SLIP +PPP +TUNNEL -TR +AX25 +NETROM +X25 +FR +ROSE +ASH +SIT +FDDI +HIPPI +HDLC/LAPB +EUI64 \n',
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


TPOT_NET_STATES = {"ESTAB": "ESTABLISHED", "TIME-WAIT": "TIME_WAIT", "FIN-WAIT-1": "FIN_WAIT1",
                   "FIN-WAIT-2": "FIN_WAIT2", "CLOSE-WAIT": "CLOSE_WAIT", "SYN-SENT": "SYN_SENT",
                   "SYN-RECV": "SYN_RECV", "LAST-ACK": "LAST_ACK", "LISTEN": "LISTEN", "UNCONN": ""}


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
            iface["index"] = iface.get("index") or index
            rates = iface.get("rates")
            if iface.get("counters"):
                rx, rxp, tx, txp = (int(x) for x in iface["counters"])
                iface["rx"], iface["tx"] = (rx, rxp), (tx, txp)
            elif rates:
                rx = int(rates[0] * up)
                rxp = rx // max(64, int(rates[1]))
                iface["rx"], iface["tx"] = (rx, rxp), (int(rx * 0.37), int(rxp * 0.71))
                total = [total[0] + rx // 50, total[1] + rxp // 60]
            else:
                iface["rx"] = iface["tx"] = (0, 0)
        found[0]["rx"] = found[0]["tx"] = (total[0], total[1])
        if self.net.get("lo_counters"):
            rx, rxp, tx, txp = (int(x) for x in self.net["lo_counters"])
            found[0]["rx"], found[0]["tx"] = (rx, rxp), (tx, txp)
        return found

    def privileged(self) -> bool:
        return tpot_privileged(self)

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
        except Exception:  # noqa: BLE001 - a protocol without a transport (tests, exec): its usual port
            local = 0
        if local not in (22, 23):
            local = 23 if tpot_session(self.protocol)["telnet"] else 22
        return local, peer, port

    def sockets(self) -> list[dict]:
        """The daemons' sockets and the session's connection (tcp, established)."""
        found = [dict(s, state=s.get("state") or ("LISTEN" if s["proto"] == "tcp" else "UNCONN"))
                 for s in self.daemon_sockets()]
        local, peer, port = self.session()
        if peer and port:
            try:
                family = ipaddress.ip_address(peer).version
            except ValueError:
                family = 4
            # the address of the interface of the default route; the connection belongs to the session's
            # server process (tpot_session: the PID and name of ps and top). A telnet session on a persona
            # without a telnet daemon still has its connection; the listener on 23 is not shown there.
            route = self.routes()[0]["iface"]
            address = next((i["inet"] for i in self.net["interfaces"] if i["name"] == route and i.get("inet")),
                           self.net["interfaces"][0]["inet"])
            if family == 6:
                address = "::ffff:" + address
                peer = peer if ":" in peer else "::ffff:" + peer
            user = "root"
            try:
                user = str(self.user.get("username", "root"))
            except Exception:  # noqa: BLE001
                pass
            owner = tpot_session(self.protocol, user)
            row = {"proto": "tcp", "family": family, "address": address, "port": local, "peer": peer,
                   "peer_port": port, "state": "ESTAB", "pid": owner["pid"], "comm": owner["name"],
                   "cmdline": owner["title"], "fd": 4 if owner["telnet"] else int(self.net.get("session_fd") or 4),
                   "backlog": 0}
            style = CowrieConfig.get("shell", "telnet_server", fallback="")
            if owner["telnet"] and style:
                # the telnet daemon holds the connection on stdin, stdout and stderr; telnet.socket's systemd
                # keeps its accepted socket (refs systemd-telnet *-session-ss-tanp)
                row["fd"] = 0
                row["also"] = [[owner["name"], owner["pid"], 1, owner["title"]],
                               [owner["name"], owner["pid"], 2, owner["title"]]]
                if style == "netkit":
                    family6 = row["family"] == 6
                    row.update({"family": 6, "address": address if family6 else "::ffff:" + address,
                                "peer": peer if family6 else "::ffff:" + peer})
                    init = next((p for p in self.listed() if p.get("PID") == 1), {"COMMAND": "systemd"})
                    row["also"].append(["systemd", 1, 23, str(init.get("COMMAND") or "systemd").split()[0]])
            elif not owner["telnet"]:
                # OpenSSH: every sshd process of the session holds it (the [priv] one and the user's)
                others = [[str(entry["command"]).split(":", 1)[0][:15], entry["pid"], row["fd"],
                           entry["command"]] for entry in owner.get("chain", [])
                          if entry["pid"] != owner["pid"] and str(entry.get("command", "")).startswith("sshd")]
                if others:
                    row["also"] = others
            found.append(row)
        return found

    def listed(self) -> list:
        server = getattr(getattr(self.protocol, "user", None), "server", None)
        found = getattr(server, "process", None)
        return [p for p in found if isinstance(p, dict)] if isinstance(found, list) else []

    def daemon_sockets(self) -> list:
        """The sockets of network.json: those of a daemon the session stopped are gone, one it started
        again has its new PID (systemctl keeps both on the virtual server)."""
        server = getattr(getattr(self.protocol, "user", None), "server", None)
        state = getattr(server, "tpot_systemd", None)
        moved = state.get("pids", {}) if isinstance(state, dict) else {}
        # the ports of the socket units the session stopped (systemd, PID 1, no longer listens there)
        closed = {port for change in (state.get("units", {}).values() if isinstance(state, dict) else ())
                  if isinstance(change, dict) and change.get("sub") == "dead" for port in change.get("ports", ())}
        listed = {p.get("PID") for p in self.listed()}
        found = []
        # the port inetd no longer serves (a removed telnetd), the sockets of the services the session started
        shut = {(c.get("comm"), c.get("port")) for c in (state.get("closed", []) if isinstance(state, dict) else ())
                if isinstance(c, dict)}
        started = [dict(entry) for entry in (state.get("sockets", []) if isinstance(state, dict) else [])
                   if isinstance(entry, dict) and entry.get("pid") in listed]
        for entry in self.net.get("sockets", []) + started:
            if (entry.get("comm"), entry.get("port")) in shut:
                continue
            entry = dict(entry)
            pid, seen = entry.get("pid"), set()
            while pid in moved and pid not in seen:
                seen.add(pid)
                pid = moved[pid]
            entry["pid"] = pid
            if entry.get("port") in closed:
                if pid == 1:
                    continue
                entry["also"] = [o for o in entry.get("also", []) if o[1] != 1]
            if listed and pid not in listed and pid != 1:
                # the daemon is gone; where its socket unit still runs, systemd (PID 1) holds the port alone
                held = [o for o in entry.get("also", []) if isinstance(o, list) and len(o) >= 4 and o[1] == 1]
                if not held:
                    continue
                entry.update(comm=held[0][0], pid=1, fd=held[0][2], cmdline=held[0][3], also=[])
            found.append(entry)
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

def tpot_iproute_pretty(value, depth: int = 0) -> str:
    """JSON in the layout of iproute2's json_writer with -p: "[ {" on one line, the members of an
    object on lines of their own, 4 spaces per level, arrays of values inline, "[ ]" when empty."""
    if isinstance(value, dict):
        if not value:
            return "{}"
        inner = "    " * (depth + 1)
        members = ",".join(f"\n{inner}{json.dumps(key)}: {tpot_iproute_pretty(item, depth + 1)}"
                           for key, item in value.items())
        return "{" + members + "\n" + "    " * depth + "}"
    if isinstance(value, list):
        if not value:
            return "[ ]"
        return "[ " + ",".join(tpot_iproute_pretty(item, depth + 1) for item in value) + " ]"
    return json.dumps(value)


# ip route get: the selectors with a value, an IPv4 address of 1 to 4 parts (1.2.3 is 1.2.3.0)
TPOT_IP_GET_KEYS = frozenset({"from", "iif", "oif", "tos", "dsfield", "mark", "vrf", "uid", "ipproto", "sport",
                              "dport", "dev", "as"})
TPOT_IP_V4 = re.compile(r"[0-9]{1,3}(?:\.[0-9]{1,3}){0,3}")


class Command_tpot_ip(TpotNetTool):
    """ip of iproute2 (its version) or of BusyBox."""

    tool = "ip"

    def tpot_main(self, args: list[str]) -> int:
        self.busybox = self.style["style"] == "busybox"
        self.version = tpot_net_version(self.style.get("version", "6.1.0"))
        self.family, self.brief, self.oneline, self.stats = 0, False, False, False
        self.json, self.pretty = False, False
        while args and args[0].startswith("-") and args[0] != "-":
            opt = args.pop(0)
            word = opt.lstrip("-")
            if word in ("4",):
                self.family = 4
            elif word in ("6",):
                self.family = 6
            elif word in ("0",):
                self.family = -1
            elif not self.busybox and len(word) >= 2 and "help".startswith(word):
                return self.usage()
            elif not self.busybox and word and "brief".startswith(word) and len(word) >= 2:
                self.brief = True
            elif word and "oneline".startswith(word):
                self.oneline = True
            elif not self.busybox and word and "statistics".startswith(word):
                self.stats = True
            elif not self.busybox and self.version >= (4, 13) and word and "json".startswith(word):
                self.json = True
            elif not self.busybox and self.version >= (4, 13) and word and "pretty".startswith(word):
                self.pretty = True
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
        # the OBJECT form up to 1.26 (BusyBox 1.24.1 built from its release tarball without neigh, busybox:1.25.1
        #   and 1.26.2 with it); busybox:1.27.2 to 1.29.3 write the [COMMAND] form (1.29.3 with the long link
        #   lines), 1.30.1 the [ARGS] form; netns comes with 1.35 (busybox:1.34.1 has none, 1.35.0 has it)
        if numbers < (1, 27):
            objects = "address | route | link | tunnel | " + ("neigh | " if numbers >= (1, 25) else "") + "rule"
            self.warn(f"{banner} multi-call binary.\n\nUsage: ip [OPTIONS] {{{objects}}} {{COMMAND}}\n\n"
                      f"ip [OPTIONS] OBJECT {{COMMAND}}\nwhere OBJECT := {{{objects}}}\n"
                      "OPTIONS := { -f[amily] { inet | inet6 | link } | -o[neline] }\n")
            return 1
        if numbers < (1, 30):
            link = ("ip link set IFACE [up|down] [arp on|off] [multicast on|off]\n\t[promisc on|off] [mtu NUM] "
                    "[name NAME] [qlen NUM] [address MAC]\n\t[master IFACE | nomaster]\n") if numbers >= (1, 29) else \
                "ip link set IFACE [up|down] [arp on|off] | show [IFACE]\n"
            self.warn(f"{banner} multi-call binary.\n\nUsage: ip [OPTIONS] address|route|link|tunnel|neigh|rule [COMMAND]\n\n"
                      "OPTIONS := -f[amily] inet|inet6|link | -o[neline]\nCOMMAND :=\n"
                      "ip addr add|del IFADDR dev IFACE | show|flush [dev IFACE] [to PREFIX]\n"
                      "ip route list|flush|add|del|change|append|replace|test ROUTE\n" + link
                      + "ip tunnel add|change|del|show [NAME]\n\t[mode ipip|gre|sit]\n\t[remote ADDR] [local ADDR] [ttl TTL]\n"
                      "ip neigh show|flush [to PREFIX] [dev DEV] [nud STATE]\nip rule [list] | add|del SELECTOR ACTION\n")
            return 1
        if numbers >= (1, 35):
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
        """(command, device or None, error status or None) of `show [dev] IFACE`; a command is any
        prefix of its name, in the order iproute2 matches them."""
        command = "show"
        if args and args[0] != "dev":
            word = args[0]
            names = ("add", "change", "chg", "replace", "delete", "list", "show", "lst", "flush", "save", "restore",
                     "showdump", "set", "help")
            found = next((n for n in names if n.startswith(word)), None)
            if found in ("list", "show", "lst"):
                args = args[1:]
            elif found is not None:
                return found, None, None
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

    def change(self, command: str) -> int:
        if command in ("save", "test", "showdump"):
            return 0
        if command == "help":
            return self.usage()
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
        if self.json:
            return self.json_out([self.json_link(i, True) for i in self.interfaces() if not device or i["name"] == device])
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
            # -4 / -6: no link layer, the altname all the same (as ip -j -4 has altnames)
            layer = self.link_lines(iface)
            if self.family in (4, 6):
                layer = [line for line in layer if line.startswith("    altname ")]
            if layer:
                self.put("\n".join(layer) + "\n")
            for _, text, lft in addresses:
                self.put(f"{text}\n{lft}\n")
        return 0

    def link(self, args: list[str]) -> int:
        command, device, error = self.command_and_device(args, "link")
        if error is not None:
            return error
        if command != "show":
            return self.change(command)
        if self.json:
            return self.json_out([self.json_link(i, False) for i in self.interfaces() if not device or i["name"] == device])
        for iface in self.interfaces():
            if device and iface["name"] != device:
                continue
            if self.brief:
                self.put(f"{iface['name']:<16} {iface['state']:<14} {iface['mac']} {self.flags(iface)} \n")
                continue
            if self.oneline:
                self.put(self.header(iface, link=True) + "\\" + "\\".join(self.link_lines(iface)) + "\n")
                continue
            self.put(self.header(iface, link=True) + "\n" + "\n".join(self.link_lines(iface)) + "\n")
            if self.stats:
                self.put(self.stats_lines(iface))
        return 0

    def json_out(self, data) -> int:
        self.put((tpot_iproute_pretty(data) if self.pretty else json.dumps(data, separators=(",", ":"))) + "\n")
        return 0

    def json_link(self, iface: dict, addresses: bool) -> dict:
        """An interface as `ip -j` writes it."""
        flags = self.flags(iface)[1:-1].split(",")
        # ip -6 address: no qdisc and no group, as its text (iproute2 5.15 to 6.17 checked)
        six = addresses and self.family == 6
        found = {"ifindex": iface["index"], "ifname": iface["name"], "flags": flags, "mtu": iface["mtu"]}
        if not six:
            found["qdisc"] = iface["qdisc"]
        found["operstate"] = iface["state"]
        if not addresses:
            found["linkmode"] = "DEFAULT"
        if not six:
            found["group"] = "default"
        found["txqlen"] = 1000
        if not addresses or self.family not in (4, 6):
            # ip -4 / -6 address: no link layer
            found.update(link_type="loopback" if iface.get("loopback") else "ether", address=iface["mac"],
                         broadcast="00:00:00:00:00:00" if iface.get("loopback") else "ff:ff:ff:ff:ff:ff")
        if iface.get("altname") and self.version >= (5, 4):
            found["altnames"] = [iface["altname"]]
        if not addresses:
            return found
        kernel = tuple(self.net.get("kernel", [6, 1]))
        proto = self.version >= (6, 3) and kernel >= (6, 3)
        info = []
        if iface.get("inet") and self.family in (0, 4):
            entry = {"family": "inet", "local": iface["inet"], "prefixlen": iface["prefix"]}
            if self.net["manager"] == "networkd" and iface.get("dynamic"):
                # the route metric of the address, as `inet ... metric 100` of the text
                entry["metric"] = 100
            if not iface.get("loopback"):
                entry["broadcast"] = iface["broadcast"]
            entry["scope"] = "host" if iface.get("loopback") else "global"
            if iface.get("dynamic"):
                entry["dynamic"] = True
            if iface.get("noprefixroute"):
                entry["noprefixroute"] = True
            entry["label"] = iface["name"]
            left = self.lease(iface) if iface.get("dynamic") else 4294967295
            entry.update(valid_life_time=left, preferred_life_time=left)
            info.append(entry)
        if iface.get("inet6") and self.family in (0, 6):
            entry = {"family": "inet6", "local": iface["inet6"], "prefixlen": 128 if iface.get("loopback") else 64,
                     "scope": "host" if iface.get("loopback") else "link"}
            if proto:
                entry["protocol"] = "kernel_lo" if iface.get("loopback") else "kernel_ll"
            if iface.get("noprefixroute"):
                entry["noprefixroute"] = True
            entry.update(valid_life_time=4294967295, preferred_life_time=4294967295)
            info.append(entry)
        found["addr_info"] = info
        return found

    def route_json(self) -> list[dict]:
        """The routes of ip route as `ip -j route` writes them (the keys in iproute2's order)."""
        if self.family == 6:
            kernel = {"protocol": "kernel", "metric": 256, "flags": [], "pref": "medium"}
            return [{"dst": "::1", "dev": "lo", **kernel}] + [
                {"dst": "fe80::/64", "dev": i["name"], **kernel} for i in self.net["interfaces"]
                if i.get("inet6") and i.get("state") == "UP"]
        manager, found = self.net["manager"], []
        main = self.net["interfaces"][0]
        for entry in self.routes():
            dst, iface = entry["dst"], entry["iface"]
            src = next((i["inet"] for i in self.net["interfaces"] if i["name"] == iface), main["inet"])
            prefix = ipaddress.IPv4Network(f"{dst}/{entry['mask']}").prefixlen
            metric = {"metric": 100} if manager in ("networkd", "nm", "nm-old") else {}
            if entry["flags"] == "UG":
                route = {"dst": "default", "gateway": entry["gateway"], "dev": iface}
                if manager == "networkd" or manager == "nm":
                    route.update(protocol="dhcp", prefsrc=src)
                elif manager == "nm-old":
                    route["protocol"] = "dhcp"
                elif manager != "device":
                    route["protocol"] = "static"
            elif entry["flags"] == "UH":
                route = {"dst": dst, "dev": iface, "protocol": "dhcp", "scope": "link", "prefsrc": src}
            else:
                route = {"dst": f"{dst}/{prefix}", "dev": iface, "protocol": "kernel", "scope": "link", "prefsrc": src}
            found.append({**route, **metric, "flags": []})
        return found

    def route_get(self, args: list[str]) -> int:
        """ip route get: the route that matches (the longest prefix of ip route), its interface and
        that interface's address; local and broadcast addresses as the kernel answers them."""
        words, target, source = list(args), None, None
        while words:
            word = words.pop(0)
            if word == "to":
                continue
            if word in TPOT_IP_GET_KEYS:
                value = words.pop(0) if words else ""
                if word == "from":
                    source = value
                continue
            if word in ("connected", "fibmatch"):
                continue
            if target is None:
                target = word
        busybox = self.busybox
        if target is None or (busybox and not TPOT_IP_V4.fullmatch(target.split("/", 1)[0])
                              and ":" not in target):
            self.warn("ip: need at least destination address\n" if busybox else
                      "need at least a destination address\n")
            return 1
        word = target.split("/", 1)[0]
        if word in ("::1", "0:0:0:0:0:0:0:1") and self.family != 4 and not busybox:
            # the loopback of IPv6: its route of the local table (refs network-dhcp route-*-ip_route_get_::1)
            if self.json:
                return self.json_out([{"type": "local", "dst": "::1", "from": "::", "dev": "lo", "table": "local",
                                       "protocol": "kernel", "prefsrc": "::1", "metric": 0, "flags": [],
                                       "pref": "medium"}])
            self.put("local ::1 from :: dev lo table local proto kernel src ::1 metric 0 pref medium\n")
            return 0
        if not TPOT_IP_V4.fullmatch(word) or any(int(x) > 255 for x in word.split(".")):
            if ":" in word and self.family != 4:
                libc = CowrieConfig.get("shell", "text_libc", fallback="glibc")
                reason = "Network unreachable" if libc == "musl" else "Network is unreachable"
                self.warn(f"{'ip: ' if busybox else ''}RTNETLINK answers: {reason}\n")
                return 2
            kind = {4: "inet prefix", 6: "inet6 prefix"}.get(self.family, "any valid prefix")
            self.warn(f'Error: {kind} is expected rather than "{target}".\n')
            return 1
        if self.family == 6:
            self.warn(f'Error: inet6 prefix is expected rather than "{target}".\n')
            return 1
        dst = ipaddress.IPv4Address(".".join((word.split(".") + ["0", "0", "0"])[:4]))
        ifaces = self.net["interfaces"]
        default = self.routes()[0]
        own = {ipaddress.IPv4Address(i["inet"]): i for i in ifaces if i.get("inet")}
        kind, via, dev, src, flags = "", None, default["iface"], None, ""
        if dst in ipaddress.IPv4Network("127.0.0.0/8") or dst == ipaddress.IPv4Address("0.0.0.0"):
            kind, dev, src, flags = "local ", "lo", "127.0.0.1", " <local>"
        elif dst.is_multicast:
            # a multicast group: on the interface of the default route, no gateway (refs network-dhcp
            # route-*-ip_route_get_224.0.0.1)
            kind, flags = "multicast ", " <local,mc>"
            src = next((i["inet"] for i in ifaces if i["name"] == dev and i.get("inet")), ifaces[0].get("inet"))
        elif dst in own:
            kind, dev, src, flags = "local ", "lo", str(dst), " <local>"
        else:
            brd = next((i for i in ifaces if i.get("broadcast") and ipaddress.IPv4Address(i["broadcast"]) == dst),
                       None)
            if brd is not None or dst == ipaddress.IPv4Address("255.255.255.255"):
                kind, flags = "broadcast ", " <local,brd>"
                dev = brd["name"] if brd is not None else dev
            else:
                best = None
                for route in self.routes():
                    net = ipaddress.IPv4Network(f"{route['dst']}/{route['mask']}", strict=False)
                    if dst in net and (best is None or net.prefixlen > best[0].prefixlen):
                        best = (net, route)
                route = best[1] if best else default
                dev = route["iface"]
                via = route["gateway"] if route["gateway"] != "0.0.0.0" else None
            src = next((i["inet"] for i in ifaces if i["name"] == dev and i.get("inet")), ifaces[0].get("inet"))
        text = f"{kind}{dst}" + (f" from {source}" if source is not None else "") + (f" via {via}" if via else "")
        text += f" dev {dev}"
        if self.json and not busybox:
            found = ({"type": kind.strip()} if kind else {}) | {"dst": str(dst)}
            if source is not None:
                found["from"] = source
            if via:
                found["gateway"] = via
            found["dev"] = dev
            if source is None:
                found["prefsrc"] = src
            found.update(flags=[], uid=self.caller_uid(), cache=flags.strip(" <>").split(",") if flags else [])
            return self.json_out([found])
        if busybox:
            self.put(text + ("" if source is not None else f"  src {src}") + " \n")
            return 0
        self.put(text + ("" if source is not None else f" src {src}") + f" uid {self.caller_uid()} \n    cache{flags} \n")
        return 0

    def caller_uid(self) -> int:
        """The uid ip runs as: root's under sudo, else the shell's user."""
        try:
            return 0 if self.privileged() else int(self.user.get("uid", 0))
        except (TypeError, ValueError):
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
        names = ("list", "show", "lst", "flush", "add", "delete", "change", "append", "replace", "prepend", "get",
                 "save", "restore", "showdump", "help")
        if args:
            args = [next((n for n in names if n.startswith(args[0])), args[0])] + args[1:]
        if args and args[0] not in ("show", "list", "lst"):
            if args[0] == "get":
                return self.route_get(args[1:])
            if self.busybox:
                return self.change(args[0])
            if args[0] in ("add", "delete", "change", "replace", "append", "prepend", "flush", "save", "restore",
                           "showdump", "help"):
                return self.change(args[0])
            self.warn(f'Command "{args[0]}" is unknown, try "ip route help".\n')
            return 255
        if self.json:
            return self.json_out(self.route_json())
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

# the options of ss with a value, its long options (getopt_long: a unique prefix will do), the
# letters of its socket tables, -f FAMILY and -A QUERY
TPOT_SS_VALUES = frozenset("fANFD")
TPOT_SS_LONG = {"tcp": "t", "udp": "u", "unix": "x", "raw": "w", "listening": "l", "all": "a", "numeric": "n",
                "processes": "p", "ipv4": "4", "ipv6": "6", "no-header": "H", "summary": "s", "version": "V",
                "help": "h", "extended": "e", "options": "o", "memory": "m", "info": "i", "resolve": "r",
                "family": "f", "query": "A", "socket": "A", "net": "N", "filter": "F", "diag": "D", "sctp": "S",
                "dccp": "d", "mptcp": "M", "packet": "0", "events": "E", "kill": "K", "bpf": "b", "threads": "T",
                "oneline": "O", "context": "Z", "contexts": "z"}
TPOT_SS_LETTERS = {"t": "tcp", "u": "udp", "x": "unix", "w": "raw", "S": "sctp", "d": "dccp", "M": "mptcp",
                   "0": "packet"}
TPOT_SS_FAMILIES = {"inet": 4, "inet6": 6, "unix": "unix", "link": "link", "netlink": "netlink", "vsock": "vsock",
                    "tipc": "tipc", "xdp": "xdp"}
TPOT_SS_INET = frozenset({"tcp", "udp", "raw", "sctp", "dccp", "mptcp"})
TPOT_SS_TABLES = {"all": TPOT_SS_INET | {"unix", "packet", "netlink", "vsock", "tipc", "xdp"}, "inet": TPOT_SS_INET,
                  "tcp": {"tcp"}, "mptcp": {"mptcp"}, "udp": {"udp"}, "raw": {"raw"}, "unix": {"unix"},
                  "unix_dgram": {"unix"}, "unix_stream": {"unix"}, "unix_seqpacket": {"unix"}, "packet": {"packet"},
                  "packet_raw": {"packet"}, "packet_dgram": {"packet"}, "netlink": {"netlink"}, "dccp": {"dccp"},
                  "sctp": {"sctp"}, "vsock_stream": {"vsock"}, "vsock_dgram": {"vsock"}, "tipc": {"tipc"},
                  "xdp": {"xdp"}}
TPOT_SS_TABLE_FAMILIES = {**{t: {4, 6} for t in TPOT_SS_INET}, "unix": {"unix"}, "packet": {"link"},
                          "netlink": {"netlink"}, "vsock": {"vsock"}, "tipc": {"tipc"}, "xdp": {"xdp"}}
TPOT_SS_FAMILY_TABLES = {4: set(TPOT_SS_INET), 6: set(TPOT_SS_INET), "unix": {"unix"}, "link": {"packet"},
                         "netlink": {"netlink"}, "vsock": {"vsock"}, "tipc": {"tipc"}, "xdp": {"xdp"}}


class Command_tpot_ss(TpotNetTool):
    """ss of iproute2: the columns of 4.15 and later (widths of their content), the fixed widths
    of older releases."""

    tool = "ss"

    def tpot_main(self, args: list[str]) -> int:
        self.version = tpot_net_version(self.style.get("version", "6.1.0"))
        tables, families, inet = set(), set(), 0
        listen, every, numeric, processes, header, summary = False, False, False, False, True, False
        dump = None
        words = iter(args)
        for arg in words:
            if arg.startswith("--"):
                word, eq, value = arg[2:].partition("=")
                long = word in TPOT_SS_LONG and word or next((k for k in TPOT_SS_LONG if k.startswith(word)), None)
                if not word or long is None:
                    self.warn(f"ss: unrecognized option '{arg}'\n" + self.help_text())
                    return 255
                letter = TPOT_SS_LONG[long]
                if letter not in TPOT_SS_VALUES:
                    if eq:
                        self.warn(f"ss: option '--{long}' doesn't allow an argument\n" + self.help_text())
                        return 255
                    items = [(letter, None)]
                else:
                    value = value if eq else next(words, None)
                    if value is None:
                        self.warn(f"ss: option '--{long}' requires an argument\n" + self.help_text())
                        return 255
                    items = [(letter, value)]
            elif arg.startswith("-") and len(arg) > 1:
                items, rest = [], arg[1:]
                while rest:
                    letter, rest = rest[0], rest[1:]
                    if letter in TPOT_SS_VALUES:
                        value = rest or next(words, None)
                        if value is None:
                            self.warn(f"ss: option requires an argument -- '{letter}'\n" + self.help_text())
                            return 255
                        items.append((letter, value))
                        break
                    items.append((letter, None))
            else:
                break  # a filter: shown as without it
            for letter, value in items:
                if letter in "tuxwSdM0":
                    tables.add(TPOT_SS_LETTERS[letter])
                elif letter == "l":
                    listen = True
                elif letter == "a":
                    every = True
                elif letter == "n":
                    numeric = True
                elif letter == "p":
                    processes = True
                elif letter == "4":
                    inet = 4
                elif letter == "6":
                    inet = 6
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
                elif letter == "f":
                    if value == "help":
                        self.put(self.help_text())
                        return 0
                    if value not in TPOT_SS_FAMILIES:
                        self.warn(f'ss: "{value}" is invalid family\n' + self.help_text())
                        return 255
                    family = TPOT_SS_FAMILIES[value]
                    if family in (4, 6):
                        inet = family
                    else:
                        families.add(family)
                elif letter == "A":
                    for name in value.split(","):
                        if name not in TPOT_SS_TABLES:
                            self.warn(f'ss: "{name}" is illegal socket table id\n' + self.help_text())
                            return 255
                        tables |= TPOT_SS_TABLES[name]
                elif letter == "N":
                    self.warn(f'Cannot open network namespace "{value}": No such file or directory\n')
                    return 1
                elif letter == "F":
                    self.warn("fopen filter file: No such file or directory\n")
                    return 255
                elif letter == "D":
                    dump = value  # the raw dump of the TCP sockets goes to the file, nothing to the terminal
                elif letter in "eomirEKbTOBQ":
                    continue
                else:
                    self.warn(f"ss: invalid option -- '{letter}'\n" + self.help_text())
                    return 255
        # of -4, -6, -f inet and -f inet6 the last one counts (ss: preferred_family); as ss merges its
        # defaults: a table without one of its families takes them all, a family
        # without one of its tables takes all of the family's tables
        if inet:
            families.add(inet)
        for table in list(tables):
            if not TPOT_SS_TABLE_FAMILIES[table] & families:
                families |= TPOT_SS_TABLE_FAMILIES[table]
        for family in list(families):
            if not TPOT_SS_FAMILY_TABLES[family] & tables:
                tables |= TPOT_SS_FAMILY_TABLES[family]
        kinds = tables
        sockets = self.sockets()
        if summary:
            return self.summary(sockets)
        many_kinds = len(kinds) != 1
        shown = []
        for s in sockets:
            if kinds and s["proto"] not in kinds:
                continue
            if families and s["family"] not in families:
                continue
            listening = s["state"] in ("LISTEN", "UNCONN")
            if listen and not listening:
                continue
            # without -a: connected sockets, not those in TIME-WAIT or SYN-RECV
            if not listen and not every and (listening or s["state"] in ("TIME-WAIT", "SYN-RECV")):
                continue
            shown.append(s)
        shown.sort(key=lambda s: (s["proto"] != "udp", s["state"] != "LISTEN" and s["state"] != "UNCONN"))
        if dump is not None:
            # one dump (and its NLMSG_DONE) per family asked for
            return self.diag_dump(dump, [s for s in shown if s["proto"] == "tcp"],
                                  1 if families and families <= {4, 6} and len(families) == 1 else 2)
        # the processes of other users only for root
        processes_shown = processes and self.privileged()
        rows = [self.row(s, numeric, processes_shown, many_kinds) for s in shown]
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
        if host == "*":
            # a socket of both families (IPV6_V6ONLY off)
            shown = "*"
        elif ":" in host:
            shown = f"[{host}]"
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

    def diag_dump(self, name: str, sockets: list, dumps: int = 2) -> int:
        """ss -D FILE: the netlink answers of the TCP dump into the file (a message of 116 bytes per socket
        and the NLMSG_DONE of IPv4 and IPv6; refs network-dhcp ssD-*: 40 bytes without a socket)."""
        done = (20).to_bytes(4, "little") + (3).to_bytes(2, "little") + (2).to_bytes(2, "little") + \
            (123456).to_bytes(4, "little") + bytes(8)
        data = bytes(116) * len(sockets) + done * dumps
        if getattr(self, "fs", None) is None:
            return 0  # a command without a file system (exec of tests): nothing to write to
        try:
            path = self.fs.resolve_path(name, self.protocol.cwd)
            if self.fs.isdir(path):
                self.warn("fopen dump file: Is a directory\n")
                return 255
            directory = path.rsplit("/", 1)[0] or "/"
            if not self.fs.isdir(directory):
                raise OSError
            uid = int(self.user.get("uid", 0) or 0) if isinstance(self.user, dict) else 0
            gid = int(self.user.get("gid", uid) or 0) if isinstance(self.user, dict) else 0
            if uid != 0 and not self.privileged():
                # the user may write the file only into a directory of their own or one open to all
                node = self.fs.getfile(directory)
                old = self.fs.getfile(path) if self.fs.exists(path) else None
                mode, owner = int(node[5]), int(node[2])
                writable = (owner == uid and mode & 0o200) or (int(node[3]) == gid and mode & 0o020) or mode & 0o002
                if old is not None:
                    writable = (int(old[2]) == uid and int(old[5]) & 0o200) or int(old[5]) & 0o002
                if not writable:
                    self.warn("fopen dump file: Permission denied\n")
                    return 255
            self.fs.mkfile(path, uid, gid, len(data), 0o100644)
            node = self.fs.getfile(path)
            node[7] = data  # A_CONTENTS: what cat and ls -l show of it
        except Exception:  # noqa: BLE001 - no such directory, or a file system that takes no file
            self.warn("fopen dump file: No such file or directory\n")
            return 255
        return 0

    def row(self, s: dict, numeric: bool, processes: bool, many: bool):
        old = self.version < (4, 15)
        local = self.endpoint(s["address"], s["port"], numeric, old)
        if s.get("peer"):
            peer = self.endpoint(s["peer"], s["peer_port"], True, old)
        elif s["address"] == "*":
            peer = "*", "*"
        else:
            peer = ("[::]" if s["family"] == 6 else ("*" if old else "0.0.0.0")), "*"
        state = s["state"]
        recv = 0
        send = s.get("backlog", 0) if s["state"] == "LISTEN" else 0
        # every process that holds the socket, in the order ss finds them: the later PID and the higher fd first
        owners = [[s.get("comm", ""), s.get("pid"), s.get("fd")]] + [list(o[:3]) for o in s.get("also", [])]
        owners.sort(key=lambda o: (-int(o[1] or 0), -int(o[2] or 0)))
        process = (" users:(" + ",".join(f'("{o[0][:15]}",pid={o[1]},fd={o[2]})' for o in owners) + ")") \
            if processes and s.get("pid") else ""
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
        waiting = sum(1 for s in tcp if s["state"] == "TIME-WAIT")
        if self.version < (4, 15):
            self.put(f"Total: {total} (kernel {total + 900})\nTCP:   {len(tcp)} (estab {estab}, closed 0, orphaned 0, "
                     f"synrecv 0, timewait {waiting}/0), ports 0\n\nTransport Total     IP        IPv6\n"
                     f"*\t  {total + 900:<9} {'-':<9} {'-':<9}\n"
                     f"RAW\t  {0:<9} {0:<9} {0:<9}\nUDP\t  {len(udp):<9} {udp4:<9} {udp6:<9}\n"
                     f"TCP\t  {len(tcp):<9} {tcp4:<9} {tcp6:<9}\nINET\t  {len(tcp) + len(udp):<9} {tcp4 + udp4:<9} "
                     f"{tcp6 + udp6:<9}\nFRAG\t  {0:<9} {0:<9} {0:<9}\n\n")
            return 0
        self.put(f"Total: {total}\nTCP:   {len(tcp)} (estab {estab}, closed 0, orphaned 0, timewait {waiting})\n\n"
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
        address = "::" if address == "*" else address
        host = address.partition("%")[0] if v6 and numeric else self.name_of(address, numeric)
        service = "*" if port == "*" else (str(port) if numeric else TPOT_NET_SERVICES.get(int(port), str(port)))
        text = f":::{service}" if v6 and numeric and host == "::" else f"{host}:{service}"
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
        privileged = self.privileged()
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
                owners = [(s.get("pid"), s.get("comm"), s.get("cmdline"))] + \
                    [(o[1], o[0], o[3] if len(o) > 3 else o[0]) for o in s.get("also", [])]
                first = min(owners, key=lambda o: int(o[0] or 0))
                s = dict(s, pid=first[0], comm=first[1], cmdline=first[2])
                v6 = s["family"] == 6
                proto = s["proto"] + ("6" if v6 and not busybox else "")
                local = self.endpoint(s["address"], s["port"], numeric, wide, v6)
                if s.get("peer"):
                    peer = self.endpoint(s["peer"], s["peer_port"], True, wide, v6)
                else:
                    peer = ":::*" if v6 and (numeric or busybox) else ("[::]:*" if v6 else "0.0.0.0:*")
                state = TPOT_NET_STATES.get(s["state"], s["state"])
                send = 0
                line = f"{proto:<5} {0:>6} {send:>6} {local:<23} {peer:<23} "
                if busybox:
                    line += f"{state:<12}"
                    if programs:
                        line += f"{s['pid']}/{s['comm']}" if s.get("pid") else "-"
                else:
                    line += f"{state:<11}"
                    if programs:
                        name = s.get("cmdline") or s.get("comm") or "-"
                        if self.style.get("basename"):
                            # net-tools 2.0: the program of the first word
                            name = name.split()[0].rsplit("/", 1)[-1].rstrip(":")
                        elif name.startswith("/"):
                            # net-tools 2.10: the base name of argv[0] (strrchr ends at its NUL), a title as it is
                            name = name.split(" ", 1)[0].rsplit("/", 1)[-1]
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
                self.warn(f"{CowrieConfig.get('shell', 'text_banner', fallback='BusyBox')} multi-call binary.\n\n"
                          "Usage: ifconfig [-a] interface [address]\n\nConfigure a network interface\n\n")
                return 1
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
                if not self.privileged():
                    self.warn("SIOCSIFFLAGS: Operation not permitted\n" if names[1] in ("up", "down") else
                             "SIOCSIFADDR: Operation not permitted\n")
                    return 255 if not busybox else 1
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


def patch_ps_session(cowrie_root):
    """The session's server process of ps and top (tpot_session: one PID per session, the daemon of the
    persona: sshd, sshd-session, dropbear, telnetd or in.telnetd) before Command_ps in base.py; ps itself
    is Command_tpot_ps (PS_COMMAND)."""
    path = cowrie_root / "src" / "cowrie" / "commands" / "base.py"
    replace_once(path, "\n\nclass Command_ps(HoneyPotCommand):\n", TPOT_SESSION + "\n\nclass Command_ps(HoneyPotCommand):\n")


# module code of the patched bashparse.py: the PID $$ expands to
SHELL_PID = r'''

# >>> tpot shell pid
def tpot_shell_pid(context) -> str:
    """T-Pot: the PID of the login shell of the session, the one ps, top and /proc/self show: the shell
    of the ps list, else the one after the session's server process."""
    protocol = getattr(context, "protocol", None)
    server = getattr(getattr(protocol, "user", None), "server", None)
    shells = [p["PID"] for p in (getattr(server, "process", None) or [])
              if isinstance(p, dict) and isinstance(p.get("PID"), int)
              and str(p.get("TTY") or "").startswith(("pts/", "tty")) and "getty" not in str(p.get("COMMAND") or "")]
    if shells:
        return str(shells[-1])
    pid = getattr(protocol, "tpot_session_pid", None)
    if not isinstance(pid, int):
        pid = 4000 + int.from_bytes(os.urandom(2), "big") % 4000
        try:
            protocol.tpot_session_pid = pid
        except Exception:  # noqa: BLE001 - a protocol that takes no attribute keeps no PID
            pass
    return str(pid + 1)
# <<< tpot shell pid
'''


def patch_shell_pid(cowrie_root):
    """$$ is the PID of the login shell (SHELL_PID), as ps, top and /proc/self show it; upstream keeps it
    as the text $$."""
    path = cowrie_root / "src" / "cowrie" / "shell" / "bashparse.py"
    replace_once(path, "\n\nclass ShellContext(Protocol):\n", SHELL_PID + "\n\nclass ShellContext(Protocol):\n")
    replace_once(path, '''        if special == "?":
            return self.context.get_status()
''', '''        if special == "?":
            return self.context.get_status()
        if special == "$":
            # T-Pot: $$ is the PID of the login shell
            return tpot_shell_pid(self.context)
''')
    replace_once(path, "\nimport gc\nimport re\n", "\nimport gc\nimport os\nimport re\n")


# module code of the patched honeypot.py before HoneyPotShell: the words of a pipeline's stages for ps
PIPELINE_WORDS = r'''

def tpot_stage_words(stages) -> list:
    """T-Pot: the words of each stage of a pipeline as ps shows them (quotes taken off, redirections
    left out, a compound stage as the shell that runs it); only the source text, nothing runs."""
    words = []
    for stage in list(stages)[:64]:
        items, line = getattr(stage, "items", None), getattr(stage, "line", "")
        if not isinstance(items, list) or not isinstance(line, str):
            words.append(["bash"])
            continue
        stage_words, skip = [], False
        for item in items[:256]:
            if isinstance(item, str):
                skip = True
                continue
            if skip:
                skip = False
                continue
            meta = getattr(item, "meta", None)
            if meta is None or getattr(meta, "empty", True):
                continue
            stage_words.append(tpot_unquoted(line[meta.start_pos:meta.end_pos][:4096]))
        words.append(stage_words or ["bash"])
    return words


def tpot_unquoted(text: str) -> str:
    """T-Pot: a word as the shell passes it on: quotes taken off; a backslash stays inside single quotes,
    inside double quotes it only takes $ ` " \\ and the newline, outside quotes any character."""
    out, quote, index = [], "", 0
    while index < len(text):
        char = text[index]
        index += 1
        if quote == "'":
            if char == "'":
                quote = ""
            else:
                out.append(char)
        elif char == "\\" and index < len(text) and (not quote or text[index] in "$`\"\\\n"):
            out.append(text[index])
            index += 1
        elif char in "'\"" and quote in ("", char):
            quote = "" if quote else char
        else:
            out.append(char)
    return "".join(out)
'''


def patch_pipeline_words(cowrie_root):
    """A pipeline keeps the words of its stages and the one running (tpot_pipeline of the shell), so ps
    shows the commands after it (ps aux | grep x: the grep row), as the processes of a real pipeline
    run at once."""
    path = cowrie_root / "src" / "cowrie" / "shell" / "honeypot.py"
    replace_once(path, "\n\nclass HoneyPotShell:\n", PIPELINE_WORDS + "\n\nclass HoneyPotShell:\n")
    replace_once(path, '''                d = self.run_child(
                    [stages[index]],
                    fds=fds,
''', '''                # T-Pot: the words of the stages for ps (tpot_stage_words)
                self.tpot_pipeline = (words, index)
                d = self.run_child(
                    [stages[index]],
                    fds=fds,
''')
    replace_once(path, "            self._advance()\n\n        run_from(0)\n",
                 "            self.tpot_pipeline = None\n            self._advance()\n\n"
                 "        words = tpot_stage_words(stages)\n        run_from(0)\n")


def patch_net_tools(cowrie_root):
    """ip, ss, netstat and ifconfig of the persona (NET_COMMAND after the commands of netstat.py; ifconfig.py
    registers its ifconfig): the interfaces, routes and sockets of [shell] network. Runs after patch_netstat."""
    path = cowrie_root / "src" / "cowrie" / "commands" / "netstat.py"
    replace_once(path, 'commands["/bin/netstat"] = Command_netstat\ncommands["netstat"] = Command_netstat\n',
                 'commands["/bin/netstat"] = Command_netstat\ncommands["netstat"] = Command_netstat\n' + NET_COMMAND
                 + TPOT_SESSION + TPOT_PRIVILEGED
                 + tpot_metering("Command_tpot_ip", "Command_tpot_ss", "Command_tpot_netstat", "Command_tpot_ifconfig"))
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
TPOT_SYSTEMCTL_CHANGES = ("start", "stop", "restart", "reload", "try-restart", "reload-or-restart", "force-reload",
                          "enable", "disable", "mask", "unmask", "kill", "reset-failed", "daemon-reload")
TPOT_SYSTEMCTL_FLAGS = ("--all", "-a", "--no-pager", "--no-legend", "--plain", "--full", "-l", "--quiet",
                        "-q", "--system", "--no-ask-password", "--now", "--failed", "--legend=false", "--value")
# options with a value (-n 20, --lines=20, -n20) and their long names
TPOT_SYSTEMCTL_VALUES = {"-n": "--lines", "-o": "--output", "-p": "--property", "-t": "--type", "-H": "--host",
                         "-M": "--machine", "-s": "--signal", "-P": "-P"}
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
    if s:
        return f"{s}s ago"
    # below a second: milliseconds, then microseconds
    micro = int(max(0.0, seconds) * 1000000)
    if micro >= 1000:
        return f"{micro // 1000}ms ago"
    return f"{micro}us ago" if micro else "now"


def tpot_bytes(value: int, version: int = 252) -> str:
    # systemd's format_bytes: 1024 based, one decimal cut off; from 257 on without a decimal of 0
    for factor, suffix in ((1 << 30, "G"), (1 << 20, "M"), (1 << 10, "K")):
        if value >= factor:
            whole, tenth = value // factor, value * 10 // factor % 10
            return f"{whole}{suffix}" if version >= 257 and not tenth else f"{whole}.{tenth}{suffix}"
    return f"{value}B"


def tpot_timespan(ms: int) -> str:
    # systemd's format_timespan to the millisecond: 54ms, 1.204s, 2min 3.5s, 1h 2min 3s, 1w 5d 19h 47min
    #   (its units: a year of 365.25 days, a month of a twelfth of it)
    if ms < 1000:
        return f"{ms}ms"
    parts, rest = [], ms
    for unit, size in (("y", 31557600000), ("month", 2629800000), ("w", 604800000), ("d", 86400000),
                       ("h", 3600000), ("min", 60000)):
        count, rest = divmod(rest, size)
        if count:
            parts.append(f"{count}{unit}")
    if rest >= 1000:
        # the seconds with three decimals to the millisecond (25.310s), without them on a whole second (13s)
        parts.append(f"{rest // 1000}.{rest % 1000:03d}s" if rest % 1000 else f"{rest // 1000}s")
    elif rest:
        parts.append(f"{rest}ms")
    return " ".join(parts)


def tpot_cpu(unit: dict) -> str:
    # the CPU time of a unit: microseconds below one millisecond (a unit the session just started)
    micro = unit.get("cpu_us")
    if isinstance(micro, int) and micro < 1000:
        return f"{micro}us"
    return tpot_timespan(int(unit.get("cpu_ms", micro // 1000 if isinstance(micro, int) else 0)))


TPOT_UNIT_TYPES = ("service", "socket", "target", "device", "mount", "automount", "swap", "timer", "path", "slice",
                   "scope")
TPOT_UNIT_LIMIT = 1 << 20
# where systemd looks for unit files before the unit directory of the persona (and the other spelling
# of it: /lib and /usr/lib)
TPOT_UNIT_DIRS = ("/etc/systemd/system", "/run/systemd/system")
TPOT_NO_INSTALL = {
    # systemctl's text for a unit without [Install] (enable / disable), up to 252 and from 253 on;
    # Red Hat's 252 has the newer first sentence
    "old": ("The unit files have no installation config (WantedBy=, RequiredBy=, Also=,\n"
            "Alias= settings in the [Install] section, and DefaultInstance= for template\n"
            "units). This means they are not meant to be enabled using systemctl.\n \n"
            "Possible reasons for having this kind of units are:\n"
            "• A unit may be statically enabled by being symlinked from another unit's\n"
            "  .wants/ or .requires/ directory.\n"),
    "rhel": ("The unit files have no installation config (WantedBy=, RequiredBy=, Also=,\n"
             "Alias= settings in the [Install] section, and DefaultInstance= for template\n"
             "units). This means they are not meant to be enabled or disabled using systemctl.\n \n"
             "Possible reasons for having this kind of units are:\n"
             "• A unit may be statically enabled by being symlinked from another unit's\n"
             "  .wants/ or .requires/ directory.\n"),
    "new": ("The unit files have no installation config (WantedBy=, RequiredBy=, UpheldBy=,\n"
            "Also=, or Alias= settings in the [Install] section, and DefaultInstance= for\n"
            "template units). This means they are not meant to be enabled or disabled using systemctl.\n \n"
            "Possible reasons for having these kinds of units are:\n"
            "• A unit may be statically enabled by being symlinked from another unit's\n"
            "  .wants/, .requires/, or .upholds/ directory.\n"),
}
TPOT_NO_INSTALL_TAIL = ("• A unit's purpose may be to act as a helper for some other unit which has\n"
                        "  a requirement dependency on it.\n"
                        "• A unit may be started when needed via activation (socket, path, timer,\n"
                        "  D-Bus, udev, scripted systemctl call, ...).\n"
                        "• In case of template units, the unit is meant to be enabled with some\n"
                        "  instance name specified.\n")


def tpot_unit(data: dict, name: str):
    # (the unit name systemd uses, its unit or None): a name without type is a service
    if "." not in name.rsplit("/", 1)[-1]:
        name += ".service"
    name = data.get("aliases", {}).get(name, name)
    return name, next((unit for unit in data["units"] if unit.get("name") == name), None)


def tpot_command_line(command: str, version: int) -> str:
    # systemd 249 and later quote an argument with spaces (a process title "sshd: ...", ref
    # ubuntu-22-04-status-sshd)
    return f'"{command}"' if version >= 249 and command.split(" ", 1)[0].endswith(":") else command


# --- what the session changed: on its virtual server (the file system and process list ps shows) ----

def tpot_sd_holder(cmd):
    server = getattr(getattr(getattr(cmd, "protocol", None), "user", None), "server", None)
    return server if server is not None else getattr(cmd, "protocol", None)


def tpot_sd_state(cmd) -> dict:
    '''The units the session changed ({name: changed fields}), the PIDs of daemons it started again
    ({old: new}) and the aliases it removed.'''
    holder = tpot_sd_holder(cmd)
    state = getattr(holder, "tpot_systemd", None)
    if not isinstance(state, dict):
        state = {"units": {}, "pids": {}, "unaliased": set()}
        try:
            holder.tpot_systemd = state
        except Exception:  # noqa: BLE001 - a holder that takes no attribute keeps nothing
            pass
    return state


def tpot_sd_processes(cmd):
    processes = getattr(tpot_sd_holder(cmd), "process", None)
    return processes if isinstance(processes, list) else None


def tpot_fs_text(cmd, path: str):
    try:
        text = cmd.fs.file_contents(path)
    except Exception:  # noqa: BLE001 - Cowrie's FileNotFound is no OSError; a directory, a missing file
        return None
    return bytes(text[:TPOT_UNIT_LIMIT]) if isinstance(text, (bytes, bytearray)) else None


def tpot_fs_lexists(cmd, path: str) -> bool:
    try:
        return bool(cmd.fs.lexists(path))
    except Exception:  # noqa: BLE001
        return False


def tpot_fs_link(cmd, link: str, target: str) -> bool:
    '''A link in Cowrie's file system (a node of T_LINK, 0), its directory made where missing.'''
    directory, name = link.rsplit("/", 1)
    try:
        # a link is a new node like a file: it counts against the session's limit of Cowrie (newcount)
        if int(getattr(cmd.fs, "newcount", 0)) > 10000:
            return False
        if not cmd.fs.exists(directory):
            cmd.fs.mkdir(directory, 0, 0, 4096, 0o40755)
        cmd.fs.link_entry([name, 0, 0, 0, len(target), 0o120777, time.time(), [], target, None], directory)
        if hasattr(cmd.fs, "newcount"):
            cmd.fs.newcount += 1
        return True
    except Exception:  # noqa: BLE001 - a full or read-only file system: the link is not made
        return False


def tpot_fs_unlink(cmd, link: str) -> bool:
    try:
        cmd.fs.remove(link)
        return True
    except Exception:  # noqa: BLE001
        return False


def tpot_unit_keys(text: bytes) -> dict:
    '''{"Section.Key": [values]} of a unit file (an empty assignment clears the list, as systemd does).'''
    keys: dict = {}
    section = ""
    for raw in text.decode("utf-8", "replace").splitlines()[:4096]:
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        if line[0] == "[":
            section = line.strip("[]").strip()
            continue
        key, sep, value = line.partition("=")
        if not sep:
            continue
        values = keys.setdefault(f"{section}.{key.strip()}", [])
        if value.strip():
            values.append(value.strip()[:4096])
        else:
            values.clear()
    return keys


def tpot_unit_dirs(data: dict) -> tuple:
    lib = str(data.get("unit_dir") or "/usr/lib/systemd/system")
    other = lib[4:] if lib.startswith("/usr/") else "/usr" + lib
    return TPOT_UNIT_DIRS + (lib, other)


def tpot_file_unit(cmd, data: dict, name: str):
    '''A unit of a file the session wrote (or one of the persona's tree systemctl does not list).'''
    if "@." in name:
        # a template has no unit of its own
        return None
    for directory in tpot_unit_dirs(data):
        path = f"{directory}/{name}"
        text = tpot_fs_text(cmd, path)
        if text is None:
            continue
        keys = tpot_unit_keys(text)

        def words(key):
            return [word for value in keys.get(key, []) for word in value.split()][:64]

        # (at most 8 targets and aliases: what one enable links)
        wants = ([f"{target}.wants" for target in words("Install.WantedBy")] +
                 [f"{target}.requires" for target in words("Install.RequiredBy")])[:8]
        aliases = words("Install.Alias")[:8]
        installs = bool(wants or aliases or keys.get("Install.Also"))
        command = (keys.get("Service.ExecStart") or [""])[-1].lstrip("-@:+!")
        unit = {"name": name, "description": (keys.get("Unit.Description") or [name])[-1], "path": path,
                "state": "disabled" if installs else "static", "preset": data.get("preset", "enabled"),
                "sub": "dead", "session": True, "docs": words("Unit.Documentation"), "wants": wants,
                "alias": aliases, "exec": command, "kind": (keys.get("Service.Type") or ["simple"])[-1],
                "user": (keys.get("Service.User") or ["root"])[-1][:32],
                "packaged": path in tpot_sd_state(cmd).get("packaged", ())}
        if installs and any(tpot_fs_lexists(cmd, f"/etc/systemd/system/{want}/{name}") for want in wants):
            unit["state"] = "enabled"
        return unit
    return None


def tpot_lookup(cmd, data: dict, name: str):
    '''(name, unit with what the session changed, the name was an alias) of a unit name.'''
    if "." not in name.rsplit("/", 1)[-1]:
        name += ".service"
    state = tpot_sd_state(cmd)
    alias = name in data.get("aliases", {}) and name not in state["unaliased"]
    resolved, unit = tpot_unit(data, name) if alias or name not in data.get("aliases", {}) else (name, None)
    if resolved in state.get("removed", ()):
        # a unit of a package the session removed: what its file says again, if a package brought one
        unit = None
    if unit is None:
        unit = tpot_file_unit(cmd, data, resolved)
    if unit is None:
        return resolved, None, False
    unit = dict(unit)
    unit.update(state["units"].get(resolved, {}))
    return resolved, unit, alias


def tpot_all_units(cmd, data: dict) -> list:
    '''The units systemd has loaded: the persona's and those the session started or changed.'''
    state = tpot_sd_state(cmd)
    gone = state.get("removed", set())
    units = [dict(unit, **state["units"].get(unit.get("name"), {})) for unit in data["units"]
             if unit.get("name") not in gone]
    known = {unit.get("name") for unit in units}
    for name in sorted((set(state["units"]) | gone) - known):
        _, unit, _ = tpot_lookup(cmd, data, name)
        if unit is not None:
            units.append(unit)
    return units


def tpot_label(data: dict, unit: dict) -> str:
    # how systemd 252 and later name a unit in its journal: the name, then the description
    version = int(data.get("version", 252))
    description = unit.get("description") or unit["name"]
    if description == unit["name"]:
        return description
    # (Red Hat's 252 keeps the description alone: refs almalinux-9-status-sshd, -status-socket)
    return f"{unit['name']} - {description}" if version >= 252 and not data.get("description_label") else description


def tpot_invocation(host: str, unit: dict) -> str:
    seed = f"{host} {unit['name']} {unit.get('pid', 0)} {unit.get('since', unit.get('start', 0))}"
    return hashlib.sha256(seed.encode()).hexdigest()[:32]


def tpot_journal(data: dict, unit: dict, boot: float, host: str, cmd=None, slices: bool = True) -> list:
    '''[(time, line)] of the unit's journal: the boot of the persona, then what the session did. system.slice has
    the journal of the services of the session (tpot_all_units of cmd) unless slices is False (the whole journal,
    which has their lines already).'''
    version = int(data.get("version", 252))
    label = tpot_label(data, unit)
    entries = []
    if unit["name"].endswith(".target") and not unit.get("session"):
        entries.append((boot + float(unit.get("start", 0)), f"systemd[1]: Reached target {label}."))
    elif unit.get("system") and slices:
        # system.slice: the journal of its services
        for other in tpot_all_units(cmd, data) if cmd is not None else data["units"]:
            if other.get("sub") == "running" and other["name"].endswith(".service"):
                entries += tpot_journal(data, other, boot, host)
        entries.sort(key=lambda entry: entry[0])
    if not unit.get("session") and not unit["name"].endswith((".slice", ".target")):
        since = boot + float(unit.get("start", 0))
        if unit["name"].endswith(".socket"):
            entries.append((since, f"systemd[1]: Listening on {label}."))
        elif unit.get("sub", "running") in ("running", "exited") or unit.get("stopped"):
            pid, comm = unit.get("boot_pid", unit.get("pid", 0)), unit.get("comm", "")
            done = "Finished" if unit.get("kind") == "oneshot" or (unit.get("sub") == "exited" and
                                                                    version >= 246) else "Started"
            if unit.get("kind", "notify") not in ("simple", "idle", "exec"):
                # systemd names the start of a simple service once it runs
                entries.append((since, f"systemd[1]: Starting {label}..."))
            entries += [(since, f"{comm}[{pid}]: {text}") for text in unit.get("log", [])]
            entries.append((since, f"systemd[1]: {done} {label}."))
    entries += [(float(stamp), text) for stamp, text in unit.get("journal", [])]
    return entries


def tpot_key(version: int):
    width = 11 if version >= 245 else 9
    return (lambda name: f"{name:>{width}}: "), " " * (width + 2)


def tpot_status_unit(cmd, data: dict, unit: dict, boot: float, now: float, host: str, lines: int = 10) -> list[str]:
    version = int(data.get("version", 252))
    unit = tpot_unit_cpu(cmd, data, unit, boot, now)
    key, pad = tpot_key(version)
    name, kind = unit["name"], unit["name"].rsplit(".", 1)[-1]
    sub = unit.get("sub", "running")
    active = sub not in ("dead", "failed")
    since = float(unit["since"]) if "since" in unit else boot + float(unit.get("start", 0))
    state = unit.get("state", "enabled")
    preset = "preset" if version >= 252 else "vendor preset"
    loaded = ("loaded" if not unit.get("path") else
              f"loaded ({unit.get('path')}; {state})" if state in ("static", "generated", "transient")
              else f"loaded ({unit.get('path')}; {state}; {preset}: {unit.get('preset', 'enabled')})")
    description = unit.get("description") or name
    bullet = "\u00d7" if sub == "failed" and version >= 249 else "●" if active or version < 248 else "○"
    stamp = time.strftime("%a %Y-%m-%d %H:%M:%S UTC", time.gmtime(since))
    out = [f"{bullet} {name}" + (f" - {description}" if description != name else ""), key("Loaded") + loaded]
    if active:
        out.append(key("Active") + (f"active since {stamp}; " if kind in ("slice", "target") else
                                     f"active ({sub}) since {stamp}; ") + tpot_ago(now - since, version))
        if data.get("until") and kind != "service":
            out.append(key("Until") + f"{stamp}; {tpot_ago(now - since, version)}")
    elif sub == "failed":
        out.append(key("Active") + f"failed (Result: {unit.get('result', 'exit-code')}) since {stamp}; "
                   f"{tpot_ago(now - since, version)}")
        if version >= 252 and unit.get("duration"):
            # the time of the last run (systemd keeps it through the failed start)
            out.append(key("Duration") + tpot_timespan(int(unit["duration"])))
    elif unit.get("stopped") or unit.get("condition"):
        out.append(key("Active") + f"inactive (dead) since {stamp}; {tpot_ago(now - since, version)}")
        if version >= 252 and (unit.get("duration") or unit.get("stopped")):
            out.append(key("Duration") + tpot_timespan(int(unit.get("duration") or max(
                1, int((since - float(unit.get("started", since))) * 1000)))))
    else:
        out.append(key("Active") + "inactive (dead)")
    if version >= 257 and (active or unit.get("stopped")):
        out.append(key("Invocation") + tpot_invocation(host, unit))
    if unit.get("condition") and not active:
        at = float(unit.get("condition_at", since))
        out.append(key("Condition") + f"start condition unmet at {time.strftime('%a %Y-%m-%d %H:%M:%S UTC', time.gmtime(at))}"
                   f"; {tpot_ago(now - at, version)}")
        out.append(pad + f"\u2514\u2500 {unit['condition']} was not met")
    if unit.get("triggered_by"):
        out.append(key("TriggeredBy") + f"● {unit['triggered_by']}")
    if kind == "socket" and active:
        triggers = unit.get("connection") or unit.get("triggers")
        if triggers:
            out.append(key("Triggers") + f"● {triggers}")
    for index, doc in enumerate(unit.get("docs", [])):
        out.append((key("Docs") if index == 0 else pad) + doc)
    if kind == "socket":
        for index, entry in enumerate(unit.get("listen", [])):
            out.append((key("Listen") if index == 0 else pad) + entry)
        if unit.get("accept"):
            out.append(key("Accepted") + f"{unit.get('accepted', 0)}; Connected: {unit.get('connected', 0)};")
    if not active:
        if sub == "failed" and unit.get("process"):
            pid, exec_key, words, code = (list(unit["process"]) + [0, "ExecStart", "", ""])[:4]
            out.append(key("Process") + f"{pid} {exec_key}={words} ({code})")
            if version >= 240:
                out.append(key("CPU") + tpot_cpu(unit))
        elif unit.get("stopped"):
            pid, code = unit.get("stop_pid", 0), unit.get("code", "code=exited, status=0/SUCCESS")
            runs = [run for run in unit.get("runs", []) if isinstance(run, list) and len(run) >= 3]
            if not runs and unit.get("exec"):
                runs = [[pid, "ExecStart", tpot_exec_words(str(unit["exec"]))]]
            forking = unit.get("kind") == "forking"
            # systemd 255 on names every process of the run; older ones the stop and a main ExecStart=
            #   (refs t12 *-t12r2-start-without-binary, debian-12-t12-after-remove-nginx, almalinux-9-t12-after-remove-tuned)
            for run_pid, exec_key, words in [run[:3] for run in runs][:16]:
                if version < 255 and (exec_key == "ExecStartPre" or (exec_key == "ExecStart" and forking)):
                    continue
                own = code if exec_key == "ExecStart" and not forking else "code=exited, status=0/SUCCESS"
                out.append(key("Process") + f"{run_pid} {exec_key}={words} ({own})")
            out.append(key("Main PID") + f"{pid} ({code})")
            if version >= 257:
                out.append(key("Mem peak") + tpot_bytes(int(unit.get("peak", 0)), version))
            out.append(key("CPU") + tpot_cpu(unit))
        if not unit.get("stopped") and not unit.get("journal"):
            return out
    elif kind in ("socket", "slice"):
        tasks = unit.get("tasks", 0)
        out.append(key("Tasks") + (f"{tasks} (limit: {data.get('tasks_max', 4096)})" if kind == "socket"
                                   else f"{tasks}"))
        out += tpot_memory_cpu(data, unit, key)
        out.append(key("CGroup") + (f"/system.slice/{name}" if kind == "socket" else f"/{name}"))
        if unit.get("system"):
            out += [pad + line for line in tpot_slice_services(tpot_all_units(cmd, data), version)]
        out += [pad + line for line in unit.get("tree", [])]
    elif kind == "target":
        pass
    elif sub == "running":
        pid = unit.get("pid", 0)
        out.append(key("Main PID") + f"{pid} ({unit.get('comm', '')})")
        if version >= 227:
            out.append(key("Tasks") + f"{unit.get('tasks', 1)} (limit: {data.get('tasks_max', 4096)})")
        out += tpot_memory_cpu(data, unit, key)
        out.append(key("CGroup") + f"/system.slice/{name}")
        children = [f"{pid} {tpot_command_line(unit.get('command', ''), version)}"] + \
            [f"{c[0]} {tpot_command_line(str(c[1]), version)}" for c in unit.get("children", [])]
        out += [pad + ("└─" if index == len(children) - 1 else "├─") + line
                for index, line in enumerate(children)]
    else:
        out.append(key("Main PID") + f"{unit.get('pid', 0)} (code=exited, status=0/SUCCESS)")
        if version >= 240:
            out.append(key("CPU") + tpot_cpu(unit))
    journal = tpot_journal(data, unit, boot, host, cmd)[-lines:] if lines else []
    if journal:
        out.append("")
        out += [f"{time.strftime('%b %d %H:%M:%S', time.gmtime(when))} {host} {text}" for when, text in journal]
    return out


def tpot_unit_cpu(cmd, data: dict, unit: dict, boot: float, now: float) -> dict:
    '''The unit with the CPU time of its CGroup at this call: its main process and the processes it forked
    as ps and top count them (tpot_listed_seconds), and what its helpers took (cpu_ms / cpu_us of the unit);
    system.slice the sum of its running services.'''
    host = str(getattr(cmd.protocol, "hostname", "") or "")
    if unit.get("system"):
        running = [u for u in tpot_all_units(cmd, data) if u.get("sub") == "running" and u["name"].endswith(".service")]
        total = sum(int(tpot_unit_cpu(cmd, data, u, boot, now).get("cpu_us", 0)) for u in running[:4096])
        return dict(unit, cpu_us=total, cpu_ms=total // 1000)
    if unit.get("sub") != "running" or not str(unit.get("name", "")).endswith(".service"):
        return unit
    pids = {unit.get("pid")} | {child[0] for child in unit.get("children", []) if isinstance(child, (list, tuple))
                                and child}
    seconds = sum(tpot_listed_seconds(host, p, boot, now) for p in (tpot_sd_processes(cmd) or [])[:4096]
                  if isinstance(p, dict) and p.get("PID") in pids)
    own = unit.get("cpu_us") if isinstance(unit.get("cpu_us"), int) else int(unit.get("cpu_ms", 0) or 0) * 1000
    total = int(seconds * 1e6) + own
    return dict(unit, cpu_us=total, cpu_ms=total // 1000)


def tpot_memory_cpu(data: dict, unit: dict, key) -> list:
    version = int(data.get("version", 252))
    out = []
    if version >= 238:
        memory = key("Memory") + tpot_bytes(int(unit.get("memory", 0)), version)
        if version >= 254 or data.get("peak"):
            memory += f" (peak: {tpot_bytes(int(unit.get('peak', 0)), version)})"
        out.append(memory)
    if version >= 240:
        out.append(key("CPU") + tpot_cpu(unit))
    return out


def tpot_slice_services(units: list, version: int) -> list[str]:
    # the CGroup tree of system.slice: its running services, each with its main process
    running = sorted((u for u in units if u.get("sub") == "running" and u["name"].endswith(".service")),
                     key=lambda u: u["name"].lower())
    lines = []
    for index, unit in enumerate(running):
        last = index == len(running) - 1
        lines.append(("└─" if last else "├─") + unit["name"])
        lines.append(("  " if last else "│ ") + f"└─{unit.get('pid', 0)} "
                     f"{tpot_command_line(unit.get('command', ''), version)}")
    return lines


def tpot_status_system(cmd, data: dict, boot: float, now: float, host: str) -> list[str]:
    version = int(data.get("version", 252))
    width = 11 if version >= 245 else 9
    pad = " " * (width + 2)
    since = time.strftime("%a %Y-%m-%d %H:%M:%S UTC", time.gmtime(boot))
    lines = [f"● {host}", f"{'State':>{width}}: running", f"{'Jobs':>{width}}: 0 queued",
             f"{'Failed':>{width}}: 0 units", f"{'Since':>{width}}: {since}; {tpot_ago(now - boot, version)}",
             f"{'CGroup':>{width}}: /", pad + "├─init.scope",
             pad + f"│ └─1 {data.get('init', '/sbin/init')}", pad + "└─system.slice"]
    return lines + [pad + "  " + line for line in tpot_slice_services(tpot_all_units(cmd, data), version)]


def tpot_selected(unit: dict, kinds: set, states: set, file_states: bool = False) -> bool:
    # --type and --state: the type of the unit; its LOAD, ACTIVE or SUB state (the unit file state
    # for list-unit-files)
    if kinds and unit["name"].rsplit(".", 1)[-1] not in kinds:
        return False
    if not states:
        return True
    sub = unit.get("sub", "")
    found = {unit.get("state", "enabled")} if file_states else \
        {"loaded", "inactive" if sub in ("dead", "failed") else "active", sub}
    return bool(found & states)


def tpot_list_units(cmd, data: dict, every: bool, legend: bool, kinds: set, states: set = frozenset()) -> None:
    version = int(data.get("version", 252))
    units = [u for u in tpot_all_units(cmd, data) if tpot_selected(u, kinds, states) and not u.get("template")
             and (every or states or u.get("sub", "running") != "dead")]
    units.sort(key=lambda u: (u["name"].rsplit(".", 1)[-1], u["name"].lower()))
    rows = [(u["name"], "inactive" if u.get("sub") in ("dead", "failed") else "active", u.get("sub", ""),
             u.get("description") or u["name"]) for u in units]
    name_width = max([len("UNIT")] + [len(r[0]) for r in rows])
    active_width = max([len("ACTIVE")] + [len(r[1]) for r in rows])
    sub_width = max([len("SUB")] + [len(r[2]) for r in rows])
    if legend:
        cmd.write(f"  {'UNIT':<{name_width}} LOAD   {'ACTIVE':<{active_width}} {'SUB':<{sub_width}} DESCRIPTION\n"
                  if rows else "  UNIT LOAD ACTIVE SUB DESCRIPTION\n")
    for name, active, sub, description in rows:
        cmd.write(f"  {name:<{name_width}} loaded {active:<{active_width}} {sub:<{sub_width}} {description}\n")
    if not legend:
        return
    new = version >= 253
    if rows:
        if new:
            cmd.write("\nLegend: LOAD   → Reflects whether the unit definition was properly loaded.\n"
                      "        ACTIVE → The high-level unit activation state, i.e. generalization of SUB.\n"
                      "        SUB    → The low-level unit activation state, values depend on unit type.\n\n")
        else:
            cmd.write("\nLOAD   = Reflects whether the unit definition was properly loaded.\n"
                      "ACTIVE = The high-level unit activation state, i.e. generalization of SUB.\n"
                      "SUB    = The low-level unit activation state, values depend on unit type.\n")
    elif new:
        cmd.write("\n")
    if every or "inactive" in states:
        cmd.write(f"{len(rows)} loaded units listed.\nTo show all installed unit files use "
                  "'systemctl list-unit-files'.\n")
    elif states:
        cmd.write(f"{len(rows)} loaded units listed.\n")
    else:
        cmd.write(f"{len(rows)} loaded units listed. Pass --all to see loaded but inactive units, too.\n"
                  "To show all installed unit files use 'systemctl list-unit-files'.\n")


def tpot_list_unit_files(cmd, data: dict, legend: bool, kinds: set = frozenset(),
                         states: set = frozenset()) -> None:
    version = int(data.get("version", 252))
    # a template's instances are its one unit file (getty@tty1.service: getty@.service)
    files = {}
    for unit in tpot_all_units(cmd, data):
        files.setdefault(unit.get("file") or unit["name"], dict(unit, name=unit.get("file") or unit["name"]))
    units = sorted((u for u in files.values() if tpot_selected(u, kinds, states, True)),
                   key=lambda u: (TPOT_UNIT_TYPES.index(u["name"].rsplit(".", 1)[-1])
                                  if u["name"].rsplit(".", 1)[-1] in TPOT_UNIT_TYPES else 99, u["name"].lower()))
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


# --- the verbs that change units -------------------------------------------------------------

def tpot_quote(path: str, version: int, removed: bool = False) -> str:
    # systemctl's link messages: plain up to 251, "Removed" quotes from 252, both quote '…' from 257
    if version >= 257:
        return f"'{path}'"
    if removed and version >= 252:
        return f'"{path}"'
    return path


def tpot_sysv(cmd, data: dict, name: str, verb: str) -> None:
    # Debian's systemd hands a unit with an init script of the same name to its SysV helper too
    script = name.rsplit(".", 1)[0]
    if not data.get("sysv_install") or not name.endswith(".service"):
        return
    if tpot_fs_text(cmd, f"/etc/init.d/{script}") is None:
        return
    helper = data["sysv_install"]
    cmd.errorWrite(f"Synchronizing state of {name} with SysV service script with {helper}.\n"
                   f"Executing: {helper} {verb} {script}\n")


def tpot_links(data: dict, name: str, unit: dict) -> list:
    # (link, target) of an enabled unit: its .wants / .requires link and its aliases
    path = unit.get("path", "")
    found = [(f"/etc/systemd/system/{alias}", path) for alias in unit.get("alias", [])]
    found += [(f"/etc/systemd/system/{want}/{name}", path) for want in unit.get("wants", [])]
    return found


def tpot_change(cmd, name: str) -> dict:
    return tpot_sd_state(cmd)["units"].setdefault(name, {})


def tpot_enable(cmd, data: dict, verb: str, names: list) -> int:
    version = int(data.get("version", 252))
    status = 0
    for raw in names:
        name, unit, alias = tpot_lookup(cmd, data, raw)
        if unit is None or (verb == "enable" and alias):
            target = name if unit is None else raw if raw.endswith(tuple(TPOT_UNIT_TYPES)) else raw + ".service"
            cmd.errorWrite(f"Failed to {verb} unit: Unit {target} does not exist\n" if version >= 257 else
                           f"Failed to {verb} unit: Unit file {target} does not exist.\n")
            return 1
        links = tpot_links(data, name, unit)
        if not links:
            if unit.get("state") in ("static", "generated") and (verb == "enable" or version >= 253
                                                                 or data.get("until")):
                key = "new" if version >= 253 else "rhel" if data.get("until") else "old"
                cmd.errorWrite(TPOT_NO_INSTALL[key] + TPOT_NO_INSTALL_TAIL)
            continue
        tpot_sysv(cmd, data, name, verb)
        if verb == "enable":
            for link, target in links:
                if not tpot_fs_lexists(cmd, link) and tpot_fs_link(cmd, link, target):
                    cmd.errorWrite(f"Created symlink {tpot_quote(link, version)} → "
                                   f"{tpot_quote(target, version)}.\n")
            tpot_change(cmd, name)["state"] = "enabled"
            tpot_sd_state(cmd)["unaliased"].difference_update(unit.get("alias", []))
        else:
            for link, _ in sorted(links, key=lambda pair: "/" in pair[0][len("/etc/systemd/system/"):],
                                  reverse=True):
                if tpot_fs_lexists(cmd, link) and tpot_fs_unlink(cmd, link):
                    cmd.errorWrite(f"Removed {tpot_quote(link, version, True)}.\n")
            tpot_change(cmd, name)["state"] = "disabled"
            tpot_sd_state(cmd)["unaliased"].update(unit.get("alias", []))
    return status


def tpot_unit_comm(command: str) -> str:
    '''The kernel's name of a process (15 characters), as ps and top show it: "nginx: master process ..." -> nginx,
    a script its interpreter runs by the script's name, after the interpreter's options ("/bin/sh
    /usr/bin/mysqld_safe" -> mysqld_safe, "/usr/bin/python3 -Es /usr/sbin/tuned -l -P" -> tuned); the rule of
    tpot_personas.servers.process_name.'''
    words = str(command).split()
    if not words:
        return ""
    name = words[0].rstrip(":").lstrip("@").rsplit("/", 1)[-1].lstrip("-")
    if re.fullmatch(r"(python|perl|ruby|bash|sh)[0-9.]*", name):
        rest = words[1:]
        while rest and rest[0].startswith("-"):
            rest = rest[1:]
        if rest and rest[0].startswith("/"):
            name = rest[0].rsplit("/", 1)[-1]
    return name[:15]


def tpot_helper_pids(cmd, count: int, now: float) -> list:
    '''count new PIDs for the short processes of a unit (ExecStartPre=, the parent of a forking ExecStart=,
    ExecStop=), above every PID the session has used.'''
    count = max(0, min(int(count), 16))
    if not count:
        return []
    state = tpot_sd_state(cmd)
    pids = [p.get("PID") for p in (tpot_sd_processes(cmd) or []) if isinstance(p.get("PID"), int)]
    first = max(pids + [int(state.get("pid_used", 0)), 400]) + 1
    new_pid = getattr(cmd.protocol, "tpot_new_pid", None)
    if callable(new_pid):
        first = max(first, int(new_pid(count, now)))
        try:
            cmd.protocol.tpot_pid_used = max(int(getattr(cmd.protocol, "tpot_pid_used", 0) or 0), first + count - 1)
        except Exception:  # noqa: BLE001 - a protocol that takes no attribute
            pass
    state["pid_used"] = first + count - 1
    return list(range(first, first + count))


def tpot_exec_values(cmd, unit: dict, key: str) -> list:
    '''The command lines of a key of the unit's file (Service.ExecStartPre, ...), at most 8.'''
    text = tpot_fs_text(cmd, str(unit.get("path") or ""))
    return [str(v) for v in tpot_unit_keys(text).get(f"Service.{key}", [])][:8] if text else []


def tpot_spawn(cmd, unit: dict, now: float) -> dict:
    '''The process of a unit the session starts: a row of ps (the list on the virtual server); the service of a
    package runs what its profile names (its children and the sockets they listen on, tpot_package_profile).'''
    processes = tpot_sd_processes(cmd)
    pids = [p.get("PID") for p in (processes or []) if isinstance(p.get("PID"), int)]
    profile = tpot_package_profile(str(unit.get("name", ""))) if "tpot_package_profile" in globals() else None
    count = 1 + sum(min(int(p[5]), 256) for p in (profile or {}).get("processes", [])[1:9] if len(p) == 6)
    # a new PID above every PID the session has used (tpot_new_pid of the protocol, as ps and top take theirs)
    state = tpot_sd_state(cmd)
    floor = max(pids + [int(getattr(cmd.protocol, "tpot_session_pid", 0) or 0) + 8, 400,
                        int(state.get("pid_used", 0))]) + random.randint(3, 40)
    new_pid = getattr(cmd.protocol, "tpot_new_pid", None)
    pid = floor
    if callable(new_pid):
        pid = max(floor, int(new_pid(count, now)))
        try:
            cmd.protocol.tpot_pid_used = max(int(getattr(cmd.protocol, "tpot_pid_used", 0) or 0), pid + count - 1)
        except Exception:  # noqa: BLE001 - a protocol that takes no attribute
            pass
    state["pid_used"] = pid + count - 1
    command = unit.get("command") or unit.get("exec") or ""
    user = str(unit.get("user") or "root")
    row = {"USER": user if len(user) <= 8 else user[:7] + "+", "CPU": 0.0, "MEM": 0.0, "VSZ": 2276, "RSS": 1024,
           "TTY": "?", "STAT": "Ss", "TIME": 0.0, "COMMAND": command}
    # a daemon of the persona started again: its row of ps with a new PID
    row.update(unit.get("row") or {})
    row.update({"PID": pid, "START": time.strftime("%H:%M", time.gmtime(now)), "STARTED": now})
    children = []
    if profile and profile.get("processes"):
        first = profile["processes"][0]
        row.update({"USER": str(first[0])[:7] + "+" if len(str(first[0])) > 8 else str(first[0]), "USERNAME": str(first[0]),
                    "COMMAND": str(first[1]), "VSZ": int(first[2]), "RSS": int(first[3]), "STAT": str(first[4])})
        number = pid
        groups = [[]]
        for user, title, vsz, rss, stat, count in [p for p in profile["processes"][1:] if len(p) == 6][:8]:
            groups.append([])
            for _ in range(min(int(count), 256)):
                number += 1
                children.append({"USER": str(user)[:7] + "+" if len(str(user)) > 8 else str(user),
                                 "USERNAME": str(user), "PID": number, "PPID": pid, "CPU": 0.0, "MEM": 0.0,
                                 "VSZ": int(vsz), "RSS": int(rss), "TTY": "?", "STAT": str(stat), "TIME": 0.0,
                                 "START": row["START"], "STARTED": now, "COMMAND": str(title)})
                groups[-1].append(children[-1])
    if processes is not None:
        processes.append(row)
        processes += children
    if profile:
        # the sockets of the service: the main process and its children hold each (ss lists them all)
        sockets = tpot_sd_state(cmd).setdefault("sockets", [])
        sockets[:] = [entry for entry in sockets if entry.get("unit") != unit.get("name")]
        for spec in [s for s in profile.get("sockets", []) if len(s) in (5, 6)][:8]:
            family, address, port, fd, backlog = spec[:5]
            # the processes that hold it: the main process and all its children, or (a sixth field) the
            #   processes of one entry of the profile alone (mysqld, not the mysqld_safe that started it)
            group = int(spec[5]) if len(spec) == 6 and str(spec[5]).isdigit() else 0
            holders = ([row] + children) if group == 0 or group >= len(groups) else groups[group]
            if not holders:
                continue
            first = holders[0]
            sockets.append({"proto": "tcp", "family": int(family), "address": str(address), "port": int(port),
                            "pid": first["PID"], "comm": tpot_unit_comm(first["COMMAND"]), "cmdline": first["COMMAND"],
                            "fd": int(fd), "backlog": int(backlog), "unit": unit.get("name"),
                            "also": [[tpot_unit_comm(c["COMMAND"]), c["PID"], int(fd), c["COMMAND"]]
                                     for c in holders[1:]]})
    row["TPOT_CHILDREN"] = [[c["PID"], c["COMMAND"]] for c in children]
    return row


def tpot_exec_words(value: str) -> str:
    '''A command line of a unit as systemd prints it in status: its words without their quotes.'''
    try:
        return " ".join(shlex.split(value.lstrip("@-+!:")))[:4096]
    except ValueError:
        return value.lstrip("@-+!:")[:4096]


def tpot_start_blocked(cmd, data: dict, name: str, unit: dict, now: float, label: str):
    '''What keeps a unit from starting as systemd checks it (refs t12 *-start-without-binary): an unmet
    ConditionFileIsExecutable= (the start is skipped, exit status 0), else a program of ExecStartPre= or ExecStart= that
    is gone (status=203/EXEC, the job fails, exit status 1); None if it starts.'''
    text = tpot_fs_text(cmd, str(unit.get("path") or ""))
    if not text or name.endswith(".socket"):
        return None
    keys = tpot_unit_keys(text)
    version = int(data.get("version", 252))
    for value in keys.get("Unit.ConditionFileIsExecutable", [])[:8]:
        negate, path = value.startswith("!"), value.lstrip("|!")
        if (not tpot_fs_lexists(cmd, path)) != negate:
            check = f"ConditionFileIsExecutable={value}"
            line = (f"systemd[1]: {label} skipped, unmet condition check {check}" if version >= 257 else
                    f"systemd[1]: {label} was skipped because of an unmet condition check ({check}).")
            return 0, {"sub": "dead", "condition": check, "condition_at": now}, [(now, line)], ""
    for key in ("ExecStartPre", "ExecStart"):
        for value in keys.get(f"Service.{key}", [])[:16]:
            program = value.lstrip("@+!:").split(" ", 1)[0]
            if value.startswith("-") or not program.startswith("/") or tpot_fs_lexists(cmd, program):
                continue
            new_pid = getattr(cmd.protocol, "tpot_new_pid", None)
            pid = int(new_pid(1, now)) if callable(new_pid) else random.randint(400, 30000)
            comm = program.rsplit("/", 1)[-1][:15]
            control = key == "ExecStartPre" or unit.get("kind") in ("forking", "oneshot")
            journal = [] if unit.get("kind", "simple") in ("simple", "idle", "exec") else \
                [(now, f"systemd[1]: Starting {label}...")]
            journal += [(now, f"({comm})[{pid}]: {name}: Failed to locate executable {program}: No such file or directory"),
                        (now, f"({comm})[{pid}]: {name}: Failed at step EXEC spawning {program}: No such file or directory"),
                        (now, f"systemd[1]: {name}: {'Control' if control else 'Main'} process exited, code=exited, "
                              "status=203/EXEC"),
                        (now, f"systemd[1]: {name}: Failed with result 'exit-code'."),
                        (now, f"systemd[1]: Failed to start {label}.")]
            failed = {"sub": "failed", "result": "exit-code", "stopped": False,
                      "process": [pid, key, tpot_exec_words(value), "code=exited, status=203/EXEC"],
                      "cpu_us": random.randint(180, 420)}
            simple = unit.get("kind", "simple") in ("simple", "idle")
            text = "" if simple and not control else (
                f"Job for {name} failed because the control process exited with error code.\n"
                f'See "systemctl status {name}" and "journalctl -xeu {name}" for details.\n')
            return (0 if simple and not control else 1), failed, journal, text
    return None


def tpot_start(cmd, data: dict, name: str, unit: dict, now: float):
    if unit.get("sub", "running") not in ("dead", "failed"):
        return 0
    change = tpot_change(cmd, name)
    journal = list(change.get("journal", []))
    label = tpot_label(data, unit)
    kind = name.rsplit(".", 1)[-1]
    blocked = tpot_start_blocked(cmd, data, name, unit, now, label)
    if blocked is not None:
        code, fields, lines, text = blocked
        change.update({"since": now, "started": float(unit.get("started", now) or now), **fields})
        change["journal"] = (journal + lines)[-50:]
        if text:
            cmd.errorWrite(text)
        return code
    change.update({"since": now, "started": now, "stopped": False})
    change.pop("condition", None)
    change.pop("process", None)
    if kind == "socket":
        # a new run: the processes of the last one go (status names those of this run)
        change.update({"sub": "listening", "ports": [], "runs": []})
        journal.append((now, f"systemd[1]: Listening on {label}."))
    elif unit.get("kind") == "oneshot" or not (unit.get("command") or unit.get("exec")):
        change.update({"sub": "exited", "runs": []})
        journal += [(now, f"systemd[1]: Starting {label}..."), (now, f"systemd[1]: Finished {label}.")]
    else:
        # the processes of the run as status names them after a stop: ExecStartPre=, the ExecStart= of a forking
        #   unit (its parent exits, the daemon it forks is the main process), the main process
        pres = tpot_exec_values(cmd, unit, "ExecStartPre")
        starts = tpot_exec_values(cmd, unit, "ExecStart")[-1:] or ([str(unit["exec"])] if unit.get("exec") else [])
        forking = unit.get("kind") == "forking"
        helpers = tpot_helper_pids(cmd, len(pres) + (1 if forking and starts else 0), now)
        row = tpot_spawn(cmd, unit, now)
        runs = [[pid, "ExecStartPre", tpot_exec_words(value)] for pid, value in zip(helpers, pres)]
        runs += [[helpers[-1] if forking else row["PID"], "ExecStart", tpot_exec_words(value)] for value in starts]
        change["runs"] = runs
        children = row.pop("TPOT_CHILDREN", [])
        old = unit.get("boot_pid", unit.get("pid"))
        if not unit.get("session") and isinstance(old, int):
            tpot_sd_state(cmd)["pids"][old] = row["PID"]
        command = row["COMMAND"]
        change.update({"sub": "running", "pid": row["PID"], "command": command, "children": children,
                       "comm": unit.get("comm") or tpot_unit_comm(command),
                       "tasks": 1 + len(children), "memory": row["RSS"] * 1024 // 3, "peak": row["RSS"] * 1024 + 524288,
                       "cpu_us": random.randint(500, 990), "boot_pid": old if isinstance(old, int) else row["PID"]})
        if unit.get("kind", "simple") in ("simple", "idle"):
            journal.append((now, f"systemd[1]: Started {label}."))
        else:
            journal += [(now, f"systemd[1]: Starting {label}..."), (now, f"systemd[1]: Started {label}.")]
    change["journal"] = journal[-50:]
    return 0


# the daemons of the personas that end on SIGTERM with status 0 (refs final-wave *-ffw-stop-status-*: sshd, crond of
#   cronie, rsyslogd, chronyd, systemd-journald; t12 *-after-remove-*: nginx, tuned); the others are killed by it
TPOT_TERM_EXITS = frozenset({"sshd", "crond", "rsyslogd", "chronyd", "nginx", "tuned"})


def tpot_stop(cmd, data: dict, name: str, unit: dict, now: float) -> None:
    if unit.get("sub", "running") in ("dead", "failed"):
        return
    change = tpot_change(cmd, name)
    journal = list(change.get("journal", []))
    label = tpot_label(data, unit)
    version = int(data.get("version", 252))
    pid = unit.get("pid", 0)
    boot_time = getattr(cmd.protocol, "boot_time", None)
    boot = float(boot_time()) if callable(boot_time) else now
    # the CPU time of its CGroup up to the stop (status keeps it)
    spent = tpot_unit_cpu(cmd, data, unit, boot, now)
    change.update({"cpu_us": int(spent.get("cpu_us", 0) or 0), "cpu_ms": int(spent.get("cpu_ms", 0) or 0)})
    processes = tpot_sd_processes(cmd)
    if processes is not None and unit.get("sub") == "running":
        rows = [p for p in processes if p.get("PID") == pid]
        if rows and not unit.get("session"):
            change["row"] = dict(rows[0])
        # the processes it forked go with it (its CGroup), and the sockets of a package's service
        gone = {pid} | {int(c[0]) for c in unit.get("children", []) if str(c[0]).isdigit()}
        processes[:] = [p for p in processes if p.get("PID") not in gone]
        change["children"] = []
        sockets = tpot_sd_state(cmd).get("sockets")
        if isinstance(sockets, list):
            sockets[:] = [entry for entry in sockets if entry.get("unit") != name]
    if "since" in unit:
        started = float(unit["since"])
    else:
        # a daemon of the boot: it ran since its start after the boot
        started = boot + float(unit.get("start", 0)) if callable(boot_time) and "start" in unit else now
    # how SIGTERM ends a daemon (refs final-wave *-ffw-stop-status-*, t12 *-after-remove-*): those that handle it exit
    #   with status 0 (the daemons of TPOT_TERM_EXITS, systemd's own, those of a package's unit), the others (Debian's
    #   cron, the program of a unit the session wrote) are killed by it
    comm = str(unit.get("comm") or tpot_unit_comm(str(unit.get("command") or unit.get("exec") or "")))
    killed = not unit.get("packaged") and (unit.get("session") or not (
        comm in TPOT_TERM_EXITS or comm.startswith("systemd-")))
    stops = [] if name.endswith(".socket") else tpot_exec_values(cmd, unit, "ExecStop")
    runs = [list(run) for run in unit.get("runs", []) if isinstance(run, list) and len(run) >= 3][:16]
    runs += [[stop_pid, "ExecStop", tpot_exec_words(value)]
             for stop_pid, value in zip(tpot_helper_pids(cmd, len(stops), now), stops)]
    change.update({"sub": "dead", "since": now, "started": started, "stopped": True, "stop_pid": pid,
                   "duration": max(1, int((now - started) * 1000)), "runs": runs,
                   "code": "code=killed, signal=TERM" if killed else "code=exited, status=0/SUCCESS"})
    if name.endswith(".socket"):
        journal += [(now, f"systemd[1]: {name}: Deactivated successfully."), (now, f"systemd[1]: Closed {label}.")]
        # the ports systemd stops listening on (ss and netstat drop its rows of them)
        change["ports"] = sorted({int(m) for m in re.findall(r":(\d+) \(", " ".join(unit.get("listen", [])))})
    else:
        journal.append((now, f"systemd[1]: Stopping {label}..."))
        if version >= 248:
            journal.append((now, f"systemd[1]: {name}: Deactivated successfully."))
        journal.append((now, f"systemd[1]: Stopped {label}."))
    change["journal"] = journal[-50:]


def tpot_lifecycle(cmd, data: dict, verb: str, names: list, now: float) -> int:
    for raw in names:
        name, unit, _ = tpot_lookup(cmd, data, raw)
        if unit is None:
            # (refs final-wave *-systemctl-*-nosuch: stop and kill "not loaded", kill exits 1)
            if verb == "kill":
                cmd.errorWrite(f"Failed to kill unit {name}: Unit {name} not loaded.\n")
                return 1
            reason = "not loaded" if verb == "stop" else "not found"
            cmd.errorWrite(f"Failed to {tpot_job(verb)} {name}: Unit {name} {reason}.\n")
            return 5
        status = 0
        if verb in ("start", "reload-or-restart"):
            status = tpot_start(cmd, data, name, unit, now)
        elif verb == "stop":
            # a unit a socket starts: systemd says the socket stays (ref final-wave ubuntu-24-04 service ssh stop)
            trigger = str(unit.get("triggered_by") or "")
            if trigger and (tpot_lookup(cmd, data, trigger)[1] or {}).get("sub", "dead") not in ("dead", "failed"):
                cmd.errorWrite(f"Stopping '{name}', but its triggering units are still active:\n{trigger}\n")
            tpot_stop(cmd, data, name, unit, now)
        elif verb in ("restart", "try-restart"):
            if verb == "try-restart" and unit.get("sub", "running") in ("dead", "failed"):
                continue
            tpot_stop(cmd, data, name, unit, now)
            _, unit, _ = tpot_lookup(cmd, data, name)
            status = tpot_start(cmd, data, name, unit, now)
        if status:
            return int(status)
    return 0


def tpot_job(verb: str) -> str:
    '''The name systemctl gives the job of a verb in its messages (force-reload is reload-or-try-restart).'''
    return "reload-or-try-restart" if verb == "force-reload" else verb


# --- show ------------------------------------------------------------------------------------

def tpot_show_stamp(value) -> str:
    return time.strftime("%a %Y-%m-%d %H:%M:%S UTC", time.gmtime(value)) if value else ""


def tpot_show(cmd, data: dict, unit, name: str, boot: float) -> list:
    '''(property, value) of a unit in the order systemctl show prints them (the service's own first).'''
    version = int(data.get("version", 252))
    if unit:
        unit = tpot_unit_cpu(cmd, data, unit, boot, time.time())
    unit = unit or {}
    sub = unit.get("sub", "dead") if unit else "dead"
    active = sub not in ("dead", "failed")
    since = float(unit["since"]) if "since" in unit else boot + float(unit.get("start", 0))
    kind = name.rsplit(".", 1)[-1]
    pid = unit.get("pid", 0) if sub == "running" else 0
    props = []
    if kind == "service":
        props += [("Type", unit.get("kind", "simple") if unit else "simple")]
        if version >= 250:
            props.append(("ExitType", "main"))
        props += [("Restart", "no"), ("NotifyAccess", "main" if unit.get("kind") == "notify" else "none"),
                  ("RestartUSec", "100ms"), ("TimeoutStartUSec", "1min 30s"), ("TimeoutStopUSec", "1min 30s"),
                  ("RuntimeMaxUSec", "infinity"), ("WatchdogUSec", "0"), ("RootDirectoryStartOnly", "no"),
                  ("RemainAfterExit", "yes" if sub == "exited" else "no"), ("GuessMainPID", "yes"),
                  ("MainPID", str(pid)), ("ControlPID", "0"), ("StatusErrno", "0"), ("Result", "success"),
                  ("UID", "[not set]"), ("GID", "[not set]"), ("NRestarts", "0"),
                  ("ExecMainStartTimestamp", tpot_show_stamp(since) if unit and (active or unit.get("stopped"))
                   else ""),
                  ("ExecMainPID", str(unit.get("stop_pid", pid) if unit else 0)),
                  ("ExecMainCode", "0" if active or not unit else "1"), ("ExecMainStatus", "0")]
    elif kind == "socket":
        props += [("Accept", "yes" if unit.get("accept") else "no"),
                  ("NConnections", str(unit.get("connected", 0))), ("NAccepted", str(unit.get("accepted", 0)))]
    if kind in ("service", "socket", "slice"):
        props += [("Slice", "system.slice" if kind != "slice" else "-.slice"),
                  ("ControlGroup", (f"/system.slice/{name}" if kind != "slice" else f"/{name}") if active else ""),
                  ("MemoryCurrent", str(unit.get("memory", 0)) if active else "[not set]"),
                  ("CPUUsageNSec", str(int(unit.get("cpu_us", int(unit.get("cpu_ms", 0)) * 1000)) * 1000) if active
                   else "[not set]"),
                  ("TasksCurrent", str(unit.get("tasks", 1 if kind == "service" else 0)) if active else "[not set]")]
    state = unit.get("state", "") if unit else ""
    props += [("Id", name), ("Names", name), ("Description", unit.get("description", name) if unit else name),
              ("Documentation", " ".join(f'"{d}"' for d in unit.get("docs", []))),
              ("LoadState", "loaded" if unit else "not-found"),
              ("ActiveState", "active" if active else "inactive")]
    if version >= 246:
        props.append(("FreezerState", "running"))
    props += [("SubState", sub), ("FragmentPath", unit.get("path", "") if unit else ""),
              ("UnitFileState", state), ("UnitFilePreset", unit.get("preset", "") if unit and state not in
                                         ("static", "generated", "") else ""),
              ("ActiveEnterTimestamp", tpot_show_stamp(since) if active else ""),
              ("CanStart", "yes" if unit else "no"), ("CanStop", "yes" if unit else "no"),
              ("CanReload", "no")]
    if active:
        props.append(("InvocationID", tpot_invocation(cmd.protocol.hostname.split(".")[0], unit)))
    return props


TPOT_MANAGER_PROPS = ("Version", "Features", "Architecture", "Tainted", "SystemState", "DefaultStandardOutput")


def tpot_show_manager(data: dict) -> list:
    arch = "x86-64" if CowrieConfig.get("shell", "hardware_platform", fallback="x86_64") == "x86_64" else "arm64"
    return [("Version", str(data.get("full") or data.get("version", ""))), ("Features", data.get("features", "")),
            ("Architecture", arch), ("Tainted", ""), ("SystemState", "running"),
            ("DefaultStandardOutput", "journal")]


def tpot_systemctl_show(cmd, data: dict, names: list, wanted: list, value_only: bool, boot: float) -> int:
    wanted_names = {p for item in wanted for p in item.split(",") if p}
    blocks = []
    for raw in names or [None]:
        if raw is None:
            props = tpot_show_manager(data)
        else:
            name, unit, _ = tpot_lookup(cmd, data, raw)
            props = tpot_show(cmd, data, unit, name, boot)
        lines = [value if value_only else f"{prop}={value}" for prop, value in props
                 if (prop in wanted_names if wanted_names else value != "" or prop in ("Documentation",))]
        blocks.append("".join(line + "\n" for line in lines))
    cmd.write("\n".join(blocks))
    return 0


def tpot_systemctl(cmd, args: list[str]) -> int:
    # one systemctl call on cmd (a HoneyPotCommand); returns the exit status, 1 on an unexpected error
    try:
        return tpot_systemctl_run(cmd, args)
    except Exception:  # noqa: BLE001 - the command always exits
        return 1


def tpot_systemctl_run(cmd, args: list[str]) -> int:
    data = tpot_units()
    version = int(data.get("version", 252))
    words, every, legend, quiet, now_flag, value_only, failed = [], False, True, False, False, False, False
    values: dict[str, str] = {}
    properties: list[str] = []
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
            if option == "-P":
                # -P NAME: --value --property=NAME
                option, value_only = "--property", True
            if option == "--property":
                properties.append(value)
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
            now_flag = now_flag or arg == "--now"
            value_only = value_only or arg == "--value"
            failed = failed or arg == "--failed"
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
    states = {state for state in values.get("--state", "").split(",") if state} | ({"failed"} if failed else set())
    verb, names = (words[0], words[1:]) if words else ("list-units", [])
    boot, now = cmd.protocol.boot_time(), time.time()
    host = cmd.protocol.hostname.split(".")[0]
    if verb == "list-units":
        tpot_list_units(cmd, data, every, legend, kinds, states)
        return 0
    if verb == "list-unit-files":
        tpot_list_unit_files(cmd, data, legend, kinds, states)
        return 0
    if verb == "is-system-running":
        if not quiet:
            cmd.write("running\n")
        return 0
    if verb == "show":
        return tpot_systemctl_show(cmd, data, names, properties, value_only, boot)
    if verb == "status":
        if not names:
            cmd.write("".join(line + "\n" for line in tpot_status_system(cmd, data, boot, now, host)))
            return 0
        status, blocks = 0, []
        for raw in names:
            name, unit, _ = tpot_lookup(cmd, data, raw)
            if "@." in name:
                cmd.errorWrite(f"Failed to get properties: Unit name {name} is neither a valid invocation ID "
                               "nor unit name.\n")
                return 1
            if unit is None:
                cmd.errorWrite(f"Unit {name} could not be found.\n")
                status = 4
                continue
            if unit.get("sub", "running") in ("dead", "failed") and status == 0:
                status = 3
            blocks.append("".join(line + "\n" for line in tpot_status_unit(
                cmd, data, tpot_with_session(cmd, unit), boot, now, host,
                10 if lines is None else int(lines))))
        cmd.write("\n".join(blocks))
        return status
    if verb == "cat":
        if not names:
            cmd.errorWrite("Too few arguments.\n")
            return 1
        # as systemd: a unit without a file is named on stderr, the others are still printed
        status, first = 0, True
        for raw in names:
            name, unit, _ = tpot_lookup(cmd, data, raw)
            text = tpot_fs_text(cmd, unit["path"]) if unit else None
            if text is None:
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
        for raw in names:
            name, unit, alias = tpot_lookup(cmd, data, raw)
            running = unit is not None and unit.get("sub", "running") not in ("dead", "failed")
            failed = unit is not None and unit.get("sub") == "failed"
            if verb == "is-active":
                answer, ok = ("active", True) if running else ("failed" if failed else "inactive", False)
            elif verb == "is-failed":
                answer, ok = ("active", False) if running else ("failed", True) if failed else ("inactive", False)
            elif unit is None:
                missing = True
                if version >= 255:
                    # systemd 255 and later answer the state not-found, exit status 4 (ref
                    # ubuntu-24-04-is-enabled-nosuch)
                    if not quiet:
                        cmd.write("not-found\n")
                    continue
                cmd.errorWrite(f"Failed to get unit file state for {name}: No such file or directory\n")
                continue
            else:
                answer = "alias" if alias else unit.get("state", "enabled")
                ok = answer in TPOT_SYSTEMCTL_ENABLED
            if not quiet:
                cmd.write(answer + "\n")
            found = found or ok
        if missing:
            return 4 if version >= 255 else 1
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
        if not tpot_privileged(cmd):
            name = tpot_unit(data, names[0])[0]
            if verb in ("enable", "disable", "mask", "unmask", "reenable"):
                cmd.errorWrite(f"Failed to {verb} unit: Interactive authentication required.\n")
                return 1
            # a job the user may not start: EXIT_NOPERMISSION (refs final-wave *-tester-systemctl-restart-dbus)
            cmd.errorWrite(f"Failed to {tpot_job(verb)} {name}: Interactive authentication required.\n"
                           f"See system logs and 'systemctl status {name}' for details.\n")
            return 4 if verb != "kill" else 1
        if verb in ("enable", "disable"):
            status = tpot_enable(cmd, data, verb, names)
            if status == 0 and now_flag:
                status = tpot_lifecycle(cmd, data, "start" if verb == "enable" else "stop", names, now)
            return status
        if verb == "reenable":
            status = tpot_enable(cmd, data, "disable", names)
            return status or tpot_enable(cmd, data, "enable", names)
        if verb in ("start", "stop", "restart", "try-restart", "reload-or-restart", "reload", "kill", "force-reload"):
            return tpot_lifecycle(cmd, data, verb, names, now)
        for raw in names:
            name, unit, _ = tpot_lookup(cmd, data, raw)
            if unit is None and verb not in ("mask", "unmask", "reset-failed"):
                cmd.errorWrite(f"Failed to {verb} {name}: Unit {name} not found.\n")
                return 5
        return 0
    cmd.errorWrite(f"Unknown command verb {verb}.\n" if version >= 240 else f"Unknown operation '{verb}'.\n")
    return 1


def tpot_with_session(cmd, unit: dict) -> dict:
    '''A socket of the session's connection (telnet.socket of Red Hat: Accept=yes) shows it; the
    user slice holds the session's processes.'''
    session = None
    if unit.get("accept") or unit["name"] in ("user.slice", "inetd.service"):
        try:
            session = tpot_session(cmd.protocol, str(cmd.user.get("username", "root")))
        except Exception:  # noqa: BLE001 - a protocol without a session (tests, exec)
            session = None
    if session is None:
        return unit
    unit = dict(unit)
    if unit.get("accept") and session.get("telnet"):
        local, peer, port = tpot_connection(cmd)
        if peer:
            unit["connection"] = f"telnet@{int(unit.get('accepted', 0))}-{local}:23-{peer}:{port}.service"
            unit["accepted"] = int(unit.get("accepted", 0)) + 1
            unit["connected"] = 1
    if unit["name"] == "inetd.service" and session.get("telnet") and session.get("chain") \
            and unit.get("sub") == "running":
        # the telnetd inetd started for the session's connection (refs systemd-telnet *-session-status-listener)
        unit["children"] = list(unit.get("children", [])) + [(session["chain"][0]["pid"], session["chain"][0]["command"])]
        unit["tasks"] = int(unit.get("tasks", 1)) + 1
        if CowrieConfig.get("shell", "telnet_server", fallback="") == "inetutils-2.6":
            # inetutils 2.6 logs the connection and pam the session at its start (ref debian-13-session-status-listener)
            _, peer, _ = tpot_connection(cmd)
            now = getattr(cmd.protocol, "logintime", None)
            now = float(now) if isinstance(now, (int, float)) and not isinstance(now, bool) else time.time()
            uid = int(cmd.user.get("uid", 0) or 0) if isinstance(cmd.user, dict) else 0
            name = str(cmd.user.get("username", "root")) if isinstance(cmd.user, dict) else "root"
            unit["journal"] = list(unit.get("journal", [])) + [
                (now, f"telnetd[{session['chain'][0]['pid']}]: connect from {peer} ({peer})"),
                (now, f"login[{session['chain'][-1]['pid']}]: pam_unix(remote:session): session opened for user "
                      f"{name}(uid={uid}) by (uid=0)")]
    if unit["name"] == "user.slice":
        uid = int(cmd.user.get("uid", 0) or 0) if isinstance(cmd.user, dict) else 0
        # the telnet daemon stays in the CGroup of its service (inetd.service, telnet@.service): login and the
        #   shell move to the session's scope (refs systemd-telnet *-session-status-user-slice)
        chain = session.get("chain", [])
        rows = [f"{entry['pid']} {tpot_command_line(entry['command'], int(tpot_units().get('version', 252)))}"
                for entry in (chain[1:] if session.get("telnet") and len(chain) > 1 else chain)]
        shells = [p for p in (tpot_sd_processes(cmd) or []) if str(p.get("TTY") or "").startswith("pts/")]
        rows += [f"{p['PID']} {p.get('COMMAND', '')}" for p in shells[-1:]]
        tree = [f"└─user-{uid}.slice", "  └─session-1.scope"]
        tree += [("    └─" if index == len(rows) - 1 else "    ├─") + row
                 for index, row in enumerate(rows)]
        unit.update({"tree": tree, "tasks": len(rows)})
    return unit


def tpot_connection(cmd):
    '''(the persona's address, peer address, peer port) of the session's connection.'''
    try:
        peer = str(getattr(cmd.protocol, "clientIP", "") or "")
        port = int(getattr(cmd.protocol, "realClientPort", 0) or 0)
        network = json.loads(Path(CowrieConfig.get("shell", "network", fallback="")).read_text(encoding="utf-8"))
        local = str(network["interfaces"][0]["inet"])
    except Exception:  # noqa: BLE001 - no network file (tests): no connection to show
        return "", "", 0
    return local, peer, port


class Command_systemctl(HoneyPotCommand):
    def call(self) -> None:
        self.exit_code = tpot_systemctl(self, self.args)


# journalctl of the persona: the journal lines systemctl status shows, of one or more units or of all
TPOT_JOURNAL_FLAGS = frozenset({"--no-pager", "-e", "--pager-end", "-x", "--catalog", "-b", "--boot", "-a", "--all",
                                "-l", "--full", "-m", "--merge", "--no-tail", "-f", "--follow", "--system",
                                "--utc", "--no-hostname", "-k", "--dmesg"})
TPOT_JOURNAL_LIMIT = 2000
# journalctl of a user outside the groups that read the system journal (refs track-b j2-*: debian:12,
# ubuntu:22.04/24.04, almalinux:9 with journald and the ACLs of its tmpfiles)
TPOT_JOURNAL_HINT = ("Hint: You are currently not seeing messages from other users and the system.\n"
                     "      Users in the 'systemd-journal' group can see all messages. Pass -q to\n"
                     "      turn off this notice.\n")
TPOT_JOURNAL_GROUPS = frozenset({"adm", "systemd-journal", "wheel"})


def tpot_journal_reader(cmd) -> bool:
    '''Root, sudo, or a member of adm, systemd-journal or wheel (/etc/group of the session).'''
    if tpot_privileged(cmd):
        return True
    name = str(cmd.user.get("username", "")) if isinstance(cmd.user, dict) else ""
    text = tpot_fs_text(cmd, "/etc/group") or b""
    for line in text.decode("utf-8", "replace").splitlines():
        fields = line.split(":")
        if len(fields) >= 4 and fields[0] in TPOT_JOURNAL_GROUPS and name in fields[3].split(","):
            return True
    return False


def tpot_journalctl(cmd, args: list[str]) -> int:
    try:
        return tpot_journalctl_run(cmd, args)
    except Exception:  # noqa: BLE001 - the command always exits
        return 1


def tpot_journalctl_run(cmd, args: list[str]) -> int:
    data = tpot_units()
    version = int(data.get("version", 252))
    names, lines, quiet, reverse = [], None, False, False
    items = list(args)
    while items:
        arg = items.pop(0)
        if arg.startswith("--"):
            key, eq, value = arg.partition("=")
            if key in ("--unit", "--lines"):
                if not eq:
                    if not items:
                        cmd.errorWrite(f"journalctl: option '{key}' requires an argument\n")
                        return 1
                    value = items.pop(0)
                if key == "--unit":
                    names.append(value)
                else:
                    lines = value
            elif key in ("--quiet",):
                quiet = True
            elif key in ("--reverse",):
                reverse = True
            elif key in ("--since", "--until", "--priority", "--output", "--identifier", "--grep"):
                if not eq and items:
                    items.pop(0)
            elif key not in TPOT_JOURNAL_FLAGS:
                cmd.errorWrite(f"journalctl: unrecognized option '{arg}'\n")
                return 1
            continue
        if not arg.startswith("-") or arg == "-":
            continue  # a match (FIELD=value) or a path: shown as without it
        # a cluster of short options (-xe, -xeu ssh, -fu ssh, -n5): the one of a value takes the rest or the next word
        rest = arg[1:]
        while rest:
            letter, rest = rest[0], rest[1:]
            if letter in "unSUptog":
                value = rest or (items.pop(0) if items else None)
                if value is None:
                    cmd.errorWrite(f"journalctl: option requires an argument -- '{letter}'\n")
                    return 1
                if letter == "u":
                    names.append(value)
                elif letter == "n":
                    lines = value
                rest = ""
            elif letter == "q":
                quiet = True
            elif letter == "r":
                reverse = True
            elif letter in "xefbalmk":
                continue
            else:
                cmd.errorWrite(f"journalctl: invalid option -- '{letter}'\n")
                return 1
    count = None
    if lines is not None:
        if lines == "all":
            count = None
        else:
            count = tpot_number(lines.lstrip("+"), 0xFFFFFFFF)
            if count is None:
                cmd.errorWrite(f"Failed to parse lines '{lines}'\n")
                return 1
    if not tpot_journal_reader(cmd):
        if not quiet:
            cmd.errorWrite(TPOT_JOURNAL_HINT)
        cmd.errorWrite("No journal files were opened due to insufficient permissions.\n")
        return 1
    boot = cmd.protocol.boot_time()
    host = cmd.protocol.hostname.split(".")[0]
    if names:
        units = []
        for raw in names:
            name = raw if "." in raw.rsplit("/", 1)[-1] else raw + ".service"
            if name in data.get("aliases", {}):
                # the journal names a unit by its own name (_SYSTEMD_UNIT), not by an alias (refs
                # ubuntu-22-04-journalctl-n5-sshd, debian-12-journalctl-n0005-sshd)
                continue
            _, unit, _ = tpot_lookup(cmd, data, raw)
            if unit is not None:
                units.append(tpot_with_session(cmd, unit))
    else:
        units = [tpot_with_session(cmd, unit) for unit in tpot_all_units(cmd, data)]
    # the whole journal names each line once: a slice adds none of its own (its services have them)
    entries = sorted(((when, text) for unit in units
                      for when, text in tpot_journal(data, unit, boot, host, cmd, slices=bool(names))),
                     key=lambda entry: entry[0])
    entries = entries[-TPOT_JOURNAL_LIMIT:]
    if count is not None:
        entries = entries[-count:] if count else []
    if reverse:
        entries.reverse()
    out = []
    if version < 240 and not quiet:
        # systemd 219 names the span of its journal first
        first = time.strftime("%a %Y-%m-%d %H:%M:%S UTC", time.gmtime(boot))
        last = time.strftime("%a %Y-%m-%d %H:%M:%S UTC", time.gmtime(time.time()))
        out.append(f"-- Logs begin at {first}, end at {last}. --")
    out += [f"{time.strftime('%b %d %H:%M:%S', time.gmtime(when))} {host} {text}" for when, text in entries]
    if not entries and not quiet:
        out.append("-- No entries --")
    cmd.write("".join(line + "\n" for line in out))
    return 0


class Command_journalctl(HoneyPotCommand):
    def call(self) -> None:
        self.exit_code = tpot_journalctl(self, self.args)


commands["/usr/bin/journalctl"] = Command_journalctl
commands["/bin/journalctl"] = Command_journalctl
commands["journalctl"] = Command_journalctl


commands["/usr/bin/systemctl"] = Command_systemctl
commands["/bin/systemctl"] = Command_systemctl
commands["systemctl"] = Command_systemctl
"""


def patch_systemctl(cowrie_root):
    """systemctl of the persona (SYSTEMCTL_COMMAND, in the module of service); `service X status`
    shows the status of X as systemctl does (Red Hat names the redirection first). Runs after
    patch_service."""
    path = cowrie_root / "src" / "cowrie" / "commands" / "service.py"
    replace_once(path, "import getopt\n", "import base64\nimport calendar\nimport functools\nimport getopt\nimport hashlib\n"
                 "import json\nimport os\nimport random\nimport re\nimport shlex\nimport time\n")
    replace_once(
        path,
        """    def call(self) -> None:
        try:
            opts, args = getopt.gnu_getopt(
                self.args, "h", ["help", "status-all", "full-restart"]
            )
""",
        """    def call(self) -> None:
        # T-Pot: service NAME status, start, stop, restart, reload, try-restart and force-reload of a persona with
        #   systemd are its systemctl, as the service wrapper runs it (refs final-wave *-service-*): Red Hat names the
        #   redirection first, Debian's reload of a unit it has not is "NAME: unrecognized service"
        if len(self.args) >= 2 and self.args[1] in ("status", "start", "stop", "restart", "reload", "try-restart",
                                                    "force-reload") and tpot_units().get("units"):
            data, verb = tpot_units(), self.args[1]
            if data.get("redirect"):
                self.errorWrite(f"Redirecting to /bin/systemctl {verb} {tpot_unit(data, self.args[0])[0]}\\n")
            elif verb in ("reload", "force-reload") and tpot_lookup(self, data, self.args[0])[1] is None:
                self.errorWrite(f"{self.args[0]}: unrecognized service\\n")
                self.exit_code = 1
                return
            elif verb == "force-reload":
                verb = "reload"
            self.exit_code = tpot_systemctl(self, [verb, self.args[0]])
            return
        try:
            opts, args = getopt.gnu_getopt(
                self.args, "h", ["help", "status-all", "full-restart"]
            )
""",
    )
    replace_once(path, 'commands["service"] = Command_service\n',
                 'commands["service"] = Command_service\n' + SYSTEMCTL_COMMAND + TPOT_PRIVILEGED + TPOT_SESSION + TPOT_CPU
                 + TPOT_PACKAGE_UNITS + tpot_metering("Command_service", "Command_systemctl", "Command_journalctl"))


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
                 "import fnmatch\nimport functools\nimport hashlib\nimport json\nimport random\nimport time\n")
    replace_once(path, "from typing import TYPE_CHECKING, Any\n",
                 "from pathlib import Path\nfrom typing import TYPE_CHECKING, Any\n")
    replace_once(
        path,
        """commands = {}
""",
        "commands = {}\n" + TPOT_TERMINAL + TPOT_PRIVILEGED + """

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
    # the candidates of the release's index, the archive names, the files dpkg counts, the packages that keep
    # their conffiles and the programs of the installed ones (tpot_apt_db, APT_SESSION_COMMAND)
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    found["meta"] = {str(k): v for k, v in meta.items() if isinstance(v, list) and len(v) == 11}
    archives = data.get("archives") if isinstance(data.get("archives"), dict) else {}
    found["archives"] = {str(k): str(v) for k, v in archives.items()}
    found["files"] = data.get("files") if isinstance(data.get("files"), int) else 30000
    conffiles = data.get("conffiles") if isinstance(data.get("conffiles"), list) else []
    found["conffiles"] = {str(name): "" for name in conffiles}
    essential = data.get("essential") if isinstance(data.get("essential"), list) else []
    found["essential"] = {str(name): "" for name in essential}
    removes = data.get("removes") if isinstance(data.get("removes"), dict) else {}
    found["removes"] = {str(name): [str(path) for path in paths] for name, paths in removes.items()
                        if isinstance(paths, list)}
    units = data.get("unit_files") if isinstance(data.get("unit_files"), dict) else {}
    found["unit_files"] = {str(name): [str(path) for path in paths][:64] for name, paths in units.items()
                           if isinstance(paths, list)}
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
# the operations of apt-get and of apt (apt-get(8), apt(8) of 2.x and 3.0)
TPOT_APT_GET_OPERATIONS = frozenset({"update", "upgrade", "install", "reinstall", "remove", "purge", "source",
                                     "build-dep", "download", "changelog", "dist-upgrade", "full-upgrade",
                                     "dselect-upgrade", "clean", "autoclean", "auto-clean", "check", "markauto",
                                     "unmarkauto", "autoremove", "auto-remove", "autopurge", "satisfy",
                                     "indextargets", "moo", "help"})
TPOT_APT_OPERATIONS = frozenset({"list", "search", "show", "showsrc", "install", "reinstall", "remove", "purge",
                                 "autoremove", "auto-remove", "autopurge", "update", "upgrade", "full-upgrade",
                                 "dist-upgrade", "edit-sources", "satisfy", "policy", "depends", "rdepends",
                                 "source", "build-dep", "download", "changelog", "clean", "autoclean",
                                 "auto-clean", "moo", "help", "why", "why-not", "modernize-sources", "history"})
# the commands that change dpkg's state: root's (the lock of dpkg)
TPOT_APT_ROOT = frozenset({"install", "reinstall", "remove", "purge", "autoremove", "autopurge", "upgrade",
                           "dist-upgrade", "full-upgrade"})


def tpot_apt_glob(word: str, deadline: float | None = None) -> list[str]:
    \"\"\"T-Pot: the names of the release a glob (nginx*, ht?p) matches, at most TPOT_APT_GLOB_MATCHES; the scan
    ends at the deadline (time.monotonic) of the command.\"\"\"
    lists = tpot_apt_lists()
    if lists is None or not re.fullmatch(r"[a-z0-9+.*?\\[\\]-]{1,64}", word) or not set(word) & set("*?["):
        return []
    found, matcher = [], re.compile(fnmatch.translate(word)).match
    for name in lists[1]:
        if matcher(name):
            found.append(name)
            if len(found) == TPOT_APT_GLOB_MATCHES:
                break
        if deadline is not None and time.monotonic() > deadline:
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


# T-Pot: stdout of apt is the terminal of the session
tpot_apt_tty = tpot_terminal


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
            # apt 1.4, 1.8 and 1.9 write no colours, 2.0 does (Debian 9 / 10, Ubuntu 19.10 / 20.04 checked)
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
        if tpot_apt_release() < (2, 2):
            # apt 1.x, 2.0 and 2.1 pad their tree line (1.4 of Debian 9, 1.8 of Debian 10, 1.9 / 2.0 / 2.1 of Ubuntu
            # 19.10 / 20.04 / 20.10); 2.2 of Ubuntu 21.04 writes "... Done"
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
        # T-Pot: a simulation (-s, also in a cluster of short options such as -qs) changes nothing and needs no lock
        self.tpot_simulate = False
        items = list(self.args)
        while items:
            arg = items.pop(0)
            if arg in ("-o", "-c", "-t", "-a", "--option", "--config-file", "--target-release",
                       "--default-release", "--host-architecture"):
                if items:
                    items.pop(0)
            elif arg in ("--simulate", "--dry-run", "--just-print", "--no-act", "--recon") or (
                    re.fullmatch(r"-[A-Za-z]+", arg) and "s" in arg):
                self.tpot_simulate = True
            elif not arg.startswith("-"):
                self.tpot_words.append(arg)
        if len(self.args) == 0:
            self.do_help()
        elif self.args[0] in ("-v", "--version"):
            self.do_version()
        elif self.tpot_words[:1] and self.tpot_words[0] in TPOT_APT_ROOT and tpot_apt("apt_version", "") and (
                tpot_apt_option_error(self.args) is not None):
            # T-Pot: an option apt does not have, before the lock and the packages
            self.errorWrite(tpot_apt_option_text(self, tpot_apt_option_error(self.args)))
            self.exit(100)
        elif self.tpot_words[:1] and self.tpot_words[0] in TPOT_APT_ROOT and not self.tpot_simulate and not tpot_privileged(self):
            # T-Pot: dpkg's lock is root's
            if getattr(self, "tpot_prog", "apt-get") == "apt" and not tpot_apt_tty(self) and tpot_apt("apt_version", ""):
                self.errorWrite(TPOT_APT_WARNING)
            self.errorWrite(tpot_apt_errors(self, tpot_apt_lock()))
            self.exit(100)
        elif self.tpot_words[:1] == ["install"]:
            yield self.do_install()
        elif self.tpot_words[:1] == ["reinstall"] and getattr(self, "tpot_prog", "apt-get") == "apt":
            self.args = [*self.args, "--reinstall"]
            yield self.do_install()
        elif self.tpot_words[:1] in (["remove"], ["purge"]):
            yield self.tpot_remove(self.tpot_words[0] == "purge" or "--purge" in self.args)
        elif self.tpot_words[:1] == ["autoremove"] or self.tpot_words[:1] == ["autopurge"]:
            self.tpot_autoremove()
        elif self.tpot_words[:1] in (["upgrade"], ["dist-upgrade"], ["full-upgrade"]):
            yield self.tpot_upgrade()
        elif self.tpot_words[:1] == ["list"] and getattr(self, "tpot_prog", "apt-get") == "apt":
            self.tpot_list()
        elif self.tpot_words[:1] == ["show"] and getattr(self, "tpot_prog", "apt-get") == "apt":
            self.tpot_show()
        elif self.tpot_words[:1] == ["search"] and getattr(self, "tpot_prog", "apt-get") == "apt":
            self.tpot_search()
        elif self.tpot_words[:1] == ["policy"] and getattr(self, "tpot_prog", "apt-get") == "apt":
            self.exit(tpot_apt_cache_policy(self, self.tpot_words[1:]))
        elif self.tpot_words[:1] == ["update"] and tpot_privileged(self):
            yield self.tpot_update()
        elif self.tpot_words[:1] == ["moo"]:
            self.do_moo()
        elif self.tpot_words[:1] and self.tpot_words[0] not in (
                TPOT_APT_OPERATIONS if getattr(self, "tpot_prog", "apt-get") == "apt" else TPOT_APT_GET_OPERATIONS):
            # T-Pot: an operation the program does not have
            if getattr(self, "tpot_prog", "apt-get") == "apt" and not tpot_apt_tty(self):
                self.errorWrite(TPOT_APT_WARNING)
            self.errorWrite(tpot_apt_errors(self, f"E: Invalid operation {self.tpot_words[0]}\\n"))
            self.exit(100)
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
    # install without packages: the session's summary (tpot_install), not a made-up number of upgradable packages
    replace_once(
        path,
        """        if len(self.args) <= 1:
            self.write(
                f"0 upgraded, 0 newly installed, 0 to remove and {random.randint(200, 300)} not upgraded.\\n"
            )
            self.exit()
            return
""",
        "",
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
        """        # T-Pot: the install of the session's package state (tpot_install, APT_SESSION_COMMAND)
        yield self.tpot_install()""",
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
""" + APT_SESSION_COMMAND + OPKG_COMMAND + TPOT_PACKAGE_HOOK
    + tpot_metering("Command_aptget", "Command_apt", "Command_dpkg", "Command_dpkg_query", "Command_apt_cache",
                    "Command_opkg"),
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
        "commands = {}\n" + TPOT_TERMINAL + TPOT_PRIVILEGED + """

def tpot_yum_plugins() -> str:
    return CowrieConfig.get(
        "shell", "yum_plugins", fallback="changelog, kernel-module, ovl, priorities, tsflags, versionlock"
    )


import functools as tpot_functools  # noqa: E402
import heapq  # noqa: E402
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
    return f"\\x1b[1m{text}\\x1b(B\\x1b[m" if tpot_terminal(cmd) else text


# the installed packages of a CentOS 7 that are not built for x86_64
TPOT_YUM_NOARCH = frozenset({"yum"})
# the commands of yum 3.4.3 and its plugins of CentOS 7 (yum help)
TPOT_YUM_COMMANDS = frozenset({
    "check", "check-update", "clean", "deplist", "distribution-synchronization", "distro-sync", "downgrade",
    "erase", "fs", "fssnapshot", "groups", "group", "grouplist", "groupinstall", "groupupdate", "groupremove",
    "groupinfo", "help", "history", "info", "install", "install-n", "install-na", "install-nevra", "langavailable",
    "langinfo", "langinstall", "langlist", "langremove", "list", "load-transaction", "load-ts", "makecache",
    "provides", "whatprovides", "reinstall", "remove", "remove-n", "remove-na", "remove-nevra", "repo-pkgs",
    "repo-packages", "repository-packages", "repoinfo", "repolist", "search", "shell", "swap", "update",
    "update-minimal", "update-to", "updateinfo", "upgrade", "upgrade-to", "version", "localinstall",
    "localupdate", "erase-n", "erase-na", "erase-nevra", "list-sec", "updateinfo-sec"})


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
            case [word, *_] if word not in TPOT_YUM_COMMANDS:
                # T-Pot: a command yum 3 does not have (centos:7)
                self.write(f"Loaded plugins: {tpot_yum_plugins()}\\n")
                self.errorWrite(f"No such command: {word}. Please use /usr/bin/yum --help\\n")
                self.exit(1)
            case ["install", *_] if not tpot_privileged(self):
                self.do_locked()
            case ["install", *_]:
                yield self.do_install()
            case ["remove" | "erase", *_] if not tpot_privileged(self):
                self.do_locked()
            case ["remove" | "erase", *_]:
                yield self.tpot_remove3()
            case ["list", *_]:
                self.tpot_list3()
            case ["info", *_]:
                self.tpot_info3()
            case ["search", *_]:
                self.tpot_search3()
            case ["repolist", *_]:
                self.tpot_repolist3()
            case ["clean" | "makecache", *_] if tpot_privileged(self):
                self.tpot_cache3()
            case ["provides" | "whatprovides", *_]:
                self.tpot_provides3()
            case ["history", *_]:
                self.tpot_history3()
            case ["update" | "upgrade" | "check-update", *_] if tpot_privileged(self):
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
        """        # T-Pot: names, globs and what packages provide as yum takes them (tpot_yum_find); with nothing left
        # yum has nothing to do, an error only when no name was found at all; else the transaction of the
        # session's package state (tpot_install3, YUM3_COMMAND)
        wanted, self.tpot_notes, found_any = tpot_yum_find(self, self.tpot_words[1:])
        yield self.sleep(1)
        tpot_yum_head(self)
        for note in self.tpot_notes:
            self.write(note)
        if not wanted:
            if found_any:
                self.write("Nothing to do\\n")
                self.exit()
            else:
                self.errorWrite("Error: Nothing to do\\n")
                self.exit(1)
            return
        yield self.tpot_install3(wanted)
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


# module code of the patched apt.py after its commands: the package state of the session, dpkg,
# dpkg-query and apt-cache
APT_SESSION_COMMAND = r'''

# T-Pot: one package state of the session for apt, apt-get, apt-cache, dpkg and dpkg-query. It starts
# from the installed packages of the persona ([shell] apt_packages "installed") and lives on the protocol:
# what the session installs, removes, purges and upgrades, every one of them shows afterwards. The
# candidates, components, sizes and descriptions are those of the release's indexes ("meta"); nothing is
# downloaded or written but the programs of the packages in the session's tree.
# the essential packages when the persona's data names none ([shell] apt_packages "essential")
TPOT_APT_ESSENTIAL = frozenset({"apt", "base-files", "base-passwd", "bash", "bsdutils", "coreutils", "dash",
                                "debianutils", "diffutils", "dpkg", "findutils", "grep", "gzip", "hostname",
                                "init-system-helpers", "libc-bin", "login", "ncurses-bin", "perl-base", "sed",
                                "sysvinit-utils", "tar", "util-linux"})
TPOT_APT_LIST_LIMIT = 2000


def tpot_apt_meta(name: str):
    """T-Pot: [version, component, pocket, installed KiB, bytes, all or -, section, maintainer, priority,
    essential (yes or -), description] of the release's index."""
    return tpot_apt_data()["meta"].get(name)


def tpot_apt_essential(name: str) -> bool:
    """T-Pot: an essential package of the release (Essential: yes of its index)."""
    essential = tpot_apt_data()["essential"]
    return name in essential if essential else name in TPOT_APT_ESSENTIAL


def tpot_apt_simulated(cmd, name: str, version: str, old: str | None = None, remove: bool = False) -> str:
    """T-Pot: the lines of apt-get -s for a package: Inst / Conf with the release that has the version (Debian:12.13/
    oldstable), Remv for a remove."""
    if remove:
        return f"Remv {name} [{version}]\n"
    meta = tpot_apt_meta(name)
    pocket = meta[2] if meta else "-"
    origin = tpot_apt("apt_origin_security", "Debian-Security:12") if pocket == "security" else tpot_apt(
        "apt_origin", "Debian:12")
    release = f"{origin}/{tpot_apt_data()['archives'].get(pocket, 'stable')} [{tpot_apt_arch(name)}]"
    head = f"Inst {name} [{old}] " if old else f"Inst {name} "
    return f"{head}({version} {release})\nConf {name} ({version} {release})\n"


# the name scans (globs, regular expressions, searches) take of the session's time (tpot_scanned, TPOT_BUDGET)
from cowrie.shell.command import tpot_deadline, tpot_scanned  # noqa: E402


# a regular expression of package names: at most this long, with at most so many repeats; the globs and
# regular expressions of one command together take at most this time
TPOT_APT_REGEX_LENGTH = 64
TPOT_APT_REGEX_REPEATS = 3
TPOT_APT_REGEX_SECONDS = 0.5


def tpot_apt_regex_parts(word: str) -> list[tuple] | None:
    """T-Pot: of a regular expression without groups, the texts each of its alternatives needs (a name the
    expression matches holds all texts of one of them); None where an alternative needs no text."""
    needed = []
    for branch in word.split("|"):
        runs, run, i = [], "", 0
        while i < len(branch):
            char = branch[i]
            if char == "[":
                # a set: [abc], [^abc], []abc], [^]abc]
                start = i + 2 if branch[i + 1:i + 2] == "^" else i + 1
                end = branch.find("]", start + 1 if branch[start:start + 1] == "]" else start)
                i = len(branch) if end < 0 else end + 1
                runs.append(run)
                run = ""
            elif char in ".^$?*+":
                runs.append(run)
                run, i = "", i + 1
            elif branch[i + 1:i + 2] in ("?", "*"):
                runs.append(run)
                run, i = "", i + 2
            elif branch[i + 1:i + 2] == "+":
                runs.append(run + char)
                run, i = "", i + 2
            else:
                run, i = run + char, i + 1
        runs.append(run)
        runs = tuple(r for r in runs if r)
        if not runs:
            return None
        needed.append(runs)
    return needed


def tpot_apt_regex(word: str, deadline: float | None = None) -> list[str]:
    """T-Pot: the names of the release a regular expression matches anywhere in them (apt's regex, POSIX ERE without
    groups), at most TPOT_APT_GLOB_MATCHES; bounded in length and repeats, a name is tried only when it holds the
    text the expression needs, the scan ends at the deadline (time.monotonic) of the command."""
    lists = tpot_apt_lists()
    if (lists is None or len(word) > TPOT_APT_REGEX_LENGTH or not re.fullmatch(r"[a-z0-9+.*?^$|\[\]-]+", word)
            or sum(word.count(c) for c in "*+?") > TPOT_APT_REGEX_REPEATS):
        return []
    import warnings
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            pattern = re.compile(word)
    except re.error:
        return []
    needed = tpot_apt_regex_parts(word)
    deadline = time.monotonic() + TPOT_APT_REGEX_SECONDS if deadline is None else deadline
    found = []
    for name in lists[1]:
        if (needed is None or any(all(part in name for part in branch) for branch in needed)) \
                and pattern.search(name):
            found.append(name)
            if len(found) == TPOT_APT_GLOB_MATCHES:
                break
        if time.monotonic() > deadline:
            break
    return found


def _tpot_apt_show(self):
    """T-Pot: apt show of the release's candidates: the fields the persona keeps of the index (no Depends,
    Homepage, Tag), APT-Manual-Installed for an installed one, APT-Sources."""
    if not tpot_apt_tty(self):
        self.errorWrite(TPOT_APT_WARNING)
    names, release = self.tpot_words[1:], tpot_apt_names()
    db = tpot_apt_db(self.protocol)
    ubuntu = tpot_apt("apt_fallback", "debian") == "ubuntu"
    blocks = []
    for name in names:
        meta = tpot_apt_meta(name)
        if meta is None and (release is None or name not in release) and name not in db["packages"]:
            continue
        version = tpot_apt_candidate(name)
        download, installed = tpot_apt_sizes(name)
        section = meta[6] if meta else "misc"
        component = meta[1] if meta else "main"
        url, suite = tpot_apt_source(meta[2] if meta else "-")
        lines = [f"Package: {name}", f"Version: {version}", f"Priority: {meta[8] if meta else 'optional'}",
                 f"Section: {section if component == 'main' or not ubuntu else component + '/' + section}"]
        if ubuntu:
            lines.append("Origin: Ubuntu")
        if meta and meta[7]:
            lines.append(f"Maintainer: {meta[7]}")
        if ubuntu:
            lines.append("Bugs: https://bugs.launchpad.net/ubuntu/+filebug")
        lines += [f"Installed-Size: {tpot_apt_bytes(installed)}", f"Download-Size: {tpot_apt_bytes(download)}"]
        if (db["packages"].get(name) or {}).get("status") == "ii":
            lines.append("APT-Manual-Installed: yes")
        lines += [f"APT-Sources: {url} {suite}/{component} {tpot_apt('deb_arch', 'amd64')} Packages",
                  f"Description: {meta[-1] if meta else name}"]
        blocks.append("\n".join(lines) + "\n\n")
    if not blocks:
        if tpot_apt_tty(self):
            notice = "N: " if not tpot_apt_three() else "Notice: "
            self.write("".join(f"{notice}Unable to locate package {name}\n" * 2 for name in names))
        self.errorWrite(tpot_apt_errors(self, "E: No packages found\n"))
        self.exit(100)
        return
    self.write("".join(blocks))
    self.exit()


# the options apt-get and apt understand for install, remove, upgrade and the like (apt 2.6 of debian:12, the
# release's changes of debian:9, ubuntu:22.04, ubuntu:24.04, debian:13; refs/packages-a4.json)
TPOT_APT_FLAGS = frozenset({
    "allow-change-held-packages", "allow-downgrades", "allow-remove-essential", "allow-unauthenticated", "assume-no",
    "assume-yes", "auto-remove", "autoremove", "download", "download-only", "dry-run", "fix-broken", "fix-missing",
    "fix-policy", "force-yes", "help", "ignore-hold", "ignore-missing", "install-recommends", "install-suggests",
    "just-print", "mark-auto", "no-act", "only-source", "only-upgrade", "print-uris", "purge", "quiet", "recon",
    "reinstall", "remove", "show-progress", "show-upgraded", "silent", "simulate", "trivial-only", "upgrade",
    "verbose-versions", "version", "yes"})
TPOT_APT_VALUED = frozenset({"config-file", "default-release", "option", "planner", "target-release", "with-source",
                             "solver"})
# (flags added, flags gone, valued options added, short options, short options with a value) per release
TPOT_APT_OPTION_RELEASES = {
    (1, 4): ({"allow-insecure-repositories", "allow-weak-repositories"}, {"mark-auto"}, set(), "dfhmqsuvyV", "cot"),
    (2, 4): (set(), set(), {"snapshot"}, "dfhmqsuvyV", "cotS"),
    (2, 8): ({"update"}, set(), {"snapshot"}, "dfhmqsuvyVU", "cotS"),
    (3, 0): ({"audit", "color", "list-columns", "strict-pinning", "update"}, set(), {"comment", "snapshot"},
             "dfhmqsuvyVU", "cotS"),
}
# the words of apt's StringToBool: --no-foo, --with-foo and the like
TPOT_APT_SENSES = frozenset({"no", "false", "without", "off", "disable", "yes", "true", "with", "on", "enable"})


def tpot_apt_option_error(args: list) -> str | None:
    """T-Pot: apt's error for the first option of the command line it does not understand (CommandLine::Parse of
    apt: long options in any case, --<sense>-<flag>, short options in clusters), None where all are understood."""
    extra = TPOT_APT_OPTION_RELEASES.get(tpot_apt_release(), (set(), set(), set(), "dfhmqsuvyV", "cot"))
    flags = (TPOT_APT_FLAGS | extra[0]) - extra[1]
    valued = TPOT_APT_VALUED | extra[2]
    short, short_valued = extra[3], extra[4]
    items = list(args)
    while items:
        arg = items.pop(0)
        if arg == "--":
            break
        if arg.startswith("--"):
            name, eq, _ = arg[2:].partition("=")
            name = name.lower()
            if name in valued:
                if not eq and not items:
                    return f"Option {arg} requires an argument."
                if not eq:
                    items.pop(0)
                continue
            if name in flags:
                continue
            sense, dash, rest = name.partition("-")
            if not dash or (rest not in flags and rest not in valued and not (len(rest) == 1 and rest in short)):
                return f"Command line option {arg} is not understood in combination with the other options"
            if rest in valued or rest in short_valued:
                return f"Command line option {arg} is not boolean"
            if sense not in TPOT_APT_SENSES:
                return f"Sense {sense} is not understood, try true or false."
        elif arg.startswith("-") and len(arg) > 1:
            for number, letter in enumerate(arg[1:], 1):
                if letter in short_valued:
                    if number == len(arg) - 1 and items:
                        items.pop(0)
                    break
                if letter not in short:
                    return (f"Command line option '{letter}' [from {arg}] is not understood in combination with the "
                            "other options.")
                if arg[number + 1:number + 2] == "=":
                    # -q=2: the rest of the word is the value of the option
                    break
    return None


def tpot_apt_option_text(cmd, text: str) -> str:
    """T-Pot: an error of apt's command line: apt 2.0 and later in colour also on a pipe (before it looks at the
    terminal), apt-get and apt 1.x as they are (apt 1.9 of Ubuntu 19.10 writes no colours)."""
    if getattr(cmd, "tpot_prog", "apt-get") != "apt" or tpot_apt_release() < (2, 0):
        return f"E: {text}\n"
    if tpot_apt_three():
        return f"\x1b[1;31mError: \x1b[0m\x1b[1m{text}\x1b[0m\n"
    return f"\x1b[1;31mE: \x1b[0m{text}\x1b[0m\n"


def tpot_apt_simulation_note(cmd) -> str:
    """T-Pot: what apt-get -s says first for a user who is not root (unverified: the text of apt 2.x)."""
    if tpot_privileged(cmd):
        return ""
    return ("NOTE: This is only a simulation!\n      apt-get needs root privileges for real execution.\n"
            "      Keep also in mind that locking is deactivated,\n"
            "      so don't depend on the relevance to the real current situation!\n")


def tpot_deb_part(a: str, b: str) -> int:
    """T-Pot: dpkg's verrevcmp: digits as numbers, ~ before everything, letters before the other characters."""
    def order(char: str) -> int:
        if char.isdigit():
            return 0
        if char == "~":
            return -1
        if char.isalpha():
            return ord(char)
        return ord(char) + 256 if char else 0

    i = j = 0
    while i < len(a) or j < len(b):
        while (i < len(a) and not a[i].isdigit()) or (j < len(b) and not b[j].isdigit()):
            ac = order(a[i]) if i < len(a) else 0
            bc = order(b[j]) if j < len(b) else 0
            if ac != bc:
                return -1 if ac < bc else 1
            i, j = i + 1, j + 1
        start_a, start_b = i, j
        while i < len(a) and a[i].isdigit():
            i += 1
        while j < len(b) and b[j].isdigit():
            j += 1
        da, db = int(a[start_a:i] or 0), int(b[start_b:j] or 0)
        if da != db:
            return -1 if da < db else 1
    return 0


def tpot_deb_compare(a: str, b: str) -> int:
    """T-Pot: -1, 0 or 1 as dpkg --compare-versions orders two versions."""
    def split(version: str):
        epoch, _, rest = version.partition(":") if ":" in version else ("0", "", version)
        upstream, _, revision = rest.rpartition("-") if "-" in rest else (rest, "", "")
        return int(epoch) if epoch.isdigit() else 0, upstream, revision

    (ea, ua, ra), (eb, ub, rb) = split(a), split(b)
    if ea != eb:
        return -1 if ea < eb else 1
    return tpot_deb_part(ua, ub) or tpot_deb_part(ra, rb)


def tpot_apt_db(protocol) -> dict:
    """T-Pot: the package state of the session: {"packages": {name: {"version", "status" ii / rc, "files",
    "programs"}}, "files": files and directories dpkg counts}."""
    db = getattr(protocol, "tpot_apt_db", None)
    if db is None:
        db = {"packages": {name: {"version": version, "status": "ii"} for name, version in tpot_apt_known().items()},
              "files": tpot_apt_data()["files"]}
        try:
            protocol.tpot_apt_db = db
        except AttributeError:
            pass
    return db


def tpot_apt_installed(protocol) -> dict:
    """T-Pot: {name: version} of the packages the session has installed (dpkg ii)."""
    return {name: entry["version"] for name, entry in tpot_apt_db(protocol)["packages"].items()
            if entry["status"] == "ii"}


def tpot_apt_candidate(name: str) -> str:
    """T-Pot: the version apt installs: the release's candidate, else the persona's (tpot_apt_version)."""
    meta = tpot_apt_meta(name)
    return meta[0] if meta else tpot_apt_version(name)


def tpot_apt_upgradable(protocol) -> dict:
    """T-Pot: {name: (installed, candidate)} of the installed packages the release has a newer version of."""
    found = {}
    for name, version in sorted(tpot_apt_installed(protocol).items()):
        meta = tpot_apt_meta(name)
        if meta and tpot_deb_compare(meta[0], version) > 0:
            found[name] = (version, meta[0])
    return found


def tpot_apt_source(pocket: str) -> tuple[str, str]:
    """T-Pot: (url, suite) of the persona's source of a pocket of the index (-, updates, security, backports,
    rpi: the Raspberry Pi archive)."""
    sources = tpot_apt_sources() or [("http://deb.debian.org/debian", "stable")]
    if pocket == "rpi":
        return next((s for s in sources if "raspberrypi" in s[0]), sources[0])
    codename = sources[0][1].split("-", 1)[0]
    want = codename if pocket in ("-", "") else f"{codename}-{pocket}"
    return next((s for s in sources if s[1] == want and "raspberrypi" not in s[0]), sources[0])


def tpot_apt_arch(name: str) -> str:
    meta = tpot_apt_meta(name)
    return "all" if meta and meta[5] == "all" else tpot_apt("deb_arch", "amd64")


def tpot_apt_qualified(name: str) -> str:
    """T-Pot: the name dpkg prints for a package: libraries of an architecture with it (libssl3:amd64;
    unverified: dpkg names every Multi-Arch: same package so, the index keeps no Multi-Arch field)."""
    arch = tpot_apt_arch(name)
    plain = not name.startswith("lib") or arch == "all" or name.endswith(("-dev", "-bin", "-common", "-perl", "-data"))
    return name if plain else f"{name}:{arch}"


def tpot_apt_sizes(name: str) -> tuple[int, int]:
    """T-Pot: (download bytes, installed bytes) of a package."""
    meta = tpot_apt_meta(name)
    if meta and meta[4]:
        return meta[4], meta[3] * 1024
    size = tpot_apt_size(name)
    return size * 1000, int(size * 2.2) * 1024


def tpot_apt_window(cmd) -> int:
    """T-Pot: the columns apt lays its lists out in: the terminal's, 80 elsewhere."""
    if not tpot_apt_tty(cmd):
        return 80
    size = getattr(getattr(cmd.protocol, "user", None), "windowSize", None)
    try:
        return max(20, int(size[1])) if size else 80
    except (TypeError, ValueError, IndexError):
        return 80


def tpot_apt_wrap(names: list, width: int) -> str:
    """T-Pot: apt-get's list of names: two spaces, then the names that fit the width on a line."""
    lines, line = [], " "
    for name in names:
        if len(line) + 1 + len(name) >= width and line.strip():
            lines.append(line)
            line = " "
        line += " " + name
    lines.append(line)
    return "".join(f"{line}\n" for line in lines)


def tpot_apt_columns(names: list, width: int) -> list[str]:
    """T-Pot: apt 3.0's list of names: in columns from top to bottom, as many as fit the width."""
    names = sorted(names)
    for rows in range(1, len(names) + 1):
        cols = -(-len(names) // rows)
        columns = [names[c * rows:(c + 1) * rows] for c in range(cols)]
        widths = [max(len(n) for n in column) for column in columns]
        if rows == len(names) or 2 + sum(widths) + 2 * (len(widths) - 1) <= width:
            break
    lines = []
    for r in range(rows):
        cells = [column[r] for column in columns if r < len(column)]
        lines.append("  " + "  ".join(cell.ljust(widths[c]) for c, cell in enumerate(cells)).rstrip())
    return lines


def tpot_apt_block(cmd, title: str, names: list, colour: str) -> str:
    """T-Pot: a list of packages as the program writes it: apt 3.0 in columns, coloured on a terminal."""
    if getattr(cmd, "tpot_prog", "apt-get") == "apt" and tpot_apt_three():
        lines = "".join(f"{line}\n" for line in tpot_apt_columns(names, tpot_apt_window(cmd)))
        return f"{title}:\n" + (f"\x1b[{colour}m{lines}\x1b[0m\n" if tpot_apt_tty(cmd) else f"{lines}\n")
    return f"The following {title}:\n" + tpot_apt_wrap(names, tpot_apt_window(cmd))


def tpot_apt_summary(cmd, upgraded: int, new: int, removed: int, held: int) -> str:
    if getattr(cmd, "tpot_prog", "apt-get") == "apt" and tpot_apt_three():
        return (f"Summary:\n  Upgrading: {upgraded}, Installing: {new}, Removing: {removed}, "
                f"Not Upgrading: {held}\n")
    return f"{upgraded} upgraded, {new} newly installed, {removed} to remove and {held} not upgraded.\n"


def tpot_dpkg_newline(cmd) -> str:
    """T-Pot: apt runs dpkg on a pty of its own: its lines end in CR LF also on a pipe."""
    return "\n" if tpot_apt_tty(cmd) else "\r\n"


def tpot_dpkg_database(cmd, files: int) -> str:
    """T-Pot: dpkg reading its database: the progress in steps of 5 %, then the count of files."""
    steps = "".join(f"(Reading database ... {step}%\r" for step in range(5, 101, 5))
    return (f"(Reading database ... \r{steps}(Reading database ... {files} files and directories currently "
            f"installed.){tpot_dpkg_newline(cmd)}")


def tpot_dpkg_files(name: str, programs: list) -> list[str]:
    """T-Pot: the files of a package (dpkg -L): its programs and unit files, its documents and its manual pages."""
    found = ["/.", "/usr"]
    commands = list(programs)
    # the unit files are files of the package, not programs: no manual page of their own
    programs = commands + [path for path in tpot_apt_data().get("unit_files", {}).get(name, [])
                           if path not in commands]
    dirs = sorted({program.rsplit("/", 1)[0] for program in programs})
    for directory in dirs:
        found.append(directory)
        found += sorted(program for program in programs if program.rsplit("/", 1)[0] == directory)
    found += ["/usr/share", "/usr/share/doc", f"/usr/share/doc/{name}", f"/usr/share/doc/{name}/changelog.Debian.gz",
              f"/usr/share/doc/{name}/copyright"]
    if commands:
        found += ["/usr/share/man", "/usr/share/man/man1"] + [
            f"/usr/share/man/man1/{program.rsplit('/', 1)[-1]}.1.gz" for program in commands]
    return list(dict.fromkeys(found))


def tpot_apt_programs(name: str) -> list[str]:
    """T-Pot: the programs a package puts on PATH: those of the persona's data, else /usr/bin/<name>."""
    programs = tpot_apt_data()["programs"]
    # a recorded package without programs (nginx-common: its unit only) puts none on PATH
    return list(programs[name]) if name in programs else [f"/usr/bin/{name}"]


def tpot_apt_fetched(total: int, name: str) -> str:
    speed = 150_000 + int.from_bytes(tpot_apt_digest(name)[9:12], "big") % 3_000_000
    seconds = int(total / speed)
    return f"Fetched {tpot_apt_bytes(total)} in {seconds}s ({tpot_apt_bytes(total / max(seconds, 1))}/s)\n"


def tpot_apt_words(cmd, prog: str, words: list):
    """T-Pot: the packages of the words (name, name:arch, name=version, name/release, globs, virtual names):
    ({name: wanted version or None}, errors, notes) as apt resolves them on the release."""
    # the globs and regular expressions take of the session's time
    return tpot_apt_resolve(cmd, prog, words, tpot_deadline(cmd.protocol, TPOT_APT_REGEX_SECONDS))


def tpot_apt_resolve(cmd, prog: str, words: list, deadline: float):
    """T-Pot: tpot_apt_words within the deadline of its scans."""
    release = tpot_apt_names()
    virtuals = (tpot_apt_lists() or (None, None, {}))[2]
    names, errors, notes = {}, [], []
    globs = 0
    archives = set(tpot_apt_data()["archives"].values()) | {suite for _, suite in tpot_apt_sources()}
    for word in words:
        spec = re.fullmatch(r"([a-z0-9][a-z0-9+.-]+)(?::[a-z0-9-]+)?(?:([=/])([A-Za-z0-9.+~:_-]+))?", word)
        name = spec.group(1) if spec else ""
        matches = []
        if "/" not in word and "=" not in word and set(word) & set("*?[") and globs < TPOT_APT_GLOBS:
            globs += 1
            matches = tpot_apt_glob(word, deadline)
        if (not matches and "/" not in word and "=" not in word and set(word) & set(TPOT_APT_REGEX)
                and not (spec and (release is None or name in release)) and globs < TPOT_APT_GLOBS):
            # a name that is no package: apt-get and apt try it as a regular expression of the names
            globs += 1
            matches = tpot_apt_regex(word, deadline)
        if spec and spec.group(2) and (release is None or name in release):
            # a version or a release of the package: apt takes the candidate only
            kind, value = spec.group(2), spec.group(3)
            if (kind == "=" and value != tpot_apt_candidate(name)) or (kind == "/" and value not in archives):
                if tpot_apt_release() >= (2, 1):
                    # apt 2.4 and later name the package first (apt 1.8 and 2.0 do not)
                    notes.append(f"Package {name} is not available, but is referred to by another package.\n"
                                 "This may mean that the package is missing, has been obsoleted, or\n"
                                 "is only available from another source\n\n")
                errors.append(f"E: {'Version' if kind == '=' else 'Release'} '{value}' for '{name}' was not found\n")
            else:
                names[name] = None
        elif "/" in word or word.endswith(".deb") or word in (".", ".."):
            errors.append(f"E: Unsupported file {word} given on commandline\n")
        elif matches:
            names.update(dict.fromkeys(matches))
        elif name and name in tpot_apt_data()["unavailable"]:
            notes.append(f"Package {name} is not available, but is referred to by another package.\n"
                         "This may mean that the package is missing, has been obsoleted, or\n"
                         "is only available from another source\n\n")
            errors.append(f"E: Package '{name}' has no installation candidate\n")
        elif spec and (release is None or name in release):
            names[name] = None
        elif name in virtuals and len(virtuals[name]) > 1:
            providers = "".join(f"  {p} {tpot_apt_candidate(p)}\n" for p in virtuals[name])
            notes.append(f"Package {name} is a virtual package provided by:\n{providers}"
                         "You should explicitly select one to install.\n\n")
            errors.append(f"E: Package '{name}' has no installation candidate\n")
        elif name in virtuals:
            names[virtuals[name][0]] = None
        else:
            errors.append(tpot_apt_missing(word, prog))
    return names, errors, notes


def _tpot_apt_install(self):
    """T-Pot: apt install of the session: the candidates of the release, already installed packages
    are the newest version, the new ones go into the session's state and its tree."""
    prog = getattr(self, "tpot_prog", "apt-get")
    names, errors, notes = tpot_apt_words(self, prog, self.tpot_words[1:])
    db = tpot_apt_db(self.protocol)
    installed = tpot_apt_installed(self.protocol)
    reinstall = "--reinstall" in self.args
    extra = []
    for y in names:
        # the packages a service package brings along go first (nginx-common before nginx): those not installed, for
        #   a package apt installs (an installed one has its dependencies; a reinstall takes only the package)
        for dep in tpot_package_with(y) if y not in installed else ():
            if dep not in installed and dep not in names and dep not in self.packages:
                self.packages[dep] = {"version": tpot_apt_candidate(dep)}
                extra.append(dep)
        if y not in installed or reinstall:
            self.packages[y] = {"version": installed.get(y) or tpot_apt_candidate(y)}
    three, tty = prog == "apt" and tpot_apt_three(), tpot_apt_tty(self)
    pace = min(1.0, TPOT_APT_PACE / max(1, len(self.packages)))
    held = len({n: v for n, v in tpot_apt_upgradable(self.protocol).items()})

    if prog == "apt" and not tty and tpot_apt("apt_version", ""):
        self.errorWrite(TPOT_APT_WARNING)
    if self.tpot_simulate:
        self.write(tpot_apt_simulation_note(self))
    self.write(tpot_apt_head(self))
    if errors:
        for note in notes:
            self.write(note)
        self.errorWrite(tpot_apt_errors(self, "".join(errors)))
        self.exit(100)
        return
    for y in names:
        if y in installed and not reinstall:
            self.write(f"{y} is already the newest version ({installed[y]}).\n")
    if not self.packages:
        self.write(tpot_apt_summary(self, 0, 0, 0, held))
        self.exit()
        return
    sizes = {p: tpot_apt_sizes(p) for p in self.packages}
    download, space = sum(s[0] for s in sizes.values()), sum(s[1] for s in sizes.values())
    fresh = [p for p in self.packages if p not in installed]
    if three:
        self.write(tpot_apt_block(self, "Installing" if fresh else "Reinstalling", list(self.packages), "32"))
        self.write(tpot_apt_summary(self, 0, len(fresh), 0, held).rstrip("\n")
                   + (f", Reinstalling: {len(self.packages) - len(fresh)}\n" if len(fresh) < len(self.packages)
                      else "\n")
                   + ("" if self.tpot_simulate else f"  Download size: {tpot_apt_bytes(download)}\n"
                      f"  Space needed: {tpot_apt_bytes(space)} / {tpot_apt_free()} available\n\n"))
    else:
        # apt names the packages of a list in their order by name (ref debian-12-t12-install-nginx)
        if extra:
            self.write(tpot_apt_block(self, "additional packages will be installed", sorted(extra), ""))
        if fresh:
            self.write(tpot_apt_block(self, "NEW packages will be installed", sorted(fresh), ""))
        again = len(self.packages) - len(fresh)
        self.write(f"0 upgraded, {len(fresh)} newly installed, {again} reinstalled, 0 to remove and {held} not "
                   "upgraded.\n" if again else tpot_apt_summary(self, 0, len(fresh), 0, held))
        if not self.tpot_simulate:
            self.write(f"Need to get {tpot_apt_bytes(download)} of archives.\n")
            if fresh:
                self.write(f"After this operation, {tpot_apt_bytes(space)} of additional disk space will be used.\n")
    if self.tpot_simulate:
        self.write("".join(tpot_apt_simulated(self, p, self.packages[p]["version"]) for p in self.packages))
        self.exit()
        return
    for i, p in enumerate(self.packages, 1):
        meta = tpot_apt_meta(p)
        url, suite = tpot_apt_source(meta[2] if meta else "-")
        arch = tpot_apt_arch(p)
        self.write(f"Get:{i} {url} {suite}/{meta[1] if meta else 'main'} {tpot_apt('deb_arch', 'amd64')} {p} {arch} "
                   f"{self.packages[p]['version']} [{tpot_apt_bytes(sizes[p][0])}]\n")
        yield self.sleep(pace, 2 * pace)
    self.write(tpot_apt_fetched(download, next(iter(self.packages))))
    yield self.sleep(1, 2)
    nl = tpot_dpkg_newline(self)
    many = len(self.packages) > 1
    for number, p in enumerate(self.packages):
        version = self.packages[p]["version"]
        if p not in installed:
            self.write(f"Selecting previously unselected package {tpot_apt_qualified(p)}.{nl}")
        if number == 0:
            self.write(tpot_dpkg_database(self, db["files"]))
        prefix = f"{number:02d}-" if many else ""
        self.write(f"Preparing to unpack .../{prefix}{p}_{version.replace(':', '%3a')}_{tpot_apt_arch(p)}.deb ...{nl}")
        over = f" over ({version})" if p in installed else ""
        self.write(f"Unpacking {tpot_apt_qualified(p)} ({version}){over} ...{nl}")
        yield self.sleep(pace, 2 * pace)
    for p in self.packages:
        self.write(f"Setting up {tpot_apt_qualified(p)} ({self.packages[p]['version']}) ...{nl}")
        programs = tpot_apt_place(self, p, self.packages[p]["version"])
        files = tpot_dpkg_files(p, programs)
        if p not in installed:
            db["files"] += len([f for f in files if f.startswith(("/usr/share/doc/", "/usr/share/man/man1/"))
                                or f in programs])
        kept = (db["packages"].get(p) or {}).get("status") == "rc"
        db["packages"][p] = {"version": self.packages[p]["version"], "status": "ii", "programs": programs}
        # its postinst: the units it ships enabled and started (tpot_package_units); over its kept conffiles (rc)
        #   or an installed package (a reinstall) without the config and keys a fresh install makes
        self.write(tpot_package_units(self, p, "reinstall" if kept or p in installed else "install", nl))
        yield self.sleep(2 * pace)
    self.write(f"Processing triggers for man-db ({tpot_apt_installed(self.protocol).get('man-db') or tpot_apt_version('man-db')}) ...{nl}")
    self.exit()


def tpot_apt_place(cmd, p: str, version: str) -> list[str]:
    """T-Pot: the programs of a package the session installed, in its tree and as its commands: those of
    the persona's data that answer as installed ([shell] apt_packages "answers") with Cowrie's command
    of their name; another one is a faked program, also where Cowrie's command of that name stays
    skipped; a package without data leaves a program of the persona under its name as it is."""
    known = tpot_apt_data()["programs"]
    modelled = known[p] if p in known else tpot_apt_data()["removes"].get(p)
    hidden = getattr(cmd.protocol, "tpot_apt_hidden", None) or {}
    placed = []
    for program in modelled if modelled is not None else [f"/usr/bin/{p}"]:
        name = program.rsplit("/", 1)[-1]
        placed.append(program)
        if program in hidden:
            # a program of the persona the session removed: its own command again
            try:
                cmd.fs.mkfile(program, 0, 0, random.randint(10000, 90000), 33261)
            except Exception:  # noqa: BLE001 - a program the tree takes no more files for stays absent
                continue
            for spelling, command in hidden.pop(program).items():
                cmd.protocol.commands[spelling] = command
            continue
        if not modelled and cmd.fs.exists(program):
            continue
        try:
            made = cmd.fs.mkfile(program, 0, 0, random.randint(10000, 90000), 33261)
        except Exception:  # noqa: BLE001 - a program the tree takes no more files for stays absent
            made = False
        if made is False:
            continue
        faked = Command_faked_package_class_factory.getCommand(name)
        if modelled and name in tpot_apt_data()["answers"]:
            tpot_mark_installed(cmd.protocol, name, version)
            if name not in cmd.protocol.commands:
                cmd.protocol.commands[program] = faked
        elif name in cmd.protocol.commands or program in cmd.protocol.commands:
            session = getattr(cmd.protocol, "tpot_faked", None)
            if session is None:
                session = {}
                cmd.protocol.tpot_faked = session
            session[program] = faked
        else:
            cmd.protocol.commands[program] = faked
    return placed


def tpot_apt_unplace(cmd, p: str, entry: dict) -> None:
    """T-Pot: the programs of a removed package leave the session's tree and its commands."""
    for program in entry.get("programs") or tpot_apt_data()["removes"].get(p, []):
        name = program.rsplit("/", 1)[-1]
        try:
            if cmd.fs.exists(program):
                cmd.fs.remove(program)
        except Exception:  # noqa: BLE001 - a program the session cannot remove stays
            continue
        # Cowrie's own commands of the program are kept aside: a new install brings them back
        own = {}
        for spelling in (program, name, f"/bin/{name}", f"/usr/bin/{name}", f"/usr/sbin/{name}", f"/sbin/{name}"):
            command = cmd.protocol.commands.pop(spelling, None)
            if command is not None and "faked" not in getattr(command, "__name__", "faked"):
                own[spelling] = command
            (getattr(cmd.protocol, "tpot_faked", None) or {}).pop(spelling, None)
        if own:
            hidden = getattr(cmd.protocol, "tpot_apt_hidden", None)
            if hidden is None:
                hidden = {}
                cmd.protocol.tpot_apt_hidden = hidden
            hidden[program] = own
        (getattr(cmd.protocol, "tpot_installed", None) or {}).pop(name, None)


def _tpot_apt_remove(self, purge: bool = False):
    """T-Pot: apt remove / purge / autoremove of the session."""
    prog = getattr(self, "tpot_prog", "apt-get")
    db = tpot_apt_db(self.protocol)
    names, errors, notes = tpot_apt_words(self, prog, self.tpot_words[1:])
    held = len(tpot_apt_upgradable(self.protocol))
    three, tty = prog == "apt" and tpot_apt_three(), tpot_apt_tty(self)
    if prog == "apt" and not tty and tpot_apt("apt_version", ""):
        self.errorWrite(TPOT_APT_WARNING)
    if self.tpot_simulate:
        self.write(tpot_apt_simulation_note(self))
    self.write(tpot_apt_head(self))
    if errors:
        for note in notes:
            self.write(note)
        self.errorWrite(tpot_apt_errors(self, "".join(errors)))
        self.exit(100)
        return
    gone = {}
    for name in names:
        entry = db["packages"].get(name)
        if entry is None or (entry["status"] == "rc" and not purge):
            self.write(f"Package '{name}' is not installed, so not removed\n")
        else:
            gone[name] = entry
    if not gone:
        self.write(tpot_apt_summary(self, 0, 0, 0, held))
        self.exit()
        return
    freed = sum(tpot_apt_sizes(n)[1] for n, e in gone.items() if e["status"] == "ii")
    listed = [f"{n}*" if purge else n for n in gone]
    essential = [n for n in gone if tpot_apt_essential(n)]
    if essential:
        # unverified: the text of apt 2.x for an essential package with -y
        self.write("WARNING: The following essential packages will be removed.\n"
                   "This should NOT be done unless you know exactly what you are doing!\n"
                   + tpot_apt_wrap(essential, tpot_apt_window(self)))
        self.write(tpot_apt_summary(self, 0, 0, len(gone), held))
        self.write(f"After this operation, {tpot_apt_bytes(freed)} disk space will be freed.\n")
        self.errorWrite(tpot_apt_errors(self, "E: Essential packages were removed and -y was used without "
                                              "--allow-remove-essential.\n"))
        self.exit(100)
        return
    if three:
        self.write(tpot_apt_block(self, "REMOVING", listed, "31"))
        self.write(tpot_apt_summary(self, 0, 0, len(gone), held))
        self.write(f"  Freed space: {tpot_apt_bytes(freed)}\n\n" if freed else "\n")
    else:
        self.write(tpot_apt_block(self, "packages will be REMOVED", listed, ""))
        self.write(tpot_apt_summary(self, 0, 0, len(gone), held))
        if freed and not self.tpot_simulate:
            self.write(f"After this operation, {tpot_apt_bytes(freed)} disk space will be freed.\n")
    if self.tpot_simulate:
        self.write("".join(tpot_apt_simulated(self, n, e["version"], remove=True) for n, e in gone.items()))
        self.exit()
        return
    yield self.sleep(0.5, 1)
    nl = tpot_dpkg_newline(self)
    self.write(tpot_dpkg_database(self, db["files"]))
    for name, entry in gone.items():
        if entry["status"] == "ii":
            self.write(f"Removing {tpot_apt_qualified(name)} ({entry['version']}) ...{nl}")
            tpot_apt_unplace(self, name, entry)
            db["files"] -= len(entry.get("programs") or []) * 2 + 3
            # its prerm and postrm: the units stop, their files go (tpot_package_units)
            self.write(tpot_package_units(self, name, "remove", nl))
        yield self.sleep(0.2, 0.5)
    for name, entry in gone.items():
        conffiles = name in tpot_apt_data()["conffiles"]
        if purge and conffiles:
            self.write(f"Purging configuration files for {tpot_apt_qualified(name)} ({entry['version']}) ...{nl}")
        if purge:
            self.write(tpot_package_units(self, name, "purge", nl))
        if conffiles and not purge:
            db["packages"][name] = dict(entry, status="rc")
        else:
            db["packages"].pop(name, None)
    if any(e["status"] == "ii" for e in gone.values()):
        man = tpot_apt_installed(self.protocol).get("man-db")
        if man:
            self.write(f"Processing triggers for man-db ({man}) ...{nl}")
    self.exit()


def _tpot_apt_autoremove(self):
    """T-Pot: apt autoremove: the session installs no dependencies, nothing is left to remove."""
    prog = getattr(self, "tpot_prog", "apt-get")
    if prog == "apt" and not tpot_apt_tty(self) and tpot_apt("apt_version", ""):
        self.errorWrite(TPOT_APT_WARNING)
    self.write(tpot_apt_head(self))
    self.write(tpot_apt_summary(self, 0, 0, 0, len(tpot_apt_upgradable(self.protocol))))
    self.exit()


def _tpot_apt_upgrade(self):
    """T-Pot: apt upgrade / dist-upgrade / full-upgrade of the session: the installed packages the release
    has a newer version of."""
    prog = getattr(self, "tpot_prog", "apt-get")
    db = tpot_apt_db(self.protocol)
    tty = tpot_apt_tty(self)
    three = prog == "apt" and tpot_apt_three()
    if prog == "apt" and not tty and tpot_apt("apt_version", ""):
        self.errorWrite(TPOT_APT_WARNING)
    if self.tpot_simulate:
        self.write(tpot_apt_simulation_note(self))
    self.write(tpot_apt_head(self))
    if not (three and tty):
        self.write("Calculating upgrade... Done\n" if tty else "Calculating upgrade...\n")
    todo = tpot_apt_upgradable(self.protocol)
    if not todo:
        self.write(tpot_apt_summary(self, 0, 0, 0, 0))
        self.exit()
        return
    sizes = {p: tpot_apt_sizes(p) for p in todo}
    download = sum(s[0] for s in sizes.values())
    if three:
        self.write(tpot_apt_block(self, "Upgrading", list(todo), "32"))
        self.write(tpot_apt_summary(self, len(todo), 0, 0, 0) + ("" if self.tpot_simulate else
                   f"  Download size: {tpot_apt_bytes(download)}\n"
                   f"  Space needed: 0 B / {tpot_apt_free()} available\n\n"))
    else:
        self.write(tpot_apt_block(self, "packages will be upgraded", list(todo), ""))
        self.write(tpot_apt_summary(self, len(todo), 0, 0, 0))
        if not self.tpot_simulate:
            self.write(f"Need to get {tpot_apt_bytes(download)} of archives.\n"
                       "After this operation, 0 B of additional disk space will be used.\n")
    if self.tpot_simulate:
        self.write("".join(tpot_apt_simulated(self, p, new, old) for p, (old, new) in todo.items()))
        self.exit()
        return
    pace = min(1.0, TPOT_APT_PACE / max(1, len(todo)))
    for i, p in enumerate(todo, 1):
        meta = tpot_apt_meta(p)
        url, suite = tpot_apt_source(meta[2])
        self.write(f"Get:{i} {url} {suite}/{meta[1]} {tpot_apt('deb_arch', 'amd64')} {p} {tpot_apt_arch(p)} "
                   f"{todo[p][1]} [{tpot_apt_bytes(sizes[p][0])}]\n")
        yield self.sleep(pace, 2 * pace)
    self.write(tpot_apt_fetched(download, next(iter(todo))))
    nl = tpot_dpkg_newline(self)
    for p, (old, new) in todo.items():
        self.write(tpot_dpkg_database(self, db["files"]))
        self.write(f"Preparing to unpack .../{p}_{new.replace(':', '%3a')}_{tpot_apt_arch(p)}.deb ...{nl}"
                   f"Unpacking {tpot_apt_qualified(p)} ({new}) over ({old}) ...{nl}"
                   f"Setting up {tpot_apt_qualified(p)} ({new}) ...{nl}")
        db["packages"][p] = dict(db["packages"][p], version=new)
        yield self.sleep(pace, 2 * pace)
    self.exit()


def tpot_apt_list_row(name: str, db: dict, archives: dict, upgradable: dict, coloured: str | None = None) -> str:
    """T-Pot: the row of a package in apt list and apt search (the name in colour on a terminal)."""
    entry = db["packages"].get(name)
    meta = tpot_apt_meta(name)
    candidate = meta[0] if meta else (entry["version"] if entry else tpot_apt_candidate(name))
    archive = archives.get(meta[2] if meta else "-", "")
    arch = tpot_apt_arch(name)
    shown = coloured or name
    if entry and entry["status"] == "ii" and name in upgradable:
        return f"{shown}/{archive} {candidate} {arch} [upgradable from: {entry['version']}]"
    if entry and entry["status"] == "ii" and candidate == entry["version"] and archive:
        return f"{shown}/{archive},now {candidate} {arch} [installed]"
    if entry and entry["status"] == "ii":
        return f"{shown}/now {entry['version']} {arch} [installed,local]"
    if entry:
        return f"{shown}/{archive} {candidate} {arch} [residual-config]"
    return f"{shown}/{archive} {candidate} {arch}"


def tpot_apt_search_pattern(term: str):
    """T-Pot: a pattern of apt search as a regular expression in any case (POSIX ERE without groups, bounded as
    the regular expressions of names), another one as its text; with the text the names it matches need."""
    import warnings
    lower = term.lower()
    if (len(term) <= TPOT_APT_REGEX_LENGTH and re.fullmatch(r"[a-z0-9+.*?^$|\[\] _-]+", lower)
            and sum(term.count(c) for c in "*+?") <= TPOT_APT_REGEX_REPEATS):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                return re.compile(lower), tpot_apt_regex_parts(lower)
        except re.error:
            pass
    return re.compile(re.escape(lower)), [(lower,)]


@functools.cache
def tpot_apt_described() -> tuple:
    """T-Pot: (name, description in lower case, description) of the release's packages the persona has a
    description of, sorted by name."""
    return tuple((name, meta[-1].lower(), meta[-1]) for name, meta in sorted(tpot_apt_data()["meta"].items())
                 if meta and meta[-1] and meta[-1] != name)


def tpot_apt_found(cmd, terms: list, names_only: bool) -> list:
    """T-Pot: the packages of the release whose name (or description) every term matches, sorted by name, at
    most TPOT_APT_LIST_LIMIT, within the session's scan time; only those the persona has a description of."""
    patterns = [tpot_apt_search_pattern(term) for term in terms]
    found = []
    for name, lower, description in tpot_scanned(cmd.protocol, tpot_apt_described()):
        if len(found) >= TPOT_APT_LIST_LIMIT:
            break
        text = "" if names_only else lower
        if all(any(all(part in name for part in branch) or all(part in text for part in branch) for branch in needed)
               if needed else True for _, needed in patterns) \
                and all(pattern.search(name) or (text and pattern.search(text)) for pattern, _ in patterns):
            found.append((name, description))
    return found


def _tpot_apt_search(self):
    """T-Pot: apt search (refs of debian:12, debian:13, ubuntu:24.04): the progress lines, then each package as
    apt list writes it with its description; the name in green on a terminal."""
    tty = tpot_apt_tty(self)
    if not tty:
        self.errorWrite(TPOT_APT_WARNING)
    terms = self.tpot_words[1:]
    if not terms:
        self.errorWrite(tpot_apt_errors(self, "E: You must give at least one search pattern\n"))
        self.exit(100)
        return
    names_only = "--names-only" in self.args or any(re.fullmatch(r"-[a-z]*n[a-z]*", arg) for arg in self.args)
    found = tpot_apt_found(self, terms, names_only)
    db, archives = tpot_apt_db(self.protocol), tpot_apt_data()["archives"]
    upgradable = tpot_apt_upgradable(self.protocol)
    colour = tty and tpot_apt_release() >= (2, 0)
    if not tty:
        self.write("Sorting...\nFull Text Search...\n")
    elif not tpot_apt_three():
        # apt 2.9 and later clear their progress lines on a terminal
        self.write("Sorting... Done\nFull Text Search... Done\n")
    self.write("".join(
        tpot_apt_list_row(name, db, archives, upgradable, f"\x1b[32m{name}\x1b[0m" if colour else None)
        + f"\n  {description}\n\n" for name, description in found))
    self.exit()


def _tpot_apt_list(self):
    """T-Pot: apt list [--installed | --upgradable] [patterns] of the session."""
    if not tpot_apt_tty(self):
        self.errorWrite(TPOT_APT_WARNING)
    patterns = self.tpot_words[1:]
    db = tpot_apt_db(self.protocol)
    archives = tpot_apt_data()["archives"]
    upgradable = tpot_apt_upgradable(self.protocol)
    rows = []
    if "--installed" in self.args or "--upgradable" in self.args:
        candidates = sorted(name for name, entry in db["packages"].items() if entry["status"] == "ii")
        if "--upgradable" in self.args:
            candidates = [name for name in candidates if name in upgradable]
    else:
        names = (tpot_apt_lists() or (None, ()))[1] or tuple(sorted(tpot_apt_data()["meta"]))
        candidates = sorted(set(names) | set(db["packages"]))
    for name in candidates:
        if patterns and not any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns):
            continue
        rows.append(tpot_apt_list_row(name, db, archives, upgradable))
        if len(rows) >= TPOT_APT_LIST_LIMIT:
            break
    if not tpot_apt_tty(self) or not patterns:
        self.write("Listing...\n" if tpot_apt_tty(self) else "")
    self.write("".join(f"{row}\n" for row in rows))
    self.exit()


Command_aptget.tpot_install = inlineCallbacks(_tpot_apt_install)
Command_aptget.tpot_remove = inlineCallbacks(_tpot_apt_remove)
Command_aptget.tpot_upgrade = inlineCallbacks(_tpot_apt_upgrade)
Command_aptget.tpot_autoremove = _tpot_apt_autoremove
Command_aptget.tpot_list = _tpot_apt_list
Command_aptget.tpot_show = _tpot_apt_show
Command_aptget.tpot_search = _tpot_apt_search


# T-Pot: dpkg, dpkg-query and apt-cache of the persona, on the package state of the session
TPOT_DPKG_HEAD = ("Desired=Unknown/Install/Remove/Purge/Hold\n"
                  "| Status=Not/Inst/Conf-files/Unpacked/halF-conf/Half-inst/trig-aWait/Trig-pend\n"
                  "|/ Err?=(none)/Reinst-required (Status,Err: uppercase=bad)\n")
TPOT_DPKG_HELP = ("\nType dpkg --help for help about installing and deinstalling packages [*];\n"
                  "Use 'apt' or 'aptitude' for user-friendly package management;\n"
                  "Type dpkg -Dhelp for a list of dpkg debug flag values;\n"
                  "Type dpkg --force-help for a list of forcing options;\n"
                  "Type dpkg-deb --help for help about manipulating *.deb files;\n\n"
                  "Options marked [*] produce a lot of output - pipe it through 'less' or 'more' !\n")
TPOT_DPKG_ACTIONS = {"-l": "list", "--list": "list", "-s": "status", "--status": "status", "-L": "listfiles",
                     "--listfiles": "listfiles", "-S": "search", "--search": "search", "-W": "show", "--show": "show",
                     "--get-selections": "selections", "-i": "install", "--install": "install", "-r": "remove",
                     "--remove": "remove", "-P": "purge", "--purge": "purge", "--version": "version",
                     "--print-architecture": "arch", "-p": "print-avail", "--print-avail": "print-avail"}
TPOT_DPKG_SHOW = re.compile(r"\$\{([A-Za-z:-]+)(?:;(-?[0-9]{1,3}))?\}")


def tpot_dpkg_rows(cmd, patterns: list):
    """T-Pot: ([(name, entry)], patterns that matched nothing) of the session's state for dpkg -l."""
    packages = tpot_apt_db(cmd.protocol)["packages"]
    if not patterns:
        return sorted(packages.items()), []
    found, missing = {}, []
    for pattern in patterns:
        hits = [name for name in sorted(packages) if fnmatch.fnmatchcase(name, pattern)]
        if not hits:
            missing.append(pattern)
        for name in hits:
            found[name] = packages[name]
    return sorted(found.items()), missing


def tpot_dpkg_desc(name: str) -> str:
    meta = tpot_apt_meta(name)
    return meta[-1] if meta else name


def tpot_dpkg_list(cmd, patterns: list) -> int:
    rows, missing = tpot_dpkg_rows(cmd, patterns)
    if rows:
        shown = [("ii" if entry["status"] == "ii" else "rc", tpot_apt_qualified(name), entry["version"],
                  tpot_apt_arch(name), tpot_dpkg_desc(name)) for name, entry in rows]
        nw = max([14] + [len(row[1]) for row in shown])
        vw = max([12] + [len(row[2]) for row in shown])
        aw = max([12] + [len(row[3]) for row in shown])
        dw = max([33] + [len(row[4]) for row in shown])
        cmd.write(TPOT_DPKG_HEAD + f"||/ {'Name':<{nw}} {'Version':<{vw}} {'Architecture':<{aw}} Description\n"
                  f"+++-{'=' * nw}-{'=' * vw}-{'=' * aw}-{'=' * dw}\n"
                  + "".join(f"{s:<3} {n:<{nw}} {v:<{vw}} {a:<{aw}} {d}\n" for s, n, v, a, d in shown))
    for pattern in missing:
        cmd.errorWrite(f"dpkg-query: no packages found matching {pattern}\n")
    return 1 if missing else 0


def tpot_dpkg_status(cmd, names: list) -> int:
    packages = tpot_apt_db(cmd.protocol)["packages"]
    failed, blocks = 0, []
    for name in names:
        entry = packages.get(name)
        if entry is None:
            cmd.errorWrite(f"dpkg-query: package '{name}' is not installed and no information is available\n")
            failed += 1
            continue
        meta = tpot_apt_meta(name) or [entry["version"], "main", "-", tpot_apt_sizes(name)[1] // 1024, 0, "-",
                                      "misc", "", "optional", "-", name]
        state = "install ok installed" if entry["status"] == "ii" else "deinstall ok config-files"
        essential = "Essential: yes\n" if meta[9] == "yes" else ""
        block = (f"Package: {name}\n{essential}Status: {state}\nPriority: {meta[8]}\nSection: {meta[6]}\n"
                 f"Installed-Size: {meta[3]}\n" + (f"Maintainer: {meta[7]}\n" if meta[7] else "")
                 + f"Architecture: {tpot_apt_arch(name)}\nVersion: {entry['version']}\n")
        if entry["status"] == "rc":
            block += "Config-Version: " + entry["version"] + "\n"
        blocks.append(block + f"Description: {meta[-1]}\n")
    cmd.write("\n".join(blocks))
    if failed:
        cmd.errorWrite("Use dpkg --info (= dpkg-deb --info) to examine archive files.\n")
    return 1 if failed else 0


def tpot_dpkg_listfiles(cmd, names: list) -> int:
    packages = tpot_apt_db(cmd.protocol)["packages"]
    failed = 0
    for number, name in enumerate(names):
        entry = packages.get(name)
        if entry is None or entry["status"] != "ii":
            cmd.errorWrite(f"dpkg-query: package '{name}' is not installed\n")
            failed += 1
            continue
        programs = entry.get("programs") or tpot_apt_data()["removes"].get(name) or []
        cmd.write(("\n" if number and len(names) > 1 else "")
                  + "".join(f"{path}\n" for path in tpot_dpkg_files(name, programs)))
    if failed:
        cmd.errorWrite("Use dpkg --contents (= dpkg-deb --contents) to list archive files contents.\n")
    return 1 if failed else 0


def tpot_dpkg_search(cmd, patterns: list) -> int:
    packages = tpot_apt_db(cmd.protocol)["packages"]
    failed = 0
    for pattern in patterns:
        glob = pattern if pattern.startswith("/") or set(pattern) & set("*?[") else f"*{pattern}*"
        hits = []
        for name, entry in sorted(packages.items()):
            if entry["status"] != "ii":
                continue
            programs = entry.get("programs") or tpot_apt_data()["removes"].get(name) or []
            hits += [f"{name}: {path}" for path in tpot_dpkg_files(name, programs)
                     if path != "/." and fnmatch.fnmatchcase(path, glob)]
        if hits:
            cmd.write("".join(f"{hit}\n" for hit in hits[:TPOT_APT_LIST_LIMIT]))
        else:
            cmd.errorWrite(f"dpkg-query: no path found matching pattern {glob}\n")
            failed += 1
    return 1 if failed else 0


def tpot_dpkg_show(cmd, patterns: list, fmt: str | None) -> int:
    rows, missing = tpot_dpkg_rows(cmd, patterns)
    fmt = "${binary:Package}\\t${Version}\\n" if fmt is None else fmt
    fmt = fmt.replace("\\t", "\t").replace("\\n", "\n")

    def value(name, entry, field):
        status = "install ok installed" if entry["status"] == "ii" else "deinstall ok config-files"
        return {"Package": name, "binary:Package": tpot_apt_qualified(name), "Version": entry["version"],
                "Architecture": tpot_apt_arch(name), "Status": status,
                "db:Status-Abbrev": "ii " if entry["status"] == "ii" else "rc ",
                "Installed-Size": str((tpot_apt_meta(name) or [0, 0, 0, tpot_apt_sizes(name)[1] // 1024])[3]),
                "binary:Summary": tpot_dpkg_desc(name), "Description": tpot_dpkg_desc(name),
                "Maintainer": (tpot_apt_meta(name) or [""] * 9)[7]}.get(field, "")

    def expand(name, entry):
        def one(hit):
            text = value(name, entry, hit.group(1))
            width = int(hit.group(2)) if hit.group(2) else 0
            return text.ljust(-width) if width < 0 else text.rjust(width)
        return TPOT_DPKG_SHOW.sub(one, fmt)

    cmd.write("".join(expand(name, entry) for name, entry in rows if entry["status"] == "ii" or patterns))
    for pattern in missing:
        cmd.errorWrite(f"dpkg-query: no packages found matching {pattern}\n")
    return 1 if missing else 0


def tpot_dpkg_remove(cmd, names: list, purge: bool) -> int:
    db = tpot_apt_db(cmd.protocol)
    head = False
    for name in names:
        entry = db["packages"].get(name)
        if entry is None or (entry["status"] == "rc" and not purge):
            cmd.errorWrite(f"dpkg: warning: ignoring request to remove {name} which isn't installed\n")
            continue
        if tpot_apt_essential(name):
            cmd.errorWrite(f"dpkg: error processing package {name} (--{'purge' if purge else 'remove'}):\n"
                           " this is an essential package; it should not be removed\n"
                           f"Errors were encountered while processing:\n {name}\n")
            return 1
        if not head:
            cmd.write(f"(Reading database ... {db['files']} files and directories currently installed.)\n")
            head = True
        if entry["status"] == "ii":
            cmd.write(f"Removing {tpot_apt_qualified(name)} ({entry['version']}) ...\n")
            tpot_apt_unplace(cmd, name, entry)
            db["files"] -= len(entry.get("programs") or []) * 2 + 3
            cmd.write(tpot_package_units(cmd, name, "remove"))
        conffiles = name in tpot_apt_data()["conffiles"]
        if purge and conffiles:
            cmd.write(f"Purging configuration files for {tpot_apt_qualified(name)} ({entry['version']}) ...\n")
        if purge:
            cmd.write(tpot_package_units(cmd, name, "purge"))
        if conffiles and not purge:
            db["packages"][name] = dict(entry, status="rc")
        else:
            db["packages"].pop(name, None)
    return 0


class Command_dpkg(HoneyPotCommand):
    """T-Pot: dpkg of the persona (-l, -s, -L, -S, -W, --get-selections, -i, -r, -P, --version)."""

    tpot_prog = "dpkg"

    def call(self) -> None:
        try:
            self.exit_code = self.tpot_dpkg()
        except Exception:  # noqa: BLE001 - the command always exits
            self.exit_code = 2

    def tpot_dpkg(self) -> int:
        action, words, fmt = None, [], None
        items = list(self.args)
        while items:
            arg = items.pop(0)
            if arg in ("-f", "--showformat") and items:
                fmt = items.pop(0)
            elif arg.startswith("--showformat="):
                fmt = arg.split("=", 1)[1]
            elif arg in TPOT_DPKG_ACTIONS:
                action = action or TPOT_DPKG_ACTIONS[arg]
            elif re.fullmatch(r"-[lsLSWiPr]+", arg) and len(arg) > 2:
                action = action or TPOT_DPKG_ACTIONS.get(arg[:2])
            elif arg.startswith("-"):
                if arg.startswith(("--force", "--no-", "--admindir", "--root", "--instdir", "--log", "-B", "-G",
                                   "-E", "-R", "--recursive", "--auto-deconfigure")):
                    continue
                self.errorWrite(f"{self.tpot_prog}: error: unknown option {arg}\n" + TPOT_DPKG_HELP)
                return 2
            else:
                words.append(arg)
        if action is None:
            self.errorWrite(f"{self.tpot_prog}: error: need an action option\n" + TPOT_DPKG_HELP)
            return 2
        if action == "version":
            self.write(f"Debian '{self.tpot_prog}' package management program version "
                       f"{tpot_apt('dpkg_version', '1.21.22')} ({tpot_apt('deb_arch', 'amd64')}).\n"
                       "This is free software; see the GNU General Public License version 2 or\n"
                       "later for copying conditions. There is NO warranty.\n")
            return 0
        if action == "arch":
            self.write(tpot_apt("deb_arch", "amd64") + "\n")
            return 0
        if action == "list":
            return tpot_dpkg_list(self, words)
        if action == "show":
            return tpot_dpkg_show(self, words, fmt)
        if action == "selections":
            rows, _ = tpot_dpkg_rows(self, words)
            self.write("".join(f"{tpot_apt_qualified(n)}{chr(9) * max(1, 6 - (len(tpot_apt_qualified(n)) >> 3))}"
                               f"{'install' if e['status'] == 'ii' else 'deinstall'}\n" for n, e in rows))
            return 0
        if action in ("status", "listfiles", "search", "print-avail") and not words:
            self.errorWrite(f"{self.tpot_prog}: error: --{action} needs at least one package name argument\n"
                            + TPOT_DPKG_HELP)
            return 2
        if action == "status":
            return tpot_dpkg_status(self, words)
        if action == "listfiles":
            return tpot_dpkg_listfiles(self, words)
        if action == "search":
            return tpot_dpkg_search(self, words)
        if action == "print-avail":
            for word in words:
                self.errorWrite(f"dpkg-query: package '{word}' is not available\n")
            self.errorWrite("Use dpkg --info (= dpkg-deb --info) to examine archive files.\n")
            return 1
        if self.tpot_prog != "dpkg":
            self.errorWrite(f"{self.tpot_prog}: error: unknown option --{action}\n" + TPOT_DPKG_HELP)
            return 2
        if not tpot_privileged(self):
            self.errorWrite("dpkg: error: requested operation requires superuser privilege\n")
            return 2
        if not words:
            self.errorWrite(f"dpkg: error: --{action} needs at least one package name argument\n" + TPOT_DPKG_HELP)
            return 2
        if action in ("remove", "purge"):
            return tpot_dpkg_remove(self, words, action == "purge")
        # -i: the archives the session gives are no Debian packages here
        for word in words:
            path = self.fs.resolve_path(word, self.cwd)
            if not self.fs.exists(path):
                self.errorWrite(f"dpkg: error: cannot access archive '{word}': No such file or directory\n")
                return 2
        for word in words:
            self.errorWrite(f"dpkg-deb: error: '{word}' is not a Debian format archive\n"
                            f"dpkg: error processing archive {word} (--install):\n"
                            " dpkg-deb --control subprocess returned error exit status 2\n")
        self.errorWrite("Errors were encountered while processing:\n" + "".join(f" {w}\n" for w in words))
        return 1


class Command_dpkg_query(Command_dpkg):
    """T-Pot: dpkg-query, the queries of dpkg."""

    tpot_prog = "dpkg-query"


TPOT_APT_CACHE_FIELDS = {
    "debian": ("Package", "Version", "Installed-Size", "Maintainer", "Architecture", "Description", "Section",
               "Priority", "Filename", "Size", "MD5sum", "SHA256"),
    "jammy": ("Package", "Architecture", "Version", "Priority", "Section", "Origin", "Maintainer", "Bugs",
              "Installed-Size", "Filename", "Size", "MD5sum", "SHA1", "SHA256", "SHA512", "Description"),
    "noble": ("Package", "Priority", "Section", "Installed-Size", "Maintainer", "Architecture", "Version",
              "Filename", "Size", "MD5sum", "SHA1", "SHA256", "SHA512", "Description", "Origin", "Bugs"),
}


def tpot_apt_cache_show(cmd, names: list) -> int:
    """T-Pot: apt-cache show of the release's candidates (the fields of the index the persona keeps,
    in the order of its release; the sums are those of the name and version, not of a real file)."""
    ubuntu = tpot_apt("apt_fallback", "debian") == "ubuntu"
    code = (tpot_apt_sources() or [("", "")])[0][1].split("-", 1)[0]
    order = TPOT_APT_CACHE_FIELDS["noble" if ubuntu and code != "jammy" else "jammy" if ubuntu else "debian"]
    blocks, missing = [], []
    release = tpot_apt_names()
    for name in names:
        meta = tpot_apt_meta(name)
        if meta is None and (release is None or name not in release):
            missing.append(name)
            continue
        version = tpot_apt_candidate(name)
        meta = meta or [version, "main", "-", tpot_apt_sizes(name)[1] // 1024, tpot_apt_sizes(name)[0], "-", "misc",
                        "", "optional", "-", name]
        arch = tpot_apt_arch(name)
        # the sums of the name, version and architecture (no file of the archive is read)
        seed = f"{name} {version} {arch}".encode()
        prefix = name[:4] if name.startswith("lib") else name[0]
        section = meta[6] if meta[1] == "main" or not ubuntu else f"{meta[1]}/{meta[6]}"
        fields = {"Package": name, "Version": version, "Installed-Size": str(meta[3]), "Maintainer": meta[7],
                  "Architecture": arch, "Description": meta[-1], "Section": section, "Priority": meta[8],
                  "Origin": "Ubuntu", "Bugs": "https://bugs.launchpad.net/ubuntu/+filebug",
                  "Filename": f"pool/{meta[1]}/{prefix}/{name}/{name}_{version.split(':', 1)[-1]}_{arch}.deb",
                  "Size": str(meta[4]), "MD5sum": hashlib.md5(seed).hexdigest(), "SHA1": hashlib.sha1(seed).hexdigest(),
                  "SHA256": hashlib.sha256(seed).hexdigest(), "SHA512": hashlib.sha512(seed).hexdigest()}
        blocks.append("".join(f"{field}: {fields[field]}\n" for field in order if fields[field]) + "\n")
    cmd.write("".join(blocks))
    if missing or not names:
        cmd.errorWrite("".join(f"N: Unable to locate package {name}\n" for name in missing)
                       + ("E: No packages found\n" if not blocks else ""))
        return 100 if not blocks else 0
    return 0


def tpot_apt_cache_policy(cmd, names: list) -> int:
    """T-Pot: apt-cache policy of the session's state and the release's candidates (pin 500, the
    status file 100)."""
    db = tpot_apt_db(cmd.protocol)
    arch = tpot_apt("deb_arch", "amd64")
    release = tpot_apt_names()
    if not names:
        lines = ["Package files:\n", " 100 /var/lib/dpkg/status\n", "     release a=now\n"]
        for url, suite in tpot_apt_sources():
            lines.append(f" 500 {url} {suite}/main {arch} Packages\n")
        cmd.write("".join(lines) + "Pinned packages:\n")
        return 0
    for name in names:
        meta = tpot_apt_meta(name)
        entry = db["packages"].get(name)
        if meta is None and entry is None and (release is None or name not in release):
            cmd.errorWrite(f"N: Unable to locate package {name}\n")
            continue
        candidate = tpot_apt_candidate(name)
        installed = entry["version"] if entry and entry["status"] == "ii" else None
        url, suite = tpot_apt_source(meta[2] if meta else "-")
        component = meta[1] if meta else "main"
        lines = [f"{name}:\n", f"  Installed: {installed or '(none)'}\n", f"  Candidate: {candidate}\n",
                 "  Version table:\n",
                 f" {'***' if installed == candidate else '   '} {candidate} 500\n",
                 f"        500 {url} {suite}/{component} {arch} Packages\n"]
        if installed == candidate:
            lines.append("        100 /var/lib/dpkg/status\n")
        elif installed:
            lines += [f" *** {installed} 100\n", "        100 /var/lib/dpkg/status\n"]
        cmd.write("".join(lines))
    return 0


class Command_apt_cache(HoneyPotCommand):
    """T-Pot: apt-cache of the persona (policy, show, madison, pkgnames); the others as apt-cache answers
    a command it does not know."""

    def call(self) -> None:
        try:
            self.exit_code = self.tpot_cache()
        except Exception:  # noqa: BLE001 - the command always exits
            self.exit_code = 100

    def tpot_cache(self) -> int:
        words = [arg for arg in self.args if not arg.startswith("-")]
        if not words:
            self.write(f"{tpot_apt_banner()}\nUsage: apt-cache [options] command\n"
                       "       apt-cache [options] show pkg1 [pkg2 ...]\n")
            return 1
        if words[0] == "policy":
            return tpot_apt_cache_policy(self, words[1:])
        if words[0] == "show":
            return tpot_apt_cache_show(self, words[1:])
        if words[0] == "madison":
            for name in words[1:]:
                meta = tpot_apt_meta(name)
                if meta:
                    url, suite = tpot_apt_source(meta[2])
                    self.write(f"{name:>10} | {meta[0]} | {url} {suite}/{meta[1]} {tpot_apt('deb_arch', 'amd64')} "
                               "Packages\n")
                else:
                    self.errorWrite(f"N: Unable to locate package {name}\n")
            return 0
        if words[0] == "search":
            if not words[1:]:
                self.errorWrite("E: You must give at least one search pattern\n")
                return 100
            names_only = "--names-only" in self.args or "-n" in self.args
            self.write("".join(f"{name} - {description}\n"
                               for name, description in tpot_apt_found(self, words[1:], names_only)))
            return 0
        if words[0] == "pkgnames":
            names = (tpot_apt_lists() or (None, ()))[1] or tuple(sorted(tpot_apt_data()["meta"]))
            prefix = words[1] if len(words) > 1 else ""
            self.write("".join(f"{n}\n" for n in names if n.startswith(prefix)))
            return 0
        self.errorWrite(f"E: Invalid operation {words[0]}\n")
        return 100


commands["/usr/bin/dpkg"] = Command_dpkg
commands["/bin/dpkg"] = Command_dpkg
commands["dpkg"] = Command_dpkg
commands["/usr/bin/dpkg-query"] = Command_dpkg_query
commands["/bin/dpkg-query"] = Command_dpkg_query
commands["dpkg-query"] = Command_dpkg_query
commands["/usr/bin/apt-cache"] = Command_apt_cache
commands["/bin/apt-cache"] = Command_apt_cache
commands["apt-cache"] = Command_apt_cache
'''


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


@functools.cache
def tpot_opkg_meta() -> dict[str, tuple[str, str]]:
    """T-Pot: {name: (version, description)} of the feeds of the release ([shell] opkg_meta)."""
    path = tpot_opkg("opkg_meta")
    try:
        rows = [line.split("\t") for line in Path(path).read_text(encoding="utf-8").splitlines()] if path else []
    except (OSError, ValueError):
        rows = []
    return {row[0]: (row[1], row[2]) for row in rows if len(row) == 3}


def tpot_opkg_version(name: str) -> str:
    """T-Pot: the version of a package of the lists: the feed's, else the same for a name on every call."""
    if name in tpot_opkg_meta():
        return tpot_opkg_meta()[name][0]
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
        elif words[0] == "list-installed":
            self.write("".join(f"{name} - {version}\n" for name, version in sorted(installed.items())))
            self.exit()
        elif words[0] == "list":
            # the installed packages; after `opkg update` of the session the names of the feeds too,
            # each with its version and description of the lists
            known = dict(installed)
            if getattr(self.protocol, "tpot_opkg_lists", False):
                for name in tpot_opkg_names():
                    known.setdefault(name, None)
            shown = sorted(name for name in known if len(words) < 2 or fnmatch.fnmatchcase(name, words[1]))
            meta = tpot_opkg_meta()
            self.write("".join(f"{name} - {known[name] or tpot_opkg_version(name)}"
                               + (f" - {meta[name][1]}" if meta.get(name, ("", ""))[1] else "") + "\n"
                               for name in shown))
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


# module code of the patched yum.py after Command_yum: the package state of yum 3 (CentOS 7)
YUM3_COMMAND = r'''

# T-Pot: the package state of the session for yum 3 and rpm of CentOS 7. It starts from the installed
# packages of the persona ([shell] yum_installed) and lives on the protocol; the versions, repositories,
# sizes and summaries of the other packages are those of the CentOS 7.9.2009 repositories ([shell]
# yum_meta). yum 3.4.3 writes its tables as dnf (calcColumns, fmtColumns), its lines at 80 columns.
# the name scans (globs, searches, provides) take of the session's time (tpot_scanned, TPOT_BUDGET)
from cowrie.shell.command import tpot_scanned  # noqa: E402

TPOT_YUM_WIDTH = 80
TPOT_YUM_GLOBS = 16
# the packages of the base system systemd and yum depend on: removing one takes them, both are protected
TPOT_YUM_BASE = frozenset({"bash", "basesystem", "coreutils", "filesystem", "gawk", "glibc", "glibc-common", "grep",
                           "pam", "python", "python-libs", "rpm", "rpm-libs", "sed", "setup", "shadow-utils",
                           "systemd-libs", "util-linux", "openssl-libs"})
TPOT_YUM_GLOB_MATCHES = 64


@tpot_functools.cache
def tpot_yum_meta() -> dict:
    """T-Pot: {name: [arch, epoch:version-release, repo, size, installed size, summary, url, license, group,
    description, source rpm]} ([shell] yum_meta)."""
    return tpot_rpm_meta_file(CowrieConfig.get("shell", "yum_meta", fallback=""))


def tpot_rpm_meta_file(path: str) -> dict:
    """T-Pot: the rows of a yum-meta / dnf-meta file (the description's newlines written as \\n)."""
    try:
        rows = [line.split("\t") for line in tpot_pathlib.Path(path).read_text(encoding="utf-8").splitlines()] \
            if path else []
    except (OSError, ValueError):
        rows = []
    return {row[0]: [row[1], row[2], row[3], int(row[4]), int(row[5]), row[6], row[7], row[8], row[9],
                     row[10].replace("\\n", "\n"), row[11]] for row in rows
            if len(row) == 12 and row[4].isdigit() and row[5].isdigit()}


def tpot_yum_entry(name: str, evr: str | None = None) -> dict:
    """T-Pot: what yum knows of a package: the repositories' build, else one that fits CentOS 7 (the same
    for a name on every call)."""
    meta = tpot_yum_meta().get(name)
    if meta:
        return {"arch": meta[0], "evr": evr or meta[1], "repo": meta[2], "size": meta[3], "isize": meta[4],
                "summary": meta[5], "url": meta[6], "license": meta[7], "group": meta[8], "description": meta[9],
                "programs": tpot_yum_programs(name)}
    d = hashlib.sha256(f"centos-7 {name}".encode()).digest()
    version = f"{1 + d[0] % 5}.{d[1] % 20}.{d[2] % 10}-{1 + d[3] % 12}.el7"
    return {"arch": "noarch" if name in TPOT_YUM_NOARCH else "x86_64", "evr": evr or f"0:{version}", "repo": "base",
            "size": 20_000 + int.from_bytes(d[4:7], "big") % 900_000, "isize": 0, "summary": "", "url": "",
            "license": "", "group": "Unspecified", "description": "", "programs": tpot_yum_programs(name)}


@tpot_functools.cache
def tpot_yum_paths() -> dict:
    """T-Pot: {package: [programs]} of the programs the repositories list ([shell] package_names "path package")."""
    found: dict = {}
    for provided, package in (tpot_yum_lists() or (None, {}))[1].items():
        if provided.startswith("/"):
            found.setdefault(package, []).append(provided)
    return found


def tpot_yum_programs(name: str) -> list:
    """T-Pot: the programs of a package: those the repositories list, else /usr/bin/<name>."""
    # the helpers of the i686 build of a multilib package (glibc_post_upgrade.i686) are not the system's
    return sorted(path for path in tpot_yum_paths().get(name, []) if not path.endswith(".i686")) or [
        f"/usr/bin/{name}"]


def tpot_yum_db(protocol) -> dict:
    """T-Pot: {name: entry with "from" (the repository yum installed it from)} of the session."""
    db = getattr(protocol, "tpot_yum_db", None)
    if db is None:
        db = {}
        for name, evr in tpot_yum_installed().items():
            entry = tpot_yum_entry(name, evr if ":" in evr else f"0:{evr}")
            entry["from"] = "@anaconda"
            db[name] = entry
        try:
            protocol.tpot_yum_db = db
        except AttributeError:
            pass
    return db


def tpot_yum_plain(evr: str) -> str:
    """T-Pot: the version yum's tables write: no epoch 0."""
    epoch, _, plain = evr.partition(":")
    return plain if epoch == "0" else evr


def tpot_yum_nevra(name: str, entry: dict) -> str:
    """T-Pot: name-version-release.arch with the epoch in front (1:mariadb-libs-5.5.68-1.el7.x86_64)."""
    epoch, _, plain = entry["evr"].partition(":")
    return f"{'' if epoch == '0' else epoch + ':'}{name}-{plain}.{entry['arch']}"


@tpot_functools.cache
def tpot_yum_sorted() -> tuple:
    """T-Pot: the names of the release's packages, sorted."""
    return tuple(sorted((tpot_yum_lists() or (frozenset(),))[0]))


def tpot_yum_find(cmd, words: list) -> tuple[dict, list, bool]:
    """T-Pot: ({name: entry} wanted, notes, found any) of the words: names, globs, names and programs a
    package provides; installed packages of the session are there already."""
    lists, db = tpot_yum_lists(), tpot_yum_db(cmd.protocol)
    wanted, notes, found_any, globs = {}, [], False, 0
    for word in words:
        name = re.sub(r"\.(x86_64|noarch|i686)$", "", word)
        matches = []
        if set(name) & set("*?[") and lists is not None and globs < TPOT_YUM_GLOBS:
            globs += 1
            matches = [n for n in tpot_scanned(cmd.protocol, tpot_yum_sorted()) if fnmatch.fnmatchcase(n, name)][
                :TPOT_YUM_GLOB_MATCHES]
        elif lists is not None and name not in lists[0] and name in lists[1]:
            matches = [lists[1][name]]
        elif re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+._-]{0,99}", name) and (lists is None or name in lists[0]):
            matches = [name]
        if not matches:
            notes.append(f"No package {tpot_yum_bold(cmd, word)} available.\n")
            if lists is not None and word.lower() in lists[0] and word.lower() != word:
                notes.append(f"  * Maybe you meant: {word.lower()}\n")
            continue
        found_any = True
        for match in matches:
            if match in db:
                if not set(name) & set("*?["):
                    notes.append(tpot_yum_present(match, db[match]["evr"].replace("0:", "", 1)
                                                  if db[match]["evr"].startswith("0:") else db[match]["evr"]))
            else:
                wanted[match] = tpot_yum_entry(match)
    return wanted, notes, found_any


def tpot_yum_table(rows: list, size_key: str) -> list[str]:
    """T-Pot: yum's table of a transaction: (title, [(name, entry, repo)]) sections."""
    flat = [(name, entry, repo) for _, part in rows for name, entry, repo in part]
    a_wid = max(len(entry["arch"]) for _, entry, _ in flat)
    columns = tpot_dnf_columns([tpot_dnf_lengths(name for name, _, _ in flat), {},
                                tpot_dnf_lengths(tpot_yum_plain(entry["evr"]) for _, entry, _ in flat),
                                tpot_dnf_lengths(repo for _, _, repo in flat), {}],
                               [1, a_wid, 1, 1, 5], remainder_column=2, indent="  ")
    n_wid, a_wid, v_wid, r_wid, s_wid = columns
    header = tpot_dnf_fmt((("Package", -n_wid), ("Arch", -a_wid), ("Version", -v_wid), ("Repository", -r_wid),
                           ("Size", s_wid)), " ")
    lines = ["=" * TPOT_YUM_WIDTH, header, "=" * TPOT_YUM_WIDTH]
    for title, part in rows:
        lines.append(f"{title}:")
        for name, entry, repo in part:
            lines.append(tpot_dnf_fmt(((name, -n_wid), (entry["arch"], -a_wid), (tpot_yum_plain(entry["evr"]), -v_wid),
                                       (repo, -r_wid), (tpot_dnf_number(entry[size_key]), s_wid)), " "))
    return lines


def tpot_yum_step(action: str, nevra: str, number: int, total: int) -> str:
    """T-Pot: a line of yum's transaction: the action, the package, its number at the end of 80 columns."""
    count = f"{number}/{total} "
    return f"  {action:<10} : {nevra}".ljust(TPOT_YUM_WIDTH - len(count)) + count + "\n"


def tpot_yum_out(cmd, names: list) -> str:
    return "\n".join(tpot_dnf_pto(names[0], names[1])) + "\n"


def tpot_yum_head(cmd, mirrors: bool = True) -> None:
    cmd.write(f"Loaded plugins: {tpot_yum_plugins()}\n")
    if mirrors and "fastestmirror" in tpot_yum_plugins():
        cache = tpot_yum_cache(cmd.protocol)
        # fastestmirror measures its mirrors again after yum clean all / plugins
        cmd.write("Loading mirror speeds from cached hostfile\n" if cache["mirrors"] else "Determining fastest mirrors\n")
        cache["mirrors"] = True
        if not cache["xml"]:
            # yum fetches the metadata of the repositories it reads again
            cache.update(xml=7, sqlite=18, cookies=3)


# the repositories of the persona's CentOS-Base.repo (base, extras, updates of 7.9.2009 on vault.centos.org, the
# repository id without $releasever): their names and the packages of their x86_64 primary.xml
TPOT_YUM_REPOS = (("base", "CentOS-7 - Base", 10072), ("extras", "CentOS-7 - Extras", 526),
                  ("updates", "CentOS-7 - Updates", 6173))
TPOT_YUM_CLEAN = ("headers", "packages", "metadata", "dbcache", "plugins", "expire-cache", "rpmdb", "all")


def tpot_yum_cache(protocol) -> dict:
    """T-Pot: yum's cache of the session: the list of fastest mirrors and the metadata of the repositories
    (as after yum makecache: 7 metadata files, 18 sqlite files, 3 cookies; centos:7)."""
    cache = getattr(protocol, "tpot_yum_cache", None)
    if cache is None:
        cache = {"mirrors": True, "xml": 7, "sqlite": 18, "cookies": 3}
        try:
            protocol.tpot_yum_cache = cache
        except AttributeError:
            pass
    return cache


def _tpot_yum_repolist(self):
    """T-Pot: yum 3 repolist [enabled|disabled|all] (RepoListCommand of yum 3.4.3: the extra width shared by the
    id and name columns, the counts in the locale's groups)."""
    which = (self.tpot_words[1:] or ["enabled"])[0]
    tpot_yum_head(self)
    if not tpot_privileged(self) and "ovl" in tpot_yum_plugins():
        self.errorWrite("ovl: Error while doing RPMdb copy-up:\n[Errno 13] Permission denied: '/var/lib/rpm/Sigmd5'\n")
    rows = [] if which == "disabled" else [(f"{rid}/x86_64", name, f"{count:,}") for rid, name, count in TPOT_YUM_REPOS]
    shown = "enabled: " if which == "all" else ""
    id_len = max([len("repo id")] + [len(r[0]) for r in rows])
    nm_len = max([0] + [len(r[1]) for r in rows])
    st_len = max([0] + [len(shown) + len(r[2]) for r in rows])
    ui_len = max([0] + [len(r[2]) for r in rows])
    if which == "disabled":
        left = TPOT_YUM_WIDTH - (id_len + 1)
    elif len("status") > st_len:
        left = TPOT_YUM_WIDTH - (id_len + len("status") + 2)
    else:
        left = TPOT_YUM_WIDTH - (id_len + st_len + 2)
    if left < nm_len:
        nm_len = left
    else:
        left -= nm_len
        id_len += left // 2
        nm_len += left - left // 2
    head = f"{'repo id':<{id_len}} {'repo name':<{nm_len}}"
    lines = [head if which == "disabled" else f"{head} status"]
    lines += [f"{rid:<{id_len}} {name[:nm_len]:<{nm_len}} {shown}{num:>{ui_len}}" for rid, name, num in rows]
    total = sum(count for _, _, count in TPOT_YUM_REPOS) if rows else 0
    self.write("\n".join(lines) + f"\nrepolist: {total:,}\n")
    self.exit()


def _tpot_yum_cache(self):
    """T-Pot: yum 3 clean and makecache [fast] as root (centos:7): what the session's cache holds."""
    cache = tpot_yum_cache(self.protocol)
    if self.tpot_words[0] == "makecache":
        tpot_yum_head(self)
        cache.update(xml=7, sqlite=18, cookies=3)
        self.write("Metadata Cache Created\n")
        self.exit()
        return
    tpot_yum_head(self, mirrors=False)
    kinds = self.tpot_words[1:]
    if not kinds:
        self.errorWrite("Error: clean requires an option: " + ", ".join(TPOT_YUM_CLEAN) + "\n")
        self.exit(1)
        return
    wrong = [kind for kind in kinds if kind not in TPOT_YUM_CLEAN]
    if wrong:
        self.errorWrite(f"Error: invalid clean argument: '{wrong[0]}'\n Mini usage:\n\nclean "
                        "[headers|packages|metadata|dbcache|plugins|expire-cache|all]\n\nRemove cached data\n")
        self.exit(1)
        return
    out = ["Cleaning repos: " + " ".join(rid for rid, _, _ in TPOT_YUM_REPOS)]
    mirrors = "fastestmirror" in tpot_yum_plugins()
    if "all" in kinds:
        if mirrors and cache["mirrors"]:
            out.append("Cleaning up list of fastest mirrors")
        cache.update(mirrors=False, xml=0, sqlite=0, cookies=0)
        kinds = []
    for kind in ("headers", "packages", "metadata", "dbcache", "expire-cache", "rpmdb", "plugins"):
        if kind not in kinds:
            continue
        if kind == "headers":
            out.append("0 header files removed")
        elif kind == "packages":
            out.append("0 package files removed")
        elif kind == "metadata":
            out += [f"{cache['xml']} metadata files removed", f"{cache['sqlite']} sqlite files removed",
                    "0 metadata files removed"]
            cache.update(xml=0, sqlite=0, cookies=0)
        elif kind == "dbcache":
            out.append(f"{cache['sqlite']} sqlite files removed")
            cache["sqlite"] = 0
        elif kind == "expire-cache":
            out.append(f"{cache['cookies']} metadata files removed")
            cache["cookies"] = 0
        elif kind == "rpmdb":
            out.append("0 rpmdb files removed")
        elif mirrors and cache["mirrors"]:
            out.append("Cleaning up list of fastest mirrors")
            cache["mirrors"] = False
        else:
            cache["mirrors"] = False
    self.write("\n".join(out) + "\n")
    self.exit()




def _tpot_yum_install(self, wanted: dict):
    """T-Pot: the transaction of yum 3 install: the table, the question (or -y), the steps, the session's
    state and the programs of the packages."""
    rows = [(name, entry, entry["repo"]) for name, entry in wanted.items()]
    # the dependencies of a covered package as yum 3 named them (ref centos-7-t12r2-install), those not installed
    db = tpot_yum_db(self.protocol)
    names = list(dict.fromkeys(dep for name in wanted for dep in tpot_package_with(name)
                               if dep not in wanted and dep not in db))[:128]
    deps = [(dep, entry, entry["repo"]) for dep, entry in ((dep, tpot_yum_entry(dep)) for dep in names)]
    self.write("Resolving Dependencies\n--> Running transaction check\n"
               + "".join(f"---> Package {name}.{entry['arch']} {entry['evr']} will be installed\n"
                         for name, entry, _ in rows + deps)
               + "--> Finished Dependency Resolution\n\nDependencies Resolved\n\n")
    self.write("\n".join(tpot_yum_table([("Installing", rows)] + ([("Installing for dependencies", deps)] if deps else []),
                                         "size")) + "\n\n")
    rows = deps + rows
    count = len(rows)
    self.write(f"Transaction Summary\n{'=' * TPOT_YUM_WIDTH}\nInstall  {count} Package{'s' if count > 1 else ''}\n\n"
               f"Total download size: {tpot_dnf_number(sum(e['size'] for _, e, _ in rows))}\n"
               f"Installed size: {tpot_dnf_number(sum(e['isize'] or e['size'] * 2 for _, e, _ in rows))}\n")
    self.tpot_pending = ("install", rows)
    if self.tpot_assume_yes:
        yield self.tpot_run3()
        return
    self.write("Is this ok [y/d/N]: ")


def _tpot_yum_run(self):
    kind, rows = self.tpot_pending
    self.tpot_pending = None
    self.write("Downloading packages:\n")
    yield self.sleep(0.5, 1)
    self.write("Running transaction check\nRunning transaction test\nTransaction test succeeded\nRunning transaction\n")
    db = tpot_yum_db(self.protocol)
    verb = "Installing" if kind == "install" else "Erasing"
    for number, (name, entry, _) in enumerate(rows, 1):
        self.write(tpot_yum_step(verb, tpot_yum_nevra(name, entry), number, len(rows)))
        yield self.sleep(0.2, 0.5)
    for number, (name, entry, _) in enumerate(rows, 1):
        self.write(tpot_yum_step("Verifying", tpot_yum_nevra(name, entry), number, len(rows)))
    made = []
    for name, entry, repo in rows:
        if kind == "install":
            # its programs, and the files rpm -ql names of a covered package (programs and unit files)
            programs = sorted(set(entry.get("programs") or tpot_yum_programs(name)) | set(tpot_package_files(name)))
            entry = dict(entry, programs=programs, installtime=time.time())
            entry["from"] = f"@{repo}"
            db[name] = entry
            tpot_rpm_place_manuals(self, name)
            for program in programs:
                if self.fs.exists(program):
                    continue
                try:
                    if self.fs.mkfile(program, 0, 0, random.randint(10000, 90000), 33261) is not False:
                        made.append(program)
                except Exception:  # noqa: BLE001 - a program the tree takes no more files for stays absent
                    continue
        else:
            tpot_rpm_unplace(self, name, db.get(name, {}))
            db.pop(name, None)
        # the scriptlets of systemd (preset, disable --now): silent on CentOS 7
        tpot_package_units(self, name, "install" if kind == "install" else "remove")
    restore = getattr(self.protocol.commands, "tpot_restore", None)
    for program in made:
        # a program of the persona the session removed: Cowrie's own command again (TpotCommands)
        if restore is not None and restore(program):
            continue
        if program not in self.protocol.commands and "/systemd/" not in program:
            self.protocol.commands[program] = tpot_yum_faked(program.rsplit("/", 1)[-1])
    tpot_yum_history(self).append({"user": self.user.get("username") or "root", "line": " ".join(self.args),
                                   "time": time.time(), "action": "Install" if kind == "install" else "Erase",
                                   "count": len(rows)})
    title = "Installed" if kind == "install" else "Removed"
    names = [f"{name}.{entry['arch']} {entry['evr']}" for name, entry, _ in rows]
    self.write("\n" + "\n".join(tpot_dnf_pto(title, names)) + "\n\nComplete!\n")
    self.exit()


def _tpot_yum_line(self, line: str):
    self._log.info("INPUT (yum): {line}", line=line)
    if getattr(self, "tpot_pending", None) is None:
        return
    answer = line.strip().lower()
    if answer in ("y", "yes"):
        yield self.tpot_run3()
    elif answer in ("d", "download", "downloadonly"):
        self.tpot_pending = None
        self.write("Background downloading packages, then exiting:\nexiting because \"Download Only\" specified\n")
        self.exit(1)
    else:
        self.tpot_pending = None
        stamp = time.strftime("%Y-%m-%d.%H-%M", time.localtime())
        # the name of tempfile.mkstemp, as yum saves the transaction
        tag = "".join(random.choice("abcdefghijklmnopqrstuvwxyz0123456789_") for _ in range(6))
        self.write("Exiting on user command\nYour transaction was saved, rerun it with:\n"
                   f" yum load-transaction /tmp/yum_save_tx.{stamp}.{tag}.yumtx\n")
        self.exit(1)


def _tpot_yum_remove(self):
    """T-Pot: yum 3 remove / erase of the session."""
    db = tpot_yum_db(self.protocol)
    tpot_yum_head(self, mirrors=False)
    rows, missing = [], []
    for word in self.tpot_words[1:]:
        hits = [n for n in sorted(db) if fnmatch.fnmatchcase(n, re.sub(r"\.(x86_64|noarch)$", "", word))]
        if not hits:
            missing.append(word)
        rows += [(n, db[n], db[n].get("from", "@anaconda")) for n in hits if n not in {r[0] for r in rows}]
    for word in missing:
        self.errorWrite(f"No Match for argument: {word}\n")
    if not rows:
        self.write("No Packages marked for removal\n")
        self.exit()
        return
    # yum 3 protects itself and systemd (/etc/yum/protected.d); the base system they need goes with them
    # (centos:7: yum remove glibc and bash name both)
    protected = [name for name, _, _ in rows if name in ("yum", "systemd")]
    if protected and not any(name in TPOT_YUM_BASE for name, _, _ in rows if name not in protected):
        self.errorWrite("".join(f"Error: Trying to remove \"{name}\", which is protected\n" for name in protected))
        self.exit(1)
        return
    if any(name in TPOT_YUM_BASE for name, _, _ in rows):
        self.write("Resolving Dependencies\n--> Running transaction check\n"
                   + "".join(f"---> Package {name}.{entry['arch']} {entry['evr']} will be erased\n"
                             for name, entry, _ in rows)
                   + "--> Finished Dependency Resolution\n")
        self.errorWrite('Error: Trying to remove "systemd", which is protected\n'
                        'Error: Trying to remove "yum", which is protected\n')
        self.exit(1)
        return
    self.write("Resolving Dependencies\n--> Running transaction check\n"
               + "".join(f"---> Package {name}.{entry['arch']} {entry['evr']} will be erased\n"
                         for name, entry, _ in rows)
               + "--> Finished Dependency Resolution\n\nDependencies Resolved\n\n")
    sized = [(name, dict(entry, isize=entry.get("isize") or entry["size"] * 2), repo) for name, entry, repo in rows]
    self.write("\n".join(tpot_yum_table([("Removing", sized)], "isize")) + "\n\n")
    count = len(rows)
    self.write(f"Transaction Summary\n{'=' * TPOT_YUM_WIDTH}\nRemove  {count} Package{'s' if count > 1 else ''}\n\n"
               f"Installed size: {tpot_dnf_number(sum(e['isize'] for _, e, _ in sized))}\n")
    self.tpot_pending = ("remove", rows)
    if self.tpot_assume_yes:
        yield self.tpot_run3()
        return
    self.write("Is this ok [y/N]: ")


def tpot_yum_matching(cmd, patterns: list) -> tuple[list, list]:
    """T-Pot: ([(name, entry)] installed, [(name, entry)] available) of the patterns (all without)."""
    db, lists = tpot_yum_db(cmd.protocol), tpot_yum_lists()
    names = sorted(lists[0]) if lists is not None else sorted(tpot_yum_meta())

    def hit(name):
        return not patterns or any(fnmatch.fnmatchcase(name, p) for p in patterns)

    installed = [(n, db[n]) for n in sorted(db) if hit(n)]
    available = [(n, tpot_yum_entry(n)) for n in (tpot_scanned(cmd.protocol, names) if patterns else names)
                 if n not in db and hit(n)][:5000]
    return installed, available


def _tpot_yum_list(self):
    words = self.tpot_words[1:]
    narrow = "all"
    if words and words[0] in ("all", "installed", "available", "updates", "extras", "obsoletes", "recent"):
        narrow, words = words[0], words[1:]
    tpot_yum_head(self, mirrors=narrow != "installed")
    installed, available = tpot_yum_matching(self, words)
    parts = []
    if narrow in ("all", "installed") and installed:
        parts.append(("Installed Packages", [(n, e, e.get("from", "@anaconda")) for n, e in installed]))
    if narrow in ("all", "available") and available:
        parts.append(("Available Packages", [(n, e, e["repo"]) for n, e in available]))
    if not parts:
        self.errorWrite("Error: No matching Packages to list\n")
        self.exit(1)
        return
    rows = [row for _, part in parts for row in part]
    columns = tpot_dnf_columns([tpot_dnf_lengths(f"{n}.{e['arch']}" for n, e, _ in rows),
                                tpot_dnf_lengths(tpot_yum_plain(e["evr"]) for _, e, _ in rows),
                                tpot_dnf_lengths(r for _, _, r in rows)], remainder_column=1)
    out = []
    for title, part in parts:
        out.append(title + "\n")
        for name, entry, repo in part:
            out.append(tpot_dnf_fmt(((f"{name}.{entry['arch']}", -columns[0]),
                                     (tpot_yum_plain(entry["evr"]), -columns[1]), (repo, -columns[2]))) + "\n")
    self.write("".join(out))
    self.exit()


def _tpot_yum_info(self):
    tpot_yum_head(self)
    installed, available = tpot_yum_matching(self, self.tpot_words[1:])
    if not self.tpot_words[1:]:
        available = available[:50]
    blocks = []
    for title, part in (("Installed Packages", installed), ("Available Packages", available)):
        if not part:
            continue
        blocks.append(title + "\n")
        for name, entry in part:
            epoch, _, plain = entry["evr"].partition(":")
            version, _, release = plain.rpartition("-")
            installed_one = title.startswith("Installed")
            lines = [f"Name        : {name}", f"Arch        : {entry['arch']}"]
            if epoch != "0":
                lines.append(f"Epoch       : {epoch}")
            # an installed package: its installed size and the repository it came from; else the download
            size = entry.get("isize") or entry["size"] * 2 if installed_one else entry["size"]
            lines += [f"Version     : {version}", f"Release     : {release}", f"Size        : {tpot_dnf_number(size)}"]
            if installed_one:
                lines += ["Repo        : installed", f"From repo   : {entry.get('from', '@anaconda').lstrip('@')}"]
            else:
                # the id of repolist: the vault baseurl names $basearch only (centos:7 with this repo file)
                lines.append(f"Repo        : {entry['repo']}/x86_64")
            lines.append(f"Summary     : {entry['summary']}")
            if entry.get("url"):
                lines.append(f"URL         : {entry['url']}")
            if entry.get("license"):
                lines.append(f"License     : {entry['license']}")
            lines.append(textwrap.fill(entry.get("description") or entry["summary"], width=TPOT_YUM_WIDTH,
                                       initial_indent="Description : ", subsequent_indent="            : "))
            blocks.append("\n".join(lines) + "\n\n")
    if not blocks:
        self.errorWrite("Error: No matching Packages to list\n")
        self.exit(1)
        return
    self.write("".join(blocks))
    self.exit()


def _tpot_yum_search(self):
    terms = [t.lower() for t in self.tpot_words[1:]]
    tpot_yum_head(self)
    if not terms:
        self.errorWrite("Error: Need an item to match\n")
        self.exit(1)
        return
    names = sorted(set(tpot_yum_meta()) | set(tpot_yum_db(self.protocol)))
    named, summed = [], []
    for name in names:
        entry = tpot_yum_db(self.protocol).get(name) or tpot_yum_entry(name)
        if all(t in name.lower() or t in entry["summary"].lower() for t in terms):
            (named if all(t in name.lower() for t in terms) else summed).append((name, entry))
    if not named and not summed:
        self.write(f"Warning: No matches found for: {' '.join(self.tpot_words[1:])}\nNo matches found\n")
        self.exit()
        return
    out = []
    for title, part in ((f"N/S matched: {', '.join(self.tpot_words[1:])}", named + summed),):
        out.append(tpot_dnf_section(title) + "\n")
        for name, entry in part[:2000]:
            key = f"{name}.{entry['arch']} : "
            out.append(textwrap.fill(entry["summary"], width=TPOT_YUM_WIDTH, initial_indent=key,
                                          subsequent_indent=" " * (len(key) - 2) + ": ") + "\n")
    out.append("\n  Name and summary matches only, use \"search all\" for everything.\n")
    self.write("".join(out))
    self.exit()


def tpot_yum_match_block(cmd, name: str, entry: dict, repo: str, matched: str | None, term: str) -> str:
    """T-Pot: one package of yum 3's provides (matchcallback with showdupesfromrepos): the package with its
    summary, its repository, what matched (nothing for its own name), three blank lines; the term bold on a
    terminal."""
    key = f"{tpot_yum_nevra(name, entry)} : "
    line = textwrap.fill(entry["summary"] or name, width=TPOT_YUM_WIDTH, initial_indent=key,
                         subsequent_indent=" " * (len(key) - 2) + ": ")
    if term and not set(term) & set("*?[") and tpot_yum_bold(cmd, term) != term:
        line = re.sub(re.escape(term), lambda found: tpot_yum_bold(cmd, found.group(0)), line, flags=re.IGNORECASE)
    text = f"{line}\nRepo        : {repo}\n"
    if matched:
        shown = tpot_yum_bold(cmd, matched) if matched == term else matched
        text += "Matched from:\n" + (f"Filename    : {shown}\n" if matched.startswith("/") else
                                     f"Provides    : {shown}\n")
    return text + "\n\n\n"


@tpot_functools.cache
def tpot_yum_pools() -> tuple[tuple, tuple]:
    """T-Pot: (names and provided names, programs) of the release, sorted, for the globs of provides."""
    lists = tpot_yum_lists()
    names, provided = (lists[0], lists[1]) if lists is not None else (frozenset(tpot_yum_meta()), {})
    return (tuple(sorted(set(names) | {k for k in provided if not k.startswith("/")})),
            tuple(sorted(k for k in provided if k.startswith("/"))))


def tpot_yum_providers(cmd, term: str) -> list:
    """T-Pot: [(package, what matched or None)] of a term: a package of that name, the package that lists the
    program or provides the name, the session's own programs; globs over names and programs."""
    lists = tpot_yum_lists()
    names, provided = (lists[0], lists[1]) if lists is not None else (frozenset(tpot_yum_meta()), {})
    session = {program: package for package, entry in tpot_yum_db(cmd.protocol).items()
               for program in entry.get("programs") or [] if program not in provided}

    def owner(key):
        return provided.get(key) or session.get(key)

    found = []
    if set(term) & set("*?["):
        if len(term) > 128:
            return []
        pool = heapq.merge(tpot_yum_pools()[1], sorted(session)) if "/" in term else tpot_yum_pools()[0]
        for key in tpot_scanned(cmd.protocol, pool):
            if fnmatch.fnmatchcase(key, term):
                found.append((key if key in names else owner(key), None if key in names else key))
            if len(found) >= TPOT_YUM_GLOB_MATCHES:
                break
    elif term in names:
        found.append((term, None))
    elif owner(term):
        found.append((owner(term), term))
    seen, result = set(), []
    for item in found:
        if item not in seen and item[0]:
            seen.add(item)
            result.append(item)
    return result


def _tpot_yum_provides(self):
    """T-Pot: yum 3 provides / whatprovides (centos:7): the packages of the repositories first, then the
    installed ones from the repository they came from; "No matches found" when no term matched."""
    terms = self.tpot_words[1:]
    if not terms:
        tpot_yum_head(self, mirrors=False)
        self.errorWrite("Error: Need an item to match\n Mini usage:\n\nprovides SOME_STRING\n\n"
                        "Find what package provides the given value\n\nalias: whatprovides\n")
        self.exit(1)
        return
    tpot_yum_head(self)
    db = tpot_yum_db(self.protocol)
    available, installed = [], []
    for term in terms:
        for name, matched in tpot_yum_providers(self, term):
            available.append(tpot_yum_match_block(self, name, tpot_yum_entry(name), tpot_yum_entry(name)["repo"],
                                                  matched, term))
            if name in db:
                installed.append(tpot_yum_match_block(self, name, db[name], db[name].get("from", "@anaconda"),
                                                      matched, term))
    if not available:
        self.write("No matches found\n")
    self.write("".join(available + installed))
    self.exit()


def tpot_yum_history(cmd) -> list:
    """T-Pot: the transactions of yum on the session: the installation (no login user, a week before the boot,
    every package of the persona), then the session's own."""
    history = getattr(cmd.protocol, "tpot_yum_history", None)
    if history is None:
        boot = getattr(cmd.protocol, "boot_time", None)
        history = [{"user": None, "line": "", "time": (boot() if boot else time.time()) - 7 * 86400, "action": "Install",
                    "count": len(tpot_yum_installed())}]
        try:
            cmd.protocol.tpot_yum_history = history
        except AttributeError:
            pass
    return history


def _tpot_yum_history(self):
    """T-Pot: yum 3 history [list] (history_list_view single-user-commands of CentOS 7): the command lines
    while one user made every transaction, else the users; newest first."""
    tpot_yum_head(self, mirrors=False)
    if not tpot_privileged(self):
        if "ovl" in tpot_yum_plugins():
            self.errorWrite("ovl: Error while doing RPMdb copy-up:\n"
                            "[Errno 13] Permission denied: '/var/lib/rpm/Sigmd5'\n")
        self.errorWrite("You don't have access to the history DB.\n")
        self.exit(1)
        return
    history = tpot_yum_history(self)
    single = len({entry["user"] for entry in history}) == 1
    rows = [f"{'ID':<6} | {'Command line' if single else 'Login user':<24} | {'Date and time':<16} | "
            f"{'Action(s)':<14} | Altered", "-" * 79]
    for number, entry in reversed(list(enumerate(history, 1))):
        who = entry["line"] if single else (f"{entry['user']} <{entry['user']}>" if entry["user"] else
                                            "System <unset>")
        when = time.strftime("%Y-%m-%d %H:%M", time.gmtime(entry["time"]))
        rows.append(f"{number:6d} | {who[:24]:<24} | {when:<16} | {entry['action']:<14} | {entry['count']:4d}   ")
    self.write("\n".join(rows) + "\nhistory list\n")
    self.exit()


def tpot_rpm_unplace(cmd, name: str, entry: dict) -> None:
    """T-Pot: the programs of a package rpm or yum removes leave the session's tree and its commands: those the
    session installed, else those rpm -qf names it the owner of."""
    # the programs the session installed and every file rpm -qf names it the owner of
    programs = list(dict.fromkeys(list(entry.get("programs") or []) + [path for path, owner in tpot_rpm_owners().items()
                                                                       if owner == name]))
    for manual in tpot_rpm_manuals().get(name, []):
        try:
            if cmd.fs.exists(manual):
                cmd.fs.remove(manual)
        except Exception:  # noqa: BLE001 - a page the session cannot remove stays
            continue
    for program in programs:
        try:
            if cmd.fs.exists(program):
                cmd.fs.remove(program)
        except Exception:  # noqa: BLE001 - a program the session cannot remove stays
            continue
        short = program.rsplit("/", 1)[-1]
        for spelling in (program, short, f"/bin/{short}", f"/usr/bin/{short}", f"/sbin/{short}", f"/usr/sbin/{short}"):
            cmd.protocol.commands.pop(spelling, None)
            (getattr(cmd.protocol, "tpot_faked", None) or {}).pop(spelling, None)


def tpot_yum_faked(name: str):
    """T-Pot: the program of a package yum 3 installed in the session (the faked program of Cowrie's apt)."""
    class Command_faked_installation(HoneyPotCommand):
        def call(self) -> None:
            self.write(f"{name}: Segmentation fault\n")

    return Command_faked_installation


Command_yum.tpot_install3 = inlineCallbacks(_tpot_yum_install)
Command_yum.tpot_run3 = inlineCallbacks(_tpot_yum_run)
Command_yum.tpot_remove3 = inlineCallbacks(_tpot_yum_remove)
Command_yum.lineReceived = inlineCallbacks(_tpot_yum_line)
Command_yum.tpot_list3 = _tpot_yum_list
Command_yum.tpot_info3 = _tpot_yum_info
Command_yum.tpot_search3 = _tpot_yum_search
Command_yum.tpot_provides3 = _tpot_yum_provides
Command_yum.tpot_repolist3 = _tpot_yum_repolist
Command_yum.tpot_cache3 = _tpot_yum_cache
Command_yum.tpot_history3 = _tpot_yum_history
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


@functools.cache
def tpot_dnf_names() -> tuple[dict, dict]:
    """({name: repository} of the names of the RHEL 9 repositories, {name or program: package} of what
    packages provide) ([shell] package_names: "name repo" and "provided > package")."""
    path = CowrieConfig.get("shell", "package_names", fallback="")
    names, provides = {}, {}
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines() if path else []
    except (OSError, ValueError):
        lines = []
    for line in lines:
        row = line.split(" ")
        if len(row) == 2:
            names[row[0]] = row[1]
        elif len(row) == 3 and row[1] == ">":
            provides[row[0]] = row[2]
    return names, provides


@functools.cache
def tpot_dnf_meta() -> dict:
    """{name: [arch, epoch:version-release, repo, size, installed size, summary, url, license, group,
    description, source rpm]} of the RHEL 9 repositories ([shell] dnf_meta; the builds of AlmaLinux 9.4)."""
    return tpot_rpm_meta_file(CowrieConfig.get("shell", "dnf_meta", fallback=""))


def tpot_dnf_listed(name: str) -> dict:
    """An available package of the repositories without data of its own: the repositories' build ([shell]
    dnf_meta), the persona's repository of its name; without a build a version that fits RHEL 9 (the same
    for a name on every call; unverified)."""
    return dict(tpot_dnf_listed_once(name))


@functools.cache
def tpot_dnf_paths() -> dict:
    """{package: [programs]} of the programs the repositories list ([shell] package_names "path > package")."""
    found: dict = {}
    for path, package in tpot_dnf_names()[1].items():
        if path.startswith("/"):
            found.setdefault(package, []).append(path)
    return found


@functools.cache
def tpot_dnf_listed_once(name: str) -> dict:
    repo = tpot_dnf_names()[0].get(name, "baseos")
    repo_id = next((r["id"] for r in tpot_dnf_data()["repos"] if f"-{repo}" in r["id"] and r.get("enabled")), repo)
    programs = sorted(tpot_dnf_paths().get(name, [])) or [f"/usr/bin/{name}"]
    meta = tpot_dnf_meta().get(name)
    if meta:
        epoch, _, plain = meta[1].partition(":")
        return {"evr": plain if epoch == "0" else meta[1], "arch": meta[0], "repo": repo_id, "summary": meta[5],
                "download": meta[3], "size": meta[4], "requires": [], "programs": programs}
    d = tpot_dnf_digest(f"listed {name}")
    size = 30_000 + int.from_bytes(d[:3], "big") % 900_000
    return {"evr": f"{1 + d[3] % 5}.{d[4] % 20}.{d[5] % 10}-{1 + d[6] % 12}.el9", "arch": "x86_64", "repo": repo_id,
            "summary": "", "download": size, "size": size * 3, "requires": [], "programs": programs}


def tpot_dnf_pool(protocol, patterns: list) -> dict:
    """The packages of the repositories' names that are not installed and have no data of their own, of the
    patterns (every one without)."""
    installed, data = tpot_dnf_installed(protocol), tpot_dnf_data()["available"]
    names = tpot_dnf_names()[0]
    return {name: tpot_dnf_listed(name) for name in (tpot_scanned(protocol, names) if patterns else names)
            if name not in installed and name not in data
            and (not patterns or any(fnmatch.fnmatchcase(name, p) for p in patterns))}


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


# dnf marks only on the terminal of the session; root, su to root or sudo (the rules of the top of yum.py)
tpot_dnf_tty = tpot_terminal
tpot_dnf_privileged = tpot_privileged


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
        elif command == "info":
            self.tpot_info(names)
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
        """(kind, name, entry) of a package argument: installed / available (the persona's data, then the
        names of the repositories, [shell] package_names), None without a match."""
        name = arg
        for arch in (".x86_64", ".noarch"):
            if name.endswith(arch):
                name = name[: -len(arch)]
        names, provides = tpot_dnf_names()
        name = tpot_dnf_data()["provides"].get(name, name)
        installed, available = tpot_dnf_installed(self.protocol), tpot_dnf_available(self.protocol)
        if name not in installed and name not in available and name not in names:
            name = provides.get(name, name)
        if name in installed:
            return "installed", name, installed[name]
        if name in available:
            return "available", name, available[name]
        if name in names:
            return "available", name, tpot_dnf_listed(name)
        return None

    def tpot_expand(self, args: list) -> list:
        """The arguments with their globs taken as the names they match (installed, data, repositories)."""
        found = []
        for arg in args:
            if set(arg) & set("*?[") and "/" not in arg:
                pool = sorted(set(tpot_dnf_installed(self.protocol)) | set(tpot_dnf_data()["available"])
                              | set(tpot_dnf_names()[0]))
                hits = [name for name in tpot_scanned(self.protocol, pool) if fnmatch.fnmatchcase(name, arg)][:64]
                found += hits or [arg]
            else:
                found.append(arg)
        return found

    @inlineCallbacks
    def tpot_install(self, names: list, reinstall: bool = False):
        self.tpot_metadata()
        missing, wanted, again = [], {}, {}
        for arg in self.tpot_expand(names):
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
        installed = tpot_dnf_installed(self.protocol)
        listed = tpot_dnf_names()[0]

        def candidate(dep):
            # a package of the data, else one of the repositories' names
            if dep in available:
                return available[dep]
            return tpot_dnf_listed(dep) if dep in listed else None

        deps: dict = {}
        weak: dict = {}
        # the requires of the data and the dependencies of a covered package as dnf named them (refs t12
        #   almalinux-9-t12r2-install-*)
        todo = [dep for name, entry in wanted.items() for dep in list(entry.get("requires", [])) + tpot_package_with(name)]
        while todo and len(deps) < 256:
            dep = todo.pop(0)
            found = candidate(dep)
            if found is not None and dep not in wanted and dep not in deps and dep not in installed:
                deps[dep] = found
                todo += found.get("requires", [])
        for dep in [dep for name in wanted for dep in tpot_package_weak(name)][:64]:
            found = candidate(dep)
            if found is not None and dep not in wanted and dep not in deps and dep not in installed:
                weak[dep] = found
        # the files rpm -ql names of a covered package: its programs and unit files
        for table in (wanted, deps, weak):
            for name, entry in list(table.items()):
                files = tpot_package_files(name)
                if files:
                    table[name] = dict(entry, programs=sorted(set(entry.get("programs", [])) | set(files)))
        sections = [("Reinstalling", sorted(again.items()))] if again else []
        sections += [("Installing", sorted(wanted.items())), ("Installing dependencies", sorted(deps.items()))]
        if weak:
            sections.append(("Installing weak dependencies", sorted(weak.items())))
        self.tpot_pending = ("install" if wanted else "reinstall", sections)
        self.tpot_table(sections, "download")
        counts = [("Install", len(wanted) + len(deps) + len(weak)), ("Reinstall", len(again))]
        self.tpot_summary(counts)
        packages = [entry for _, rows in sections for _, entry in rows]
        self.tpot_out(f"Total download size: {tpot_dnf_number(sum(e.get('download', e.get('size', 0)) for e in packages))}\n")
        if wanted or deps:
            self.tpot_out(f"Installed size: {tpot_dnf_number(sum(e.get('size', 0) for _, e in list(wanted.items()) + list(deps.items()) + list(weak.items())))}\n")
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
        # the packages that require one that goes go too; the dependencies the session installed with them that
        #   nothing else needs follow (refs t12 almalinux-9-t12r3-remove-nginx-core)
        def needs(name):
            return set(installed.get(name, {}).get("requires", [])) | set(tpot_package_with(name))

        dependent, todo = {}, list(wanted)
        while todo and len(dependent) < 64:
            gone = todo.pop()
            for other in sorted(installed):
                if other not in wanted and other not in dependent and gone in needs(other):
                    dependent[other] = dict(installed[other], repo=installed[other].get("from", "@System"))
                    todo.append(other)
        going = set(wanted) | set(dependent)
        own = tpot_dnf_session(self.protocol)["installed"]
        unused = {}
        for name in sorted(going):
            for dep in (tpot_package_with(name) + tpot_package_weak(name))[:64]:
                entry = own.get(dep)
                if (entry and entry.get("reason") == "dependency" and dep not in going and dep not in unused
                        and not any(dep in needs(other) for other in installed if other not in going and other != dep)):
                    unused[dep] = dict(entry, repo=entry.get("from", "@System"))
        everything = {**wanted, **dependent, **unused}
        sections = [("Removing", sorted(wanted.items())), ("Removing dependent packages", sorted(dependent.items())),
                    ("Removing unused dependencies", sorted(unused.items()))]
        self.tpot_pending = ("remove", sections)
        self.tpot_table(sections, "size")
        self.tpot_summary([("Remove", len(everything))])
        self.tpot_out(f"Freed space: {tpot_dnf_number(sum(e.get('size', 0) for e in everything.values()))}\n")
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
        # the deps first, as rpm orders them; a removal erases the packages that require others first
        first = ("Installing dependencies", "Installing weak dependencies", "Removing dependent packages")
        order = [row for row in rows if row[0] in first] + [row for row in rows if row[0] not in first]
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
                    # the programs this transaction made before go again: rpm leaves no files of it
                    for done in made:
                        try:
                            self.fs.remove(done)
                        except Exception:  # noqa: BLE001 - a file the session cannot remove stays
                            pass
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
        # the scriptlets of systemd: disable --now before a package goes, preset after it came (their systemctl
        #   lines, each with a blank line after it; refs t12 almalinux-9-t12-remove-*, -install-*)
        scripts = {name: tpot_package_units(self, name, "remove" if kind == "remove" else "install")
                   for title, name, _ in order if title != "Reinstalling"}
        for number, (title, name, entry) in enumerate(order, 1):
            if kind == "remove" and scripts.get(name):
                lines.append(f"  {'Running scriptlet':<17}: {tpot_dnf_nevra(name, entry):<{wid2}}   "
                             f"{done.format(number)}\n{scripts[name]}\n")
            verb = "Reinstalling" if title == "Reinstalling" else action[kind]
            lines.append(f"  {verb:<17}: {tpot_dnf_nevra(name, entry):<{wid2}}   {done.format(number)}\n")
        last = order[-1]
        lines.append(f"  {'Running scriptlet':<17}: {tpot_dnf_nevra(last[1], last[2]):<{wid2}}   "
                     f"{done.format(len(order))}\n")
        if kind != "remove":
            lines += [text + "\n" for text in scripts.values() if text]
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
                tpot_rpm_unplace(self, name, session["installed"].get(name, {}))
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
                tpot_rpm_place_manuals(self, name)
                session["installed"][name] = {"evr": entry["evr"], "arch": entry["arch"],
                                              "from": "@" + entry["repo"], "summary": entry.get("summary", ""),
                                              "size": entry.get("size", 0), "programs": entry.get("programs", []),
                                              "installtime": time.time(),
                                              "reason": "dependency" if title in first[:2] else "user"}
                session["removed"].discard(name)
        restore = getattr(self.protocol.commands, "tpot_restore", None)
        for program in made:
            if "/systemd/" in program:
                continue  # a unit file, no command
            # a program of the persona the session removed: Cowrie's own command again (TpotCommands)
            if restore is not None and restore(program):
                continue
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
        available = {**tpot_dnf_pool(self.protocol, names), **available}

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

    def tpot_info(self, names: list) -> None:
        """dnf info: the packages of the names (installed, then available) with the fields of the repositories."""
        self.tpot_metadata()
        installed = tpot_dnf_installed(self.protocol)
        available = {**tpot_dnf_pool(self.protocol, names), **tpot_dnf_available(self.protocol)}

        def hit(name):
            return not names or any(fnmatch.fnmatchcase(name, p) for p in names)

        out = []
        for title, part in (("Installed Packages", installed), ("Available Packages", available)):
            rows = sorted(name for name in part if hit(name))
            if not rows:
                continue
            out.append(title + "\n")
            for name in rows[:200]:
                entry = part[name]
                meta = tpot_dnf_meta().get(name)
                epoch, _, plain = entry["evr"].rpartition(":")
                version, _, release = plain.rpartition("-")
                if meta and meta[1].split(":", 1)[-1] != plain:
                    meta = None
                lines = [f"Name         : {name}"] + ([f"Epoch        : {epoch}"] if epoch and epoch != "0" else []) + [
                    f"Version      : {version}", f"Release      : {release}", f"Architecture : {entry['arch']}",
                    f"Size         : {tpot_dnf_number(entry.get('download', entry.get('size', 0)))}",
                    f"Source       : {(meta[10] if meta and meta[10] else f'{name}-{plain}.src.rpm')}"]
                if title.startswith("Installed"):
                    lines += ["Repository   : @System", f"From repo    : {tpot_dnf_repo(entry)}"]
                else:
                    lines.append(f"Repository   : {entry['repo']}")
                lines.append(f"Summary      : {entry.get('summary', '')}")
                if meta and meta[6]:
                    lines.append(f"URL          : {meta[6]}")
                if meta and meta[7]:
                    lines.append(f"License      : {meta[7]}")
                text = (meta[9] if meta and meta[9] else entry.get("summary", "")).splitlines() or [""]
                lines.append(f"Description  : {text[0]}")
                lines += [f"             : {line}" for line in text[1:]]
                out.append("\n".join(lines) + "\n\n")
        if not out:
            self.errorWrite("Error: No matching Packages to list\n")
            self.exit(1)
            return
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
        packages = {**tpot_dnf_pool(self.protocol, []), **tpot_dnf_available(self.protocol),
                    **tpot_dnf_installed(self.protocol)}
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


def tpot_rpm_installed(protocol) -> dict:
    """The installed packages of the session for rpm: dnf's on RHEL 9, yum 3's on CentOS 7."""
    if CowrieConfig.get("shell", "dnf_packages", fallback=""):
        return tpot_dnf_installed(protocol)
    return {name: {"evr": entry["evr"], "arch": entry["arch"], "programs": entry.get("programs", []),
                   "summary": entry.get("summary", ""), "size": entry.get("isize") or entry.get("size", 0) * 2,
                   "installtime": entry.get("installtime")} for name, entry in tpot_yum_db(protocol).items()}


@functools.cache
def tpot_rpm_owners() -> dict:
    """{path: package} of the system's programs ([shell] rpm_owners of CentOS 7: "path package"; dnf's on RHEL 9)."""
    if CowrieConfig.get("shell", "dnf_packages", fallback=""):
        return dict(tpot_dnf_data()["owners"])
    path = CowrieConfig.get("shell", "rpm_owners", fallback="")
    try:
        rows = [line.split(" ") for line in Path(path).read_text(encoding="utf-8").splitlines()] if path else []
    except (OSError, ValueError):
        rows = []
    return {row[0]: row[1] for row in rows if len(row) == 2}


@functools.cache
def tpot_rpm_manuals() -> dict:
    """{package: [manual pages]} of the programs of the release's packages, as the repositories' filelists name
    them ([shell] rpm_manuals: "path package")."""
    path = CowrieConfig.get("shell", "rpm_manuals", fallback="")
    found: dict = {}
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines() if path else []
    except (OSError, ValueError):
        lines = []
    for line in lines:
        row = line.split(" ")
        if len(row) == 2:
            found.setdefault(row[1], []).append(row[0])
    return found


def tpot_rpm_files(cmd, name: str, entry: dict) -> list:
    """rpm -ql of a package: its programs (of the session's install or of rpm -qf's owners), its documents and the
    manual pages of its programs that the repositories name and the session's tree holds (/usr/share/doc/<name>-
    <version> on CentOS 7, /usr/share/doc/<name> on RHEL 9)."""
    programs = sorted(entry.get("programs") or [path for path, owner in tpot_rpm_owners().items() if owner == name])
    plain = entry["evr"].split(":", 1)[-1]
    centos = not CowrieConfig.get("shell", "dnf_packages", fallback="")
    doc = f"/usr/share/doc/{name}-{plain.rpartition('-')[0]}" if centos else f"/usr/share/doc/{name}"
    manuals = []
    for manual in sorted(tpot_rpm_manuals().get(name, [])):
        try:
            if cmd.fs.exists(manual):
                manuals.append(manual)
        except Exception:  # noqa: BLE001 - a page the tree cannot look up is not listed
            continue
    return programs + [doc] + manuals


def tpot_rpm_place_manuals(cmd, name: str) -> None:
    """The manual pages of a package the session installs go into its tree."""
    for manual in tpot_rpm_manuals().get(name, []):
        try:
            if not cmd.fs.exists(manual):
                cmd.fs.mkfile(manual, 0, 0, random.randint(1500, 9000), 33188)
        except Exception:  # noqa: BLE001 - a page the tree takes no more files for stays absent
            continue


def tpot_rpm_owner(owners: dict, given: str, path: str):
    """The owner of a file by the path as given, its form below /usr or its form in /bin, /sbin and /lib."""
    short = re.sub(r"^/usr/(bin|sbin|lib|lib64)/", r"/\1/", path)
    return next((owners[p] for p in (given, path, short) if p in owners), None)


def tpot_rpm_info(cmd, name: str, entry: dict) -> str:
    """rpm -qi of the fields the persona knows (no signature, build date and host): the install date of the
    session's install (else a week before the boot), group, license, URL and description of the repositories."""
    epoch, _, plain = entry["evr"].rpartition(":")
    version, _, release = plain.rpartition("-")
    when = entry.get("installtime") or cmd.protocol.boot_time() - 7 * 86400
    centos = not CowrieConfig.get("shell", "dnf_packages", fallback="")
    meta = (tpot_yum_meta() if centos else tpot_dnf_meta()).get(name)
    group, url, licence, description = (meta[8], meta[6], meta[7], meta[9]) if meta else ("Unspecified", "", "", "")
    if meta and meta[1].split(":", 1)[-1] != plain:
        # another build than the installed one: its source package and text are not this one's
        meta, description = None, ""
    lines = [f"Name        : {name}"] + ([f"Epoch       : {epoch}"] if epoch and epoch != "0" else []) + [
        f"Version     : {version}", f"Release     : {release}", f"Architecture: {entry['arch']}",
        f"Install Date: {time.strftime('%a %b %e %H:%M:%S %Y', time.gmtime(when))}", f"Group       : {group}",
        f"Size        : {entry.get('size', 0)}"] + ([f"License     : {licence}"] if licence else []) + [
        f"Source RPM  : {(meta[10] if meta and meta[10] else f'{name}-{plain}.src.rpm')}"]
    if centos:
        lines += ["Relocations : (not relocatable)", "Packager    : CentOS BuildSystem <http://bugs.centos.org>",
                  "Vendor      : CentOS"]
    else:
        lines += [f"Packager    : {tpot_dnf_data().get('packager', '')}", "Vendor      : Red Hat, Inc."]
    if url:
        lines.append(f"URL         : {url}")
    summary = entry.get("summary", "") or (meta[5] if meta else "")
    return "\n".join(lines + [f"Summary     : {summary}", "Description :", description or summary]) + "\n"


class Command_rpm(HoneyPotCommand):
    """rpm of the persona: --version, -q / -qa / -qf / -qi (and query options as -q), -i / -U / -e."""

    def call(self) -> None:
        try:
            self.exit_code = self.tpot_rpm()
        except Exception:  # noqa: BLE001 - the command always exits
            self.exit_code = 1

    def tpot_rpm(self) -> int:
        data = dict(tpot_dnf_data())
        data.setdefault("rpm_version", CowrieConfig.get("shell", "rpm_version", fallback="4.16.1.3"))
        if not CowrieConfig.get("shell", "dnf_packages", fallback=""):
            data["rpm_version"] = CowrieConfig.get("shell", "rpm_version", fallback="4.11.3")
        installed = tpot_rpm_installed(self.protocol)
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
                owners = dict(tpot_rpm_owners())
                for name, entry in installed.items():
                    for program in entry.get("programs", []):
                        owners[program] = name
            for word in words:
                if "f" in flags:
                    given = path = self.fs.resolve_path(word, self.cwd)
                    for top in ("/bin/", "/sbin/"):
                        if path.startswith(top):
                            path = "/usr" + path
                    short = re.sub(r"^/usr/(bin|sbin|lib|lib64)/", r"/\1/", path)
                    if not (self.fs.exists(path) or self.fs.exists(given) or self.fs.exists(short)):
                        # rpm names the file as it was given (centos:7: /bin/rbash)
                        self.errorWrite(f"error: file {given}: No such file or directory\n")
                        failed += 1
                    elif tpot_rpm_owner(owners, given, path) in installed:
                        # a program the package lists in /bin (CentOS 7: /bin/mail) or below /usr: /bin, /sbin and
                        # /lib are links to /usr, rpm 4.11 answers both forms (centos:7)
                        owner = tpot_rpm_owner(owners, given, path)
                        self.write(tpot_rpm_nvra(owner, installed[owner]) + "\n")
                    else:
                        self.write(f"file {path} is not owned by any package\n")
                        failed += 1
                elif word in installed and "i" in flags:
                    self.write(tpot_rpm_info(self, word, installed[word]))
                elif word in installed and "l" in flags:
                    self.write("".join(f"{path}\n" for path in tpot_rpm_files(self, word, installed[word])))
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
                    tpot_rpm_unplace(self, word, installed[word])
                    # the preun scriptlet of systemd (disable --now): its systemctl lines on stderr
                    self.errorWrite(tpot_package_units(self, word, "remove"))
                if word in installed and not CowrieConfig.get("shell", "dnf_packages", fallback=""):
                    tpot_yum_db(self.protocol).pop(word, None)
                elif word in installed:
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
                 'commands["/usr/bin/yum"] = Command_yum\ncommands["yum"] = Command_yum\n' + YUM3_COMMAND
                 + DNF_COMMAND + TPOT_PACKAGE_HOOK
                 + tpot_metering("Command_yum", "Command_dnf", "Command_dnf_yum", "Command_rpm"))


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

    def tpot_tasks(self, session: int | None = None) -> int:
        """T-Pot: the tasks of the system: the ps list, the session's processes up to the shell
        (session: sshd and its monitor, in.telnetd and login; by default those of tpot_session), the
        running command and the kernel threads ([shell] kernel_threads), as ps -e and top count them."""
        try:
            threads = max(0, CowrieConfig.getint("shell", "kernel_threads", fallback=0))
        except ValueError:
            threads = 0
        if session is None:
            session = getattr(self, "tpot_session_size", None)
        if not isinstance(session, int):
            try:
                from cowrie.commands.base import tpot_session
                session = len(tpot_session(self, str(getattr(getattr(self, "user", None), "username", "root")))["chain"])
            except Exception:  # noqa: BLE001 - without the session's processes one
                session = 1
        return len(self.tpot_processes()) + max(0, session) + 1 + threads

    def tpot_new_pid(self, count: int = 1, now: float | None = None) -> int:
        """T-Pot: the first of count new PIDs (ps, top and the commands of their pipeline): after the
        newest process, never one of an earlier command again."""
        first = max(self.tpot_last_pid(now), getattr(self, "tpot_pid_used", 0) + 1)
        self.tpot_pid_used = first + max(1, count) - 1
        return first

    def tpot_last_pid(self, now: float | None = None) -> int:
        """T-Pot: the newest PID: a few after the newest process of the ps list, more the longer
        the session runs."""
        now = time.time() if now is None else now
        pids = [p["PID"] for p in self.tpot_processes() if isinstance(p.get("PID"), int)]
        since = max(0.0, now - getattr(self, "logintime", now))
        session = getattr(self, "tpot_session_pid", None)
        newest = max(pids + ([session + 1] if isinstance(session, int) else []) or [1000])
        return max(newest + 9 + int(since // 7), getattr(self, "tpot_pid_used", 0))

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


def tpot_parent(entry) -> int:
    """T-Pot: the PPID of a process of the ps list: the daemon that forked it (PPID of the list), else init."""
    parent = entry.get("PPID") if isinstance(entry, dict) else None
    return parent if isinstance(parent, int) and not isinstance(parent, bool) and parent > 0 else 1


'''

# module code of the patched base.py (ps, top) and service.py (systemctl): the CPU time of each process, the
# one of ps, top and the CGroup of its unit
TPOT_CPU = r'''

def tpot_process_seconds(host: str, pid: int, rss: int, uptime: float, login: bool) -> float:
    """T-Pot: the CPU time of a process of the system, the same in ps, top and systemctl: a login shell a few
    hundredths, a daemon or kernel thread a small share of the uptime (more with its memory)."""
    draw = int.from_bytes(hashlib.sha256(f"{host}\0{pid}\0cpu".encode("utf-8")).digest()[:8], "big")
    if login:
        return 0.01 + draw % 7 / 100
    return uptime * (50 + draw % 950) / 1e7 * (1 + rss / 20000)


def tpot_listed_seconds(host: str, entry: dict, boot: float, now: float) -> float:
    """T-Pot: the CPU time of a process of the ps list since its start (a unit started in the session next to
    none; a getty waits on its console, as idle as a login shell)."""
    try:
        pid = int(entry["PID"])
    except (KeyError, TypeError, ValueError):
        return 0.0
    command, tty = str(entry.get("COMMAND") or ""), str(entry.get("TTY") or "?")
    login = (tty.startswith(("pts/", "tty")) and "getty" not in command) or "getty" in command
    exact = entry.get("STARTED")
    if isinstance(exact, (int, float)) and not isinstance(exact, bool):
        # a process the session started (systemctl start): the time since then, a hundredth of a daemon's share
        return tpot_process_seconds(host, pid, int(entry.get("RSS") or 0), max(0.0, now - float(exact)), login) / 100
    started = tpot_ps_stamp(str(entry.get("START") or ""), boot, now, host, pid)
    return tpot_process_seconds(host, pid, int(entry.get("RSS") or 0),
                                min(max(1.0, now - boot), max(1.0, now - started)), login)


def tpot_ps_start(when: float, now: float) -> str:
    """START / STIME (procps pr_stime): HH:MM today, MonDD this year, else the year (UTC)."""
    started, today = time.gmtime(when), time.gmtime(now)
    if started.tm_year != today.tm_year:
        return time.strftime("%Y", started)
    if started.tm_yday != today.tm_yday:
        return time.strftime("%b%d", started)
    return time.strftime("%H:%M", started)


def tpot_ps_rendered(boot: float, now: float) -> float:
    """When the persona's ps list was made: at the start of Cowrie, [honeypot] boot_offset after the boot (the
    start renders the persona then); now without the key."""
    try:
        offset = float(str(CowrieConfig.get("honeypot", "boot_offset", fallback="") or "-1"))
    except (TypeError, ValueError):
        offset = -1.0
    return min(now, boot + offset) if offset >= 0 else now


def tpot_ps_stamp(text: str, boot: float, now: float, host: str, pid: int) -> float:
    """When a process of the persona's list started, from its START (HH:MM of the day the list was made, MonDD of
    its year, a year; at boot if that is its START), so that ELAPSED, STARTED and START agree, also after midnight
    or the new year."""
    rendered = tpot_ps_rendered(boot, now)
    if not text or text in (tpot_ps_start(boot, rendered), tpot_ps_start(boot, now)):
        return boot
    draw = int.from_bytes(hashlib.sha256(f"{host}\0{pid}\0start".encode("utf-8")).digest()[:4], "big")
    today = time.gmtime(rendered)
    try:
        if re.fullmatch(r"\d\d:\d\d", text):
            day = calendar.timegm((today.tm_year, today.tm_mon, today.tm_mday, 0, 0, 0))
            return min(now, max(boot, day + int(text[:2]) * 3600 + int(text[3:]) * 60 + draw % 60))
        if re.fullmatch(r"[A-Z][a-z]{2}\d\d", text):
            month = TPOT_PS_MONTHS.index(text[:3]) + 1
            day = calendar.timegm((today.tm_year, month, int(text[3:]), 0, 0, 0))
            return min(now, max(boot, day + draw % 86400))
        if re.fullmatch(r"\d{4}", text):
            return min(now, max(boot, calendar.timegm((int(text), 1, 1, 0, 0, 0)) + draw % (300 * 86400)))
    except (ValueError, OverflowError):
        pass
    return boot


TPOT_PS_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
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
# the long options of top of procps-ng 4 and the usage of -h / --help (refs final-wave *-ffw-top-h, 4.0.2 and 4.0.4)
TPOT_TOP_LONG = {"batch-mode": "b", "cmdline-toggle": "c", "delay": "d", "scale-summary-mem": "E",
                 "scale-task-mem": "e", "threads-show": "H", "idle-toggle": "i", "iterations": "n",
                 "list-fields": "O", "sort-override": "o", "pid": "p", "accum-time-toggle": "S", "secure-mode": "s",
                 "filter-any-user": "U", "filter-only-euser": "u", "width": "w", "single-cpu-toggle": "1",
                 "help": "h", "version": "V"}
TPOT_TOP_USAGE4 = ("\nUsage:\n top [options]\n\nOptions:\n"
                   " -b, --batch-mode                run in non-interactive batch mode\n"
                   " -c, --cmdline-toggle            reverse last remembered 'c' state\n"
                   " -d, --delay =SECS [.TENTHS]     iterative delay as SECS [.TENTHS]\n"
                   " -E, --scale-summary-mem =SCALE  set mem as: k,m,g,t,p,e for SCALE\n"
                   " -e, --scale-task-mem =SCALE     set mem with: k,m,g,t,p for SCALE\n"
                   " -H, --threads-show              show tasks plus all their threads\n"
                   " -i, --idle-toggle               reverse last remembered 'i' state\n"
                   " -n, --iterations =NUMBER        exit on maximum iterations NUMBER\n"
                   " -O, --list-fields               output all field names, then exit\n"
                   " -o, --sort-override =FIELD      force sorting on this named FIELD\n"
                   " -p, --pid =PIDLIST              monitor only the tasks in PIDLIST\n"
                   " -S, --accum-time-toggle         reverse last remembered 'S' state\n"
                   " -s, --secure-mode               run with secure mode restrictions\n"
                   " -U, --filter-any-user =USER     show only processes owned by USER\n"
                   " -u, --filter-only-euser =USER   show only processes owned by USER\n"
                   " -w, --width [=COLUMNS]          change print width [,use COLUMNS]\n"
                   " -1, --single-cpu-toggle         reverse last remembered '1' state\n"
                   "\n -h, --help                      display this help text, then exit\n"
                   " -V, --version                   output version information & exit\n"
                   "\nFor more details see top(1).\n")
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
        self.pipe_quit = False
        self.began = time.time()
        # the PID of this top, a new one
        # the commands after top in its pipeline run with it (top -bn1 | head: a head row)
        self.stages = tpot_pipeline_words(self)
        self.pid = self.protocol.tpot_new_pid(1 + len(self.stages), self.began)
        self.interactive = False
        self.restored = False
        self.limit = 0
        self.delay = 5.0 if self.style == "busybox" else 3.0
        self.users: set[str] = set()
        self.pids: set[int] = set()
        if not self.tpot_options():
            return
        self.window = tpot_window(self)
        # a pipe on stdin (echo q | top): procps needs the terminal there, BusyBox reads its keys from it
        piped = getattr(self, "input_data", None)
        piped = piped if isinstance(piped, bytes) else (b"" if getattr(self.pp, "stdin_from_pipe", False) else None)
        self.pipe_quit = piped is not None and b"q" in piped
        if piped is not None and not self.batch and self.style != "busybox":
            self.errorWrite("top: failed tty get\n")
            self.exit(1)
            return
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

    def tpot_flags(self) -> str:
        """The flags of top's usage line: -bcHiOSs of 3.3.10 (CentOS 7) and 3.3.12 (Debian 9), -bcEHiOSs1 of
        3.3.16 (Ubuntu 20.04), -bcEeHiOSs1 of 3.3.17 (Ubuntu 22.04, AlmaLinux 9); unverified between."""
        if self.version < (3, 3, 13):
            return "bcHiOSs"
        return "bcEHiOSs1" if self.version < (3, 3, 17) else "bcEeHiOSs1"

    def tpot_usage(self, error: str) -> None:
        if self.style == "busybox":
            self.errorWrite(f"top: {error}\nUsage: top [-b] [-nCOUNT] [-dSECONDS] [-m]\n")
        else:
            flags = self.tpot_flags()
            self.errorWrite(f"top: {error}\nUsage:\n  top -hv | -{flags} -d secs -n max -u|U user -p pid(s)"
                            " -o field -w [cols]\n")
        self.exit(1)

    def tpot_options4(self) -> bool:
        """The options of top of procps-ng 4 (getopt_long; refs final-wave *-ffw-top*): -h / --help the usage on
        stdout, -V / --version, the long names; an error is its line alone, exit 1 (4.0.2: 0 for an option getopt
        refuses)."""
        refused = 0 if self.version < (4, 0, 4) else 1

        def fail(text: str, code: int = 1) -> bool:
            self.errorWrite(f"top: {text}\n")
            self.exit(code)
            return False

        args = list(self.args)
        while args:
            arg = args.pop(0)
            if arg.startswith("--") and len(arg) > 2:
                name, eq, value = arg[2:].partition("=")
                found = [long for long in TPOT_TOP_LONG if long.startswith(name)]
                if name in TPOT_TOP_LONG:
                    found = [name]
                if len(found) != 1:
                    return fail(f"unrecognized option '{arg}'" if not found else f"option '{arg}' is ambiguous",
                                refused)
                letter = TPOT_TOP_LONG[found[0]]
                if letter in "dnpuUoEew":
                    if not eq and letter != "w":
                        if not args:
                            return fail(f"option '--{found[0]}' requires an argument", refused)
                        value = args.pop(0)
                    args.insert(0, f"-{letter}{value}" if value or letter != "w" else "-w")
                else:
                    args.insert(0, f"-{letter}")
                continue
            if not arg.startswith("-") or arg == "-":
                return fail(f"unknown option '{arg}'")
            letters = arg[1:]
            while letters:
                letter, letters = letters[0], letters[1:]
                if letter == "h":
                    self.write(TPOT_TOP_USAGE4)
                    self.exit(0)
                    return False
                if letter == "V":
                    self.write(f"top from procps-ng {self.version_text}\n")
                    self.exit(0)
                    return False
                if letter == "b":
                    self.batch = True
                    continue
                if letter in TPOT_TOP_FLAGS or letter == "O":
                    continue
                if letter not in ("d", "n") and letter not in TPOT_TOP_ARGUMENT:
                    return fail(f"invalid option -- '{letter}'", refused)
                value, letters = letters, ""
                if not value:
                    if letter == "w" and (not args or tpot_top_number(args[0], 0x7FFFFFFF) is None):
                        continue
                    if not args:
                        return fail(f"option requires an argument -- '{letter}'", refused)
                    value = args.pop(0)
                if not self.tpot_option(letter, value):
                    return False
        return True

    def tpot_options(self) -> bool:
        """-b, -n, -d, -p, -u / -U (filters) and the flags top takes; False when it ended."""
        self.batch = False
        if self.style != "busybox" and self.version >= (4, 0, 0):
            return self.tpot_options4()
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
                    # "version" up to 3.3.10 (CentOS 7), not in 3.3.12 (Debian 9) to 3.3.17
                    self.write(f"  procps-ng {'version ' if self.version < (3, 3, 11) else ''}{self.version_text}\n"
                               "Usage:\n  top -hv | -"
                               f"{self.tpot_flags()}"
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
        if self.frames >= self.limit or now - self.began >= TPOT_TOP_SECONDS or self.pipe_quit:
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
        # the screen reads its terminal raw: Ctrl-D is a key it ignores, the end of the input (the
        # channel's EOF, a pipe) does not end it; a batch top runs on
        if not self.interactive:
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
            login = tpot_login_entry(entry)
            if login:
                shell = pid
            # the CPU time since its start, as ps and systemctl count it
            seconds = tpot_listed_seconds(host, entry, now - uptime, now)
            rows.append({"pid": pid, "ppid": 0 if pid == 1 else tpot_parent(entry),
                         "user": tpot_top_user(user) if login else str(entry.get("USER") or "root"),
                         "stat": stat, "virt": int(entry.get("VSZ") or 0), "res": int(entry.get("RSS") or 0),
                         "mem": float(entry.get("MEM") or 0.0), "time": seconds,
                         "command": str(entry.get("COMMAND") or "")})
        for entry in tpot_kernel_threads():
            pid = entry["PID"]
            rows.append({"pid": pid, "ppid": int(entry.get("PPID", 0 if pid == 2 else 2)), "user": "root",
                         "stat": str(entry.get("STAT") or "S"), "virt": 0, "res": 0, "mem": 0.0,
                         "time": tpot_process_seconds(host, pid, 0, uptime, False) / 50,
                         "command": str(entry.get("COMMAND") or "[kworker/0:0]"), "kernel": True})
        last = self.pid
        daemon, chain = self.tpot_session(rows, user)
        # the session's processes from its PID on (tpot_session: the one of ps, ss and netstat), each
        # the child of the one before
        parent = daemon["pid"] if daemon else 1
        for process in chain:
            rows.append({"ppid": parent, "mem": 0.1, "time": 0.02, **process})
            parent = process["pid"]
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
        for offset, words in enumerate(getattr(self, "stages", []), 1):
            rows.append({"pid": last + offset, "ppid": shell or 1, "user": tpot_top_user(user), "stat": "S",
                         "virt": virt // 4, "res": res // 4, "mem": 0.0, "time": 0.0,
                         "command": " ".join(str(w) for w in words) or "sh"})
        if self.users:
            rows = [r for r in rows if r["user"] in self.users]
        if self.pids:
            rows = [r for r in rows if r["pid"] in self.pids]
        return rows

    def tpot_session(self, rows: list[dict], user: str) -> tuple[dict | None, list[dict]]:
        """The daemon of this session in the rows (or None) and the processes between it and the
        shell (tpot_session). BusyBox telnetd serves the connection itself: telnetd -> shell."""
        names = {tpot_top_comm(r["command"]): r for r in rows}
        session = tpot_session(self.protocol, user)
        chain = [{"pid": entry["pid"], "user": tpot_top_user(entry["user"]), "stat": entry["stat"],
                  "virt": entry["virt"], "res": entry["res"], "command": entry["command"]}
                 for entry in session["chain"]]
        if not chain:
            return next((r for r in rows if r["pid"] == session["pid"]), None), []
        if session["telnet"]:
            return names.get("telnetd") or names.get("utelnetd") or names.get("in.telnetd"), chain
        own = {entry["pid"] for entry in chain}
        daemon = next((r for r in rows if tpot_top_comm(r["command"]) in (session["name"], session["name"].split("-", 1)[0])
                       and r["pid"] not in own), None)
        return daemon, chain

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
        tasks = protocol.tpot_tasks(len(tpot_session(protocol, str(self.user.get("username", "root")))["chain"])) \
            + len(getattr(self, "stages", []))
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
            comm = r["command"].strip("[]")[:15] if r.get("kernel") else tpot_top_comm(r["command"])
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


# module code of the patched base.py after top: ps of the persona
PS_COMMAND = r'''
# T-Pot: ps of the persona. procps-ng ([shell] top_style procps, procps_version) with its column
# layout (show_one_proc: a value wider than its column pushes the next ones and is taken back by the
# padding of the next right-aligned one), the PID width of the system's pid_max, BSD and UNIX options;
# BusyBox ps without the DESKTOP option ([shell] top_style busybox, text_version / text_banner /
# text_libc) as the firmware of a device has it: PID USER VSZ STAT COMMAND, cut one column short of
# the terminal also into a pipe. The rows are those of top: the persona's list, the kernel threads
# ([shell] kernel_thread_list), the session's processes (tpot_session), the shell, ps itself and the
# commands after it in its pipeline (the shell's tpot_pipeline).
TPOT_PS_HELP = ("\nUsage:\n ps [options]\n\n Try 'ps --help <simple|list|output|threads|misc|all>'\n"
                "  or 'ps --help <s|l|o|t|m|a>'\n for additional help text.\n\nFor more details see ps(1).\n")
# name: header, width, right-aligned
TPOT_PS_FIELDS = {
    "user": ("USER", 8, False), "ruser": ("RUSER", 8, False), "euser": ("EUSER", 8, False),
    "uname": ("USER", 8, False), "uid_hack": ("UID", 8, False),
    "pid": ("PID", 0, True), "tgid": ("TGID", 0, True), "ppid": ("PPID", 0, True), "pgid": ("PGID", 0, True),
    "sid": ("SID", 0, True), "sess": ("SESS", 0, True),
    "%cpu": ("%CPU", 4, True), "pcpu": ("%CPU", 4, True), "%mem": ("%MEM", 4, True), "pmem": ("%MEM", 4, True),
    "vsz": ("VSZ", 6, True), "vsize": ("VSZ", 6, True), "rss": ("RSS", 5, True), "rssize": ("RSS", 5, True),
    "rsz": ("RSZ", 5, True),
    "tty": ("TT", 8, False), "tt": ("TT", 8, False), "tname": ("TTY", 8, False),
    "stat": ("STAT", 4, False), "s": ("S", 1, False), "state": ("S", 1, False),
    "start_time": ("START", 5, True), "stime": ("STIME", 5, True), "bsdstart": ("START", 6, True),
    "time": ("TIME", 8, True), "cputime": ("TIME", 8, True), "bsdtime": ("TIME", 6, True),
    "c": ("C", 2, True), "ni": ("NI", 3, True), "nice": ("NI", 3, True), "pri": ("PRI", 3, True),
    "etime": ("ELAPSED", 11, True), "etimes": ("ELAPSED", 7, True),
    "args": ("COMMAND", 27, False), "command": ("COMMAND", 27, False), "cmd": ("CMD", 27, False),
    "comm": ("COMMAND", 15, False), "ucmd": ("CMD", 15, False), "ucomm": ("COMMAND", 15, False),
    "fname": ("COMMAND", 8, False),
    "f": ("F", 1, True), "flags": ("F", 1, True), "uid": ("UID", 5, True), "euid": ("EUID", 5, True),
    "addr": ("ADDR", 1, False), "sz": ("SZ", 5, True), "wchan": ("WCHAN", 6, False), "psr": ("PSR", 3, True),
    "start": ("STARTED", 8, True), "lstart": ("STARTED", 24, True),
}
# columns procps cuts to their width when another column follows (the last one runs to the line's end)
TPOT_PS_CUT = frozenset({"args", "command", "cmd", "comm", "ucmd", "ucomm", "fname", "wchan"})
TPOT_PS_FORMATS = {
    "default": "pid,tname,time,ucmd", "full": "uid_hack,pid,ppid,c,stime,tname,time,cmd",
    "bsd": "pid,tname,stat,bsdtime,args", "user": "user,pid,%cpu,%mem,vsz,rss,tname,stat,start_time,bsdtime,args",
    "long": "f,s,uid,pid,ppid,c,pri,ni,addr,sz,wchan,tname,time,ucmd",
    "extra": "uid_hack,pid,ppid,c,sz,rss,psr,stime,tname,time,cmd",
}
# procps writes what it is asked for (3000 columns of args were recorded); the honeypot stops at a
# number of columns and a size of output, as its other commands do
TPOT_PS_ITEMS = 4096
TPOT_PS_OUTPUT = 1 << 20
# options procps takes that change nothing here (threads, forest, sorting, personality)
TPOT_PS_SYSV_QUIET = frozenset("HLTmMjNcPyZw")
TPOT_PS_BSD_QUIET = frozenset("efHLmMSTncsvjlVZ")
TPOT_PS_LONG_QUIET = frozenset({"forest", "headers", "info", "context", "cumulative", "deselect"})
TPOT_PS_LONG_VALUES = {"sort": "long sort specification must follow --sort",
                       "format": "format specification must follow --format",
                       "pid": "list of process IDs must follow --pid",
                       "ppid": "list of process IDs must follow --ppid",
                       "user": "list of users must follow --user", "User": "list of users must follow --User",
                       "group": "list of groups must follow --group", "Group": "list of groups must follow --Group",
                       "tty": "list of ttys must follow --tty", "sid": "list of session IDs must follow --sid",
                       "cols": "number of columns must follow --cols", "columns": "number of columns must follow --columns",
                       "width": "number of columns must follow --width", "rows": "number of rows must follow --rows",
                       "lines": "number of rows must follow --lines"}
TPOT_PS_ROWS = 4096


class TpotPsStop(Exception):
    def __init__(self, text: str, code: int = 1) -> None:
        super().__init__(text)
        self.text, self.code = text, code


def tpot_ps_layout(cells: list[str], specs: list[tuple]) -> str:
    """procps show_one_proc: the screen position each column should start at and where the text is,
    a right-aligned column padded on its left, a wide value pushing the next columns."""
    correct = actual = 0
    out = []
    for index, (text, spec) in enumerate(zip(cells, specs)):
        width, right = spec[1], spec[2]
        leftpad = max(0, width - len(text)) if right else 0
        space = correct - actual + leftpad
        if space < 1:
            space = 1 if index else 0
        out.append(" " * space + text)
        actual += space + len(text)
        correct += width + 1
    return "".join(out)


def tpot_ps_specs(items: list[str], pid_width: int, version: tuple) -> list[tuple]:
    """The columns of the format items (name, name:width, name=header) as (header, width, right,
    name); a TpotPsStop for an unknown one."""
    specs = []
    total = 0
    for item in items[:TPOT_PS_ITEMS]:
        if total > TPOT_PS_OUTPUT:
            # wider than the output may be: the line is never made (name:width up to 9999 each)
            break
        name, eq, title = item.partition("=")
        name, colon, size = name.strip().partition(":")
        key = name.lower() if name.lower() in TPOT_PS_FIELDS else name
        if key not in TPOT_PS_FIELDS or (colon and not (size.isascii() and size.isdigit() and 0 < len(size) < 5)):
            raise TpotPsStop(f'error: unknown user-defined format specifier "{item.partition("=")[0].strip()}"\n'
                             f"{TPOT_PS_HELP}", 1)
        head, width, right = TPOT_PS_FIELDS[key]
        width = width or pid_width
        if colon:
            width = int(size)
        if eq:
            head = title
            width = max(width, len(title))
        specs.append((head, width, right, key))
        total += width + 1
    return specs


def tpot_ps_row(cells: list[str], specs: list[tuple]) -> str:
    """A line of procps: a command column that another column follows is cut to its width."""
    last = len(specs) - 1
    cut = [(text[:spec[1]] if index < last and len(spec) > 3 and spec[3] in TPOT_PS_CUT else text)[:TPOT_PS_OUTPUT]
           for index, (text, spec) in enumerate(zip(cells, specs))]
    return tpot_ps_layout(cut, specs)[:TPOT_PS_OUTPUT + 1]


def tpot_ps_users(fs) -> list[tuple[str, int]]:
    """(name, uid) of the accounts of the emulated /etc/passwd."""
    try:
        text = fs.file_contents("/etc/passwd").decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - no passwd
        return [("root", 0)]
    users = []
    for line in text.splitlines()[:4096]:
        parts = line.split(":")
        if len(parts) > 2 and parts[2].isascii() and parts[2].isdigit():
            users.append((parts[0], int(parts[2])))
    return users or [("root", 0)]


def tpot_ps_bsdtime(seconds: float) -> str:
    whole = int(seconds)
    return f"{whole // 60}:{whole % 60:02d}"


def tpot_ps_time(seconds: float) -> str:
    whole = int(seconds)
    days, rest = divmod(whole, 86400)
    text = f"{rest // 3600:02d}:{rest % 3600 // 60:02d}:{rest % 60:02d}"
    return f"{days}-{text}" if days else text


def tpot_ps_etime(seconds: float) -> str:
    whole = max(0, int(seconds))
    days, rest = divmod(whole, 86400)
    hours, rest = divmod(rest, 3600)
    text = f"{rest // 60:02d}:{rest % 60:02d}"
    if days:
        return f"{days}-{hours:02d}:{text}"
    return f"{hours:02d}:{text}" if hours else text


def tpot_ps_comm(command: str, kernel: bool) -> str:
    if kernel:
        return command.strip("[]")
    return tpot_top_comm(command)


def tpot_bb_size(value: int) -> str:
    """BusyBox smart_ulltoa5 with the scale " mgtpezy": 5 cells, kB above 99999 as m, g, ..."""
    if value <= 99999:
        return f"{value:5d}"
    value *= 10
    index = 0
    while True:
        value //= 1024
        index += 1
        if value < 100000:
            break
    whole, tenth = divmod(value, 10)
    scale = " mgtpezy"[index]
    if whole >= 100:
        return f"{whole:4d}{scale}"
    return f"{whole:2d}.{tenth}{scale}"


def tpot_cpus(fs) -> int:
    """The CPUs of the emulated /proc/cpuinfo."""
    try:
        return sum(1 for line in fs.file_contents("/proc/cpuinfo").splitlines() if line.startswith(b"processor"))
    except Exception:  # noqa: BLE001 - no cpuinfo
        return 1


def tpot_kernel_threads() -> list[dict]:
    """The kernel threads of the persona ([shell] kernel_thread_list: a JSON list of ps entries)."""
    path = CowrieConfig.get("shell", "kernel_thread_list", fallback="")
    if not path:
        return []
    cached = _TPOT_KERNEL_THREADS.get(path)
    if cached is None:
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            data = []
        cached = [entry for entry in data if isinstance(entry, dict) and isinstance(entry.get("PID"), int)] \
            if isinstance(data, list) else []
        _TPOT_KERNEL_THREADS[path] = cached
    return cached


_TPOT_KERNEL_THREADS: dict = {}


def tpot_pipeline_words(cmd) -> list[list[str]]:
    """The commands after cmd in its pipeline: the shell that runs the pipeline keeps the words of
    each stage and the one running (patch_pipeline_words); cmd runs in the child shell of its stage."""
    own = getattr(cmd, "shell", None)
    stages = next((item.tpot_pipeline for item in reversed(getattr(cmd.protocol, "cmdstack", None) or [])
                   if item is not own and isinstance(getattr(item, "tpot_pipeline", None), tuple)), None)
    if stages is None or len(stages) != 2 or not isinstance(stages[1], int):
        return []
    words, index = stages
    return [stage for stage in list(words)[index + 1:index + 33] if isinstance(stage, list)]


class Command_tpot_ps(HoneyPotCommand):
    """T-Pot: ps of the persona (see TPOT_PS_HELP)."""

    def call(self) -> None:
        try:
            code = self.tpot_main()
        except TpotPsStop as stop:
            self.errorWrite(stop.text)
            code = stop.code
        self.exit_code = code

    def tpot_main(self) -> int:
        self.busybox = CowrieConfig.get("shell", "top_style", fallback="procps") == "busybox"
        version = CowrieConfig.get("shell", "procps_version", fallback="4.0.4")
        self.version_text = version
        self.version = tuple(int(x) for x in re.findall(r"\d+", version)[:3]) or (4, 0, 4)
        self.now = time.time()
        self.username = str(self.user.get("username", "root"))
        if self.busybox:
            return self.tpot_busybox()
        return self.tpot_procps()

    # --- the processes ------------------------------------------------------------------------

    def tpot_rows(self) -> list[dict]:
        protocol = self.protocol
        host = getattr(protocol, "hostname", "")
        boot = protocol.boot_time() if hasattr(protocol, "boot_time") else self.now
        uptime = max(1.0, self.now - boot)
        login_at = min(self.now, getattr(protocol, "logintime", self.now))
        accounts = tpot_ps_users(self.fs)
        cpus = max(1, tpot_cpus(self.fs))

        def account(name: str) -> tuple[str, int | None]:
            """The full name and uid of a user: the list's USERNAME, else its USER (cut to 8 with +) when
            only one account fits it; the session's user without an account has the uid of the session,
            any other name no uid (a uid selection does not take it)."""
            for full, uid in accounts:
                if full == name:
                    return full, uid
            fitting = [(full, uid) for full, uid in accounts
                       if name.endswith("+") and full.startswith(name[:-1]) and len(full) > 8]
            if len(fitting) == 1:
                return fitting[0]
            if name == "root":
                return name, 0
            try:
                own = self.user.get("uid") if name == self.username else None
            except Exception:  # noqa: BLE001 - a user without a uid
                own = None
            return name, own if isinstance(own, int) and not isinstance(own, bool) else None

        def row(pid, ppid, user, tty, stat, started, vsz, rss, pmem, seconds, command, kernel=False, start=None,
                full=None, **extra):
            name, uid = account(full or user)
            draw = int.from_bytes(hashlib.sha256(f"{host}\0{pid}\0row".encode("utf-8")).digest()[:4], "big")
            wchan = ("kthrea" if command == "[kthreadd]" else "worker" if command.startswith("[kworker")
                     else "smpboo" if kernel and "/" in command else "-" if kernel or stat[:1] == "R"
                     else "do_wai" if tty != "?" else "ep_pol")
            data = {"pid": pid, "ppid": ppid, "user": tpot_top_user(name), "name": name, "uid": uid, "tty": tty,
                    "stat": stat, "start": start if start is not None else tpot_ps_start(started, self.now),
                    "started": started, "elapsed": max(0.0, self.now - started), "vsz": vsz, "rss": rss,
                    "pmem": pmem, "pcpu": 0.0, "time": seconds, "args": command,
                    "comm": tpot_ps_comm(command, kernel), "kernel": kernel, "login": False, "psr": draw % cpus,
                    "wchan": wchan}
            data.update(extra)
            return data

        rows = []
        listed = protocol.tpot_processes() if hasattr(protocol, "tpot_processes") else []
        shell = None
        for entry in listed[:TPOT_PS_ROWS]:
            try:
                pid = int(entry["PID"])
            except (KeyError, TypeError, ValueError):
                continue
            tty = str(entry.get("TTY") or "?")
            login = tpot_login_entry(entry)
            command = str(entry.get("COMMAND") or "")
            stat = str(entry.get("STAT") or "S")
            user = str(entry.get("USER") or "root")
            text = str(entry.get("START") or "")
            started = tpot_ps_stamp(text, boot, self.now, host, pid)
            # the CPU time since its start (tpot_listed_seconds: the one of top and of its unit's CGroup)
            seconds = tpot_listed_seconds(host, entry, boot, self.now)
            if login:
                # the login shell of this session: its user, started at the login, leading its session unless a
                # process of the session's chain on the terminal leads it (login -p of inetutils before 2.6)
                shell = pid
                led = any(entry.get("tty") and "s" in str(entry.get("stat") or "")
                          for entry in tpot_session(protocol, self.username)["chain"])
                stat = stat.replace("s", "") if led else stat if "s" in stat else stat[:1] + "s" + stat[1:]
                rows.append(row(pid, 1, self.username, tty, stat, login_at, int(entry.get("VSZ") or 0),
                                int(entry.get("RSS") or 0), float(entry.get("MEM") or 0.0), seconds, command,
                                login=True))
                continue
            rows.append(row(pid, 0 if pid == 1 else tpot_parent(entry), user, tty, stat, started, int(entry.get("VSZ") or 0),
                            int(entry.get("RSS") or 0), float(entry.get("MEM") or 0.0), seconds, command,
                            start=text or None, pcpu=float(entry.get("CPU") or 0.0),
                            full=str(entry.get("USERNAME") or "") or None))
        for entry in tpot_kernel_threads()[:TPOT_PS_ROWS]:
            command = str(entry.get("COMMAND") or "[kworker/0:0]")
            pid = entry["PID"]
            text = str(entry.get("START") or "")
            rows.append(row(pid, int(entry.get("PPID", 0 if pid == 2 else 2)), "root", "?",
                            str(entry.get("STAT") or "S"), tpot_ps_stamp(text, boot, self.now, host, pid), 0, 0, 0.0,
                            tpot_process_seconds(host, pid, 0, uptime, False) / 50, command, kernel=True,
                            start=text or None))
        session = tpot_session(protocol, self.username)
        # the parent of the session: the inetd (xinetd, telnetd) of a telnet session, the sshd or
        # dropbear of an SSH session, else init
        own = {entry["pid"] for entry in session["chain"]}
        daemons = {r["comm"]: r["pid"] for r in rows if not r["kernel"] and r["tty"] == "?" and r["pid"] not in own}
        keys = (("inetd", "xinetd", "telnetd", "utelnetd") if session["telnet"]
                else (session["name"], session["name"].split("-", 1)[0], "sshd", "dropbear"))
        parent = next((daemons[key] for key in keys if key in daemons), 1)
        known = {r["pid"] for r in rows}
        for entry in session["chain"]:
            if entry["pid"] not in known:
                rows.append(row(entry["pid"], parent, str(entry["user"]), str(entry.get("tty") or "?"),
                                str(entry.get("stat") or "S"), login_at, int(entry.get("virt") or 0),
                                int(entry.get("res") or 0), 0.1, 0.02, str(entry["command"])))
            parent = entry["pid"]
        for item in rows:
            if item["pid"] == shell:
                item["ppid"] = parent
        # ps itself and the commands after it in its pipeline (they run when ps reads /proc)
        stages = tpot_pipeline_words(self)
        first = protocol.tpot_new_pid(1 + len(stages), self.now) if hasattr(protocol, "tpot_new_pid") \
            else max(known | {1000}) + 9
        argv0 = str(getattr(self.pp, "argv0", None) or "ps")
        commands = [[argv0, *self.args]] + stages
        tty = "pts/0" if shell is not None else "?"
        # BusyBox: every applet is the one binary, of the size of the shell
        size = next(((r["vsz"], r["rss"]) for r in rows if r["pid"] == shell), (1260, 480)) if self.busybox else None
        for offset, words in enumerate(commands):
            words = [str(w) for w in words] or ["sh"]
            rows.append(row(first + offset, shell or parent, self.username, tty,
                            ("R" if not offset else "S") + ("+" if shell is not None else ""), self.now,
                            size[0] if size else 7640 if not offset else 3136,
                            size[1] if size else 3652 if not offset else 1524, 0.0, 0.0, " ".join(words),
                            self=not offset))
        rows.sort(key=lambda r: r["pid"])
        return rows

    def tpot_clock(self, when: float) -> str:
        return time.strftime("%H:%M", time.gmtime(when))

    # --- procps -----------------------------------------------------------------------------

    def tpot_fail(self, text: str) -> TpotPsStop:
        return TpotPsStop(f"error: {text}\n{TPOT_PS_HELP}", 1)

    def tpot_list(self, value, text: str) -> list[str]:
        if value is None or not str(value).strip(" ,"):
            raise self.tpot_fail(text)
        return [item for item in re.split(r"[ ,]+", str(value)) if item]

    def tpot_procps(self) -> int:
        args = list(self.args)
        sel = {"every": False, "a": False, "x": False, "pids": set(), "users": set(), "cmds": set(), "ttys": set(),
               "unix_a": False, "d": False}
        fmt = None
        custom: list[str] = []
        header = True
        wide = 0
        bsd = None
        columns = None
        index = 0
        while index < len(args):
            arg = args[index]
            index += 1

            def value():
                nonlocal index
                if index < len(args):
                    index += 1
                    return args[index - 1]
                return None

            if arg.startswith("--"):
                name, eq, given = arg[2:].partition("=")
                if name in ("help",):
                    self.write(TPOT_PS_HELP)
                    return 0
                if name in ("version", "V"):
                    self.write(f"ps from procps-ng {self.version_text}\n" if self.version >= (3, 3, 11)
                               else f"procps-ng version {self.version_text}\n")
                    return 0
                if name in ("no-headers", "no-heading"):
                    header = False
                    continue
                if name in TPOT_PS_LONG_QUIET:
                    continue
                if name in TPOT_PS_LONG_VALUES:
                    given = given if eq else value()
                    items = self.tpot_list(given, TPOT_PS_LONG_VALUES[name])
                    if name == "format":
                        custom.append(given)
                    elif name == "pid":
                        sel["pids"].update(self.tpot_pids(items))
                    elif name in ("user", "User"):
                        sel["users"].update(items)
                    elif name == "tty":
                        sel["ttys"].update(items)
                    elif name in ("cols", "columns", "width") and items[0].isdigit():
                        columns = int(items[0][:6])
                    continue
                raise self.tpot_fail("unknown gnu long option")
            if arg.startswith("-") and arg != "-" and not (arg == "-aux" or arg == "-axu"):
                letters = arg[1:]
                while letters:
                    letter, letters = letters[0], letters[1:]
                    if letter == "V":
                        self.write(f"ps from procps-ng {self.version_text}\n" if self.version >= (3, 3, 11)
                                   else f"procps-ng version {self.version_text}\n")
                        return 0
                    if letter in "eA":
                        sel["every"] = True
                    elif letter == "a":
                        # all with a terminal but the session leaders
                        sel["unix_a"] = True
                    elif letter == "d":
                        # all but the session leaders
                        sel["d"] = True
                    elif letter == "f":
                        fmt = fmt if fmt in (TPOT_PS_FORMATS["long"], TPOT_PS_FORMATS["extra"]) else TPOT_PS_FORMATS["full"]
                        bsd = False if bsd is None else bsd
                    elif letter == "l":
                        fmt = TPOT_PS_FORMATS["long"]
                        bsd = False if bsd is None else bsd
                    elif letter == "F":
                        fmt = TPOT_PS_FORMATS["extra"]
                        bsd = False if bsd is None else bsd
                    elif letter in "pqsCuUtgGo":
                        given = letters or value()
                        letters = ""
                        texts = {"p": "list of process IDs must follow -p", "q": "list of process IDs must follow -q",
                                 "s": "list of session IDs must follow -s", "C": "list of command names must follow -C",
                                 "u": "list of users must follow -u", "U": "list of users must follow -U",
                                 "t": "list of terminals (pty, tty...) must follow -t",
                                 "g": "list of session leaders OR effective group names must follow -g",
                                 "G": "list of real groups must follow -G", "o": "format specification must follow -o"}
                        if letter in "uU" and not given:
                            # procps 3.3 / 4.0: -u without a list prints the header of u and fails
                            self.write(tpot_ps_layout([s[0] for s in self.tpot_specs(TPOT_PS_FORMATS["user"])],
                                                      self.tpot_specs(TPOT_PS_FORMATS["user"])) + "\n")
                            return 1
                        if letter == "o":
                            self.tpot_list(given, texts[letter])
                            custom.append(given)
                            continue
                        items = self.tpot_list(given, texts[letter])
                        if letter in "pq":
                            sel["pids"].update(self.tpot_pids(items))
                        elif letter in "uU":
                            sel["users"].update(items)
                        elif letter == "C":
                            sel["cmds"].update(items)
                        elif letter == "t":
                            sel["ttys"].update(items)
                    elif letter == "w":
                        wide += 1
                    elif letter in TPOT_PS_SYSV_QUIET:
                        continue
                    else:
                        raise self.tpot_fail("unsupported SysV option")
                continue
            # BSD options (a leading dash of -aux is dropped as procps does)
            letters = arg.lstrip("-")
            if letters.isascii() and letters.isdigit():
                sel["pids"].add(str(int(letters)))
                bsd = True if bsd is None else bsd
                continue
            bsd = True if bsd is None else bsd
            while letters:
                letter, letters = letters[0], letters[1:]
                if letter == "a":
                    sel["a"] = True
                elif letter == "x":
                    sel["x"] = True
                elif letter == "u":
                    fmt = fmt or TPOT_PS_FORMATS["user"]
                elif letter == "w":
                    wide += 1
                elif letter == "h":
                    header = False
                elif letter in "opqUtO":
                    given = letters or value()
                    letters = ""
                    texts = {"o": "format specification must follow o", "O": "format specification must follow O",
                             "p": "list of process IDs must follow p", "q": "List of process IDs must follow q.",
                             "U": "list of users must follow U", "t": "list of terminals (pty, tty...) must follow t"}
                    items = self.tpot_list(given, texts[letter])
                    if letter in "oO":
                        custom.append(given)
                    elif letter in "pq":
                        sel["pids"].update(self.tpot_pids(items))
                    elif letter == "U":
                        sel["users"].update(items)
                    elif letter == "t":
                        sel["ttys"].update(items)
                elif letter in TPOT_PS_BSD_QUIET:
                    continue
                else:
                    raise self.tpot_fail("unsupported option (BSD syntax)")
        if custom:
            fmt = ",".join(custom)
        elif fmt is None:
            fmt = TPOT_PS_FORMATS["bsd"] if bsd else TPOT_PS_FORMATS["default"]
        specs = self.tpot_specs(fmt)
        rows = self.tpot_select(self.tpot_rows(), sel, bsd)
        window = tpot_window(self)
        width = 0
        if columns:
            width = columns
        elif wide >= 2:
            width = 0
        elif window is not None:
            width = max(window[1], 132) if wide else window[1]
        # at most TPOT_PS_OUTPUT bytes: the lines are made one by one until they are that many
        out, size = [], 0
        if header and any(spec[0] for spec in specs):
            out.append(tpot_ps_row([spec[0] for spec in specs], specs))
        for row in rows:
            if size > TPOT_PS_OUTPUT:
                break
            line = tpot_ps_row([self.tpot_cell(name, row, specs[i]) for i, name in enumerate(self.names)], specs)
            out.append(line)
            size += len(line)
        text = "".join(f"{line[:width] if width else line}\n" for line in out)
        self.write(text[:TPOT_PS_OUTPUT])
        return 0 if rows else 1

    def tpot_specs(self, fmt: str) -> list[tuple]:
        """The columns of a format (procps: "pid,comm", "pid user", name:width; a header after = to
        the end of the item, before procps 3.3.12 to the end of the list)."""
        items = []
        rest = fmt
        while rest and len(items) < TPOT_PS_ITEMS:
            item, sep, rest = rest.partition(",")
            if "=" in item and self.version < (3, 3, 12):
                item = item + sep + rest
                rest = ""
            items += [item] if "=" in item else item.split()
        specs = tpot_ps_specs(items, self.tpot_pid_width(), self.version)
        if not specs:
            raise self.tpot_fail("format specification must follow -o")
        self.names = [spec[3] for spec in specs]
        return specs

    def tpot_pids(self, items: list[str]) -> list[str]:
        """A process ID list: numbers only (procps: "process ID list syntax error")."""
        if not all(item.isascii() and item.isdigit() and len(item) < 11 for item in items):
            raise self.tpot_fail("process ID list syntax error")
        return [str(int(item)) for item in items]

    def tpot_pid_width(self) -> int:
        try:
            pid_max = CowrieConfig.getint("shell", "pid_max", fallback=4194304)
        except ValueError:
            pid_max = 4194304
        return max(5, len(str(max(1, pid_max))))

    def tpot_select(self, rows: list[dict], sel: dict, bsd) -> list[dict]:
        listed = bool(sel["pids"] or sel["users"] or sel["cmds"] or sel["ttys"])
        shell_tty = next((r["tty"] for r in rows if r.get("self")), "?")
        out = []
        for row in rows:
            leader = "s" in row["stat"]
            if sel["every"] or (sel["a"] and sel["x"]):
                keep = True
            elif sel["d"] or sel["unix_a"]:
                keep = not leader and (sel["d"] or row["tty"] != "?")
            elif listed:
                keep = False
            elif sel["a"]:
                keep = row["tty"] != "?"
            elif sel["x"]:
                keep = row["user"] == tpot_top_user(self.username)
            else:
                keep = row["tty"] == shell_tty and row["tty"] != "?" and row["user"] == tpot_top_user(self.username)
            if not keep and listed:
                keep = (str(row["pid"]) in sel["pids"]
                        or bool({row["name"], row["user"], *(() if row["uid"] is None else (str(row["uid"]),))}
                                & sel["users"])
                        or row["comm"] in sel["cmds"] or row["tty"] in sel["ttys"]
                        or row["tty"].replace("pts/", "") in sel["ttys"])
            if keep:
                out.append(row)
        return out

    def tpot_cell(self, name: str, row: dict, spec: tuple) -> str:
        if name in ("user", "ruser", "euser", "uname", "uid_hack"):
            # a name longer than the column: its first characters and + (procps)
            return row["name"] if len(row["name"]) <= spec[1] else row["name"][:spec[1] - 1] + "+"
        if name in ("uid", "euid"):
            return "-" if row["uid"] is None else str(row["uid"])
        if name in ("f", "flags"):
            return "1" if row["kernel"] else "4" if row["uid"] == 0 and not row.get("self") else "0"
        if name == "addr":
            return "-"
        if name == "sz":
            return str(row["vsz"] // 4)
        if name == "wchan":
            return row["wchan"]
        if name == "psr":
            return str(row["psr"])
        if name == "start":
            return (time.strftime("%H:%M:%S", time.gmtime(row["started"])) if self.now - row["started"] < 86400
                    else time.strftime("%b %d", time.gmtime(row["started"])))
        if name == "lstart":
            return time.strftime("%a %b %e %H:%M:%S %Y", time.gmtime(row["started"]))
        if name in ("pid", "tgid"):
            return str(row["pid"])
        if name == "ppid":
            return str(row["ppid"])
        if name in ("pgid", "sid", "sess"):
            return str(row["pid"] if row["login"] or row["kernel"] else row["pid"])
        if name in ("%cpu", "pcpu"):
            return f"{row['pcpu']:.1f}"
        if name in ("%mem", "pmem"):
            return f"{row['pmem']:.1f}"
        if name in ("vsz", "vsize"):
            return str(row["vsz"])
        if name in ("rss", "rssize", "rsz"):
            return str(row["rss"])
        if name in ("tty", "tt", "tname"):
            return row["tty"]
        if name == "stat":
            return row["stat"]
        if name in ("s", "state"):
            return row["stat"][:1]
        if name in ("start_time", "stime", "bsdstart"):
            return row["start"]
        if name in ("time", "cputime"):
            return tpot_ps_time(row["time"])
        if name == "bsdtime":
            return tpot_ps_bsdtime(row["time"])
        if name == "c":
            return str(min(99, int(row["pcpu"])))
        if name in ("ni", "nice"):
            return "-20" if "<" in row["stat"] else "19" if "N" in row["stat"] else "0"
        if name == "pri":
            # the PRI of -l: 80 + nice
            return "60" if "<" in row["stat"] else "99" if "N" in row["stat"] else "80"
        if name == "etime":
            return tpot_ps_etime(row["elapsed"])
        if name == "etimes":
            return str(int(row["elapsed"]))
        if name in ("args", "command", "cmd"):
            return row["args"]
        if name == "fname":
            return row["comm"][:8]
        text = row["comm"]
        return text if self.names[-1] == name else text[:spec[1]]

    # --- BusyBox ----------------------------------------------------------------------------

    def tpot_busybox(self) -> int:
        numbers = re.findall(r"\d+", CowrieConfig.get("shell", "text_version", fallback="1.36.1"))
        version = tuple(int(x) for x in numbers[:3]) or (1, 36, 1)
        libc = CowrieConfig.get("shell", "text_libc", fallback="glibc")
        args = list(self.args)
        if args and not args[0].startswith("-"):
            args[0] = "-" + args[0]
        wide = 0
        for arg in args:
            if not arg.startswith("-") or arg == "-":
                continue
            if arg == "--":
                break
            if arg.startswith("--"):
                text = {"musl": f"ps: unrecognized option: {arg[2:].partition('=')[0]}\n",
                        "uclibc": f"ps: unrecognized option `{arg}'\n"}.get(libc, f"ps: unrecognized option '{arg}'\n")
                raise TpotPsStop(text + self.tpot_bb_usage(version), 1)
            for letter in arg[1:]:
                if letter == "w":
                    wide += 1
                    continue
                text = {"musl": f"ps: unrecognized option: {letter}\n",
                        "uclibc": f"ps: invalid option -- {letter}\n"}.get(libc, f"ps: invalid option -- '{letter}'\n")
                raise TpotPsStop(text + self.tpot_bb_usage(version), 1)
        if wide:
            width = 132 if wide == 1 else 0
        else:
            # get_terminal_width(0): the terminal of stdin (also when stdout is a pipe), else 80
            size = getattr(getattr(self.protocol, "user", None), "windowSize", None)
            piped = getattr(self.pp, "stdin_from_pipe", False) or getattr(self, "input_data", None) is not None
            width = (int(size[1]) if size and not piped and int(size[1]) > 0 else 80) - 1
        lines = ["  PID USER       VSZ STAT COMMAND"]
        for row in self.tpot_rows():
            state = row["stat"][:1] + ("W" if row["vsz"] == 0 and row["stat"][:1] != "Z" else " ") \
                + ("<" if "<" in row["stat"] else "N" if "N" in row["stat"] else " ")
            prefix = f"{row['pid']:5d} {row['user'][:8]:<8} {tpot_bb_size(row['vsz'])} {state}  "
            command = row["args"] if not row["kernel"] else f"[{row['comm']}]"
            if width:
                # read_cmdline: one byte less than the room left (BusyBox 1.14: the whole room)
                room = width - len(prefix) - (1 if version >= (1, 19) else 0)
                command = command[:max(0, room)]
            lines.append(prefix + command)
        self.write("".join(f"{line}\n" for line in lines))
        return 0

    def tpot_bb_usage(self, version: tuple) -> str:
        banner = CowrieConfig.get("shell", "text_banner", fallback="BusyBox v1.36.1")
        head = f"{banner} multi-call binary.\n" if version >= (1, 15) else f"{banner} multi-call binary\n"
        if version < (1, 15):
            body = "Usage: ps \n\nReport process status\n\nOptions:\n\tw\tWide output\n\n"
        elif version < (1, 24):
            body = "Usage: ps \n\nShow list of processes\n\n\tw\tWide output\n\n"
        elif version < (1, 30):
            body = "Usage: ps \n\nShow list of processes\n\n\tw\tWide output\n"
        else:
            body = "Usage: ps\n\nShow list of processes\n\n\tw\tWide output\n"
        return f"{head}\n{body}"


for _name in ("ps", "/bin/ps", "/usr/bin/ps"):
    commands[_name] = Command_tpot_ps
'''


def patch_ps_top(cowrie_root):
    """ps cuts its lines as procps does (PS_WINDOW): at the width of the terminal, not when its
    output goes to a pipe or a file (ps aux | grep sshd finds the COMMAND); top of the persona
    (TOP_COMMAND) and the load average of w. Runs after patch_protocol_load."""
    path = cowrie_root / "src" / "cowrie" / "commands" / "base.py"
    replace_once(path, "import getopt\nimport random\nimport re\n", "import calendar\nimport getopt\nimport hashlib\nimport json\nimport random\nimport re\n")
    replace_once(path, "from cowrie.core import utils\n", "from cowrie.core import utils\nfrom cowrie.core.config import CowrieConfig\n")
    replace_once(path, "\n\nclass Command_ps(HoneyPotCommand):\n", PS_WINDOW + TPOT_CPU + "\n\nclass Command_ps(HoneyPotCommand):\n")
    replace_once(path, 'commands["/bin/ps"] = Command_ps\ncommands["ps"] = Command_ps\n',
                 'commands["/bin/ps"] = Command_ps\ncommands["ps"] = Command_ps\n' + TOP_COMMAND + PS_COMMAND
                 + tpot_metering("Command_top", "Command_tpot_ps") + 'tpot_meter_class(Command_top, "tpot_tick")\n')
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
    # the session's deadline (TPOT_BUDGET) of grep
    replace_once(path, "from cowrie.shell.command import HoneyPotCommand\n",
                 "from cowrie.shell.command import HoneyPotCommand, tpot_deadline\n")
    replace_once(path, "import re\nfrom typing import TYPE_CHECKING\n", "import re\nimport time\nfrom typing import TYPE_CHECKING\n")
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
    tpot_until: float | None = None
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
            # the pattern as given: one with a slash (/bin/bash, ^tree/) is no path
            bmatch = match.replace('"', "").encode("utf8")
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
        # the session's deadline (TPOT_BUDGET), taken for each part of the input (a file, a typed line): past
        # it grep stops as when its work budget is used up
        self.tpot_until = tpot_deadline(getattr(self, "protocol", None))
        contents = contents[: self.tpot_input_left]
        self.tpot_input_left -= len(contents)
        lines = contents.split(b"\\n")
        if lines[-1] == b"":
            lines.pop()
        for number, line in enumerate(lines):
            if self.max_count is not None and self.match_count >= self.max_count:
                break
            found = matcher.search(line) is not None
            if matcher.spent or not number & 255 and time.monotonic() >= self.tpot_until:
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
            if self.tpot_exhausted:
                # as GNU grep: it ends at once, the lines after it are the shell's
                self.exit(self.exit_status())
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
            table.append([self.gnu_cell(field, row, arg, human, block, suffix) for field in fields])
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

    def gnu_cell(self, field: str, row: dict, arg: str, human: int, block: int, suffix: str) -> str:
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
        """The help of coreutils before 8.31 (checked: 8.22 of CentOS 7, 8.23 of Debian 8, 8.26 of Debian 9,
        8.28 of Ubuntu 18.04, 8.30 of Debian 10, 8.31 of Alpine 3.11)."""
        try:
            version = tuple(int(x) for x in str(self.data.get("coreutils", "9.1")).split(".")[:2])
        except ValueError:
            return text
        if version >= (8, 31):
            return text
        web = "http" if version < (8, 29) else "https"
        total = ("      --total           elide all entries insignificant to available space,\n"
                 "                          and produce a grand total\n")
        text = text.replace("Binary prefixes can be used, too: KiB=K, MiB=M, and so on.\n", "").replace(
            "GNU coreutils online help: <https://www.gnu.org/software/coreutils/>\n"
            "Report any translation bugs to <https://translationproject.org/team/>\n"
            "Full documentation <https://www.gnu.org/software/coreutils/df>\n",
            f"GNU coreutils online help: <{web}://www.gnu.org/software/coreutils/>\n"
            f"Report df translation bugs to <{web}://translationproject.org/team/>\n"
            f"Full documentation at: <{web}://www.gnu.org/software/coreutils/df>\n")
        if version >= (8, 24):
            return text
        if version >= (8, 23):
            # 8.23 of Debian 8: -a and --total as before 8.24
            return text.replace(total, "").replace(
                "  -h, --human-readable", "      --total           produce a grand total\n  -h, --human-readable").replace(
                "include pseudo, duplicate, inaccessible file systems", "include dummy file systems")
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
            old = tuple(int(x) for x in version.split(".")[:2]) < (8, 29)
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
    replace_once(path, 'commands["/bin/fgrep"] = Command_grep\n', 'commands["/bin/fgrep"] = Command_grep\n' + DF_COMMAND
                 + tpot_metering("Command_grep", "Command_df"))


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
from cowrie.shell.command import tpot_deadline


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

    def __init__(self, style: str, version: str, read_file, stdin: bytes | None, limit: float = math.inf) -> None:
        self.style, self.version, self.read_file, self.stdin = style, version, read_file, stdin or b""
        self.limit = limit  # the session's deadline (TPOT_BUDGET)
        new_mawk = style == "mawk" and (version.split() + ["0"])[1] >= "20230000"
        self.intervals = style != "mawk" or (version.split() + ["0"])[1] >= "20200717"
        self.int_limit = 2.0 ** 31 if style == "mawk" and not new_mawk else 2.0 ** 63
        self.source_name = "cmd. line"
        self.out, self.err = [], []
        self.written = self.steps = self.status = 0
        self.vars = {"FS": " ", "OFS": " ", "ORS": "\n", "OFMT": "%.6g", "CONVFMT": "%.6g", "NR": 0.0, "FNR": 0.0,
                     "NF": 0.0, "FILENAME": TPOT_AWK_UNINIT, "RS": "\n", "SUBSEP": "\x1c"}
        self.deadline = min(time.monotonic() + self.MAX_SECONDS, self.limit)
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
            if self.style == "mawk":
                return self.mawk_substr(text, start, self.num(values[2]) if len(values) == 3 else None)
            # gawk and BusyBox: a start below 1 is 1, the length stays
            start = max(1, int(max(min(start, 2.0 ** 31), -2.0 ** 31)))
            if len(values) == 2:
                return text[start - 1:]
            length = self.num(values[2])
            if not length > 0:
                return ""
            return text[start - 1:start - 1 + int(min(length, 2.0 ** 31))]
        if name == "index":
            needle = self.touch(self.text(values[1]))
            if not needle and self.style == "busybox":
                return 0.0  # BusyBox finds no empty string, gawk and mawk find it at 1
            return float(text.find(needle) + 1)
        if name == "tolower":
            return text.lower() if text.isascii() else text.translate(TPOT_AWK_LOWER)
        if name == "toupper":
            return text.upper() if text.isascii() else text.translate(TPOT_AWK_UPPER)
        if name == "int":
            x = self.num(values[0])
            return float(math.trunc(x)) if math.isfinite(x) else x
        return self.format(text, values[1:])

    def mawk_substr(self, text: str, start: float, length) -> str:
        """substr of mawk, start and length truncated to its int (as recorded with mawk 1.3.3 of
        debian:9, 1.3.4 20200120, 20240123 and 20250131):
        - 1.3.3 shortens as POSIX says: a start below 1 takes its distance from 1 off the length
          (substr("hello", 0, 5) is "hell", substr("hello", -1, 3) is "h");
        - 1.3.4 adds its distance from 0 to the length (substr("hello", -1, 3) is "hell");
        - up to 2020 values beyond the int are 2147483647 (or its negative), from 2023 (unverified:
          the release between) they wrap around 2**32 (substr("hello", 1, 2147483650) is "");
        - the length of substr(s, m) is 2147483647 in 1.3.3 and the length of s in 1.3.4;
        - the length is computed in int arithmetic, so it can wrap past the int as well."""
        def wrap(value: int) -> int:
            return (value + 2 ** 31) % 2 ** 32 - 2 ** 31

        def to_int(value: float) -> int:
            if value != value:
                return 0
            if (self.version.split() + ["0"])[1] >= "20230000":
                # through a long: saturated at its range, then the low 32 bits
                return wrap(int(max(min(value, 2.0 ** 63 - 1), -2.0 ** 63)))
            return int(max(min(value, 2147483647.0), -2147483647.0))

        old = self.version.startswith("1.3.3")
        begin = to_int(start)
        size = to_int(length) if length is not None else 2147483647 if old else len(text)
        if begin < 1:
            size = wrap(size + begin - 1 if old else size - begin)
            begin = 1
        if size <= 0:
            return ""
        return text[begin - 1:begin - 1 + size]

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
        self.deadline = min(time.monotonic() + self.MAX_SECONDS, self.limit)
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


def tpot_awk_getopt(kind: str, option: str) -> str:
    """The getopt message of the C library of a BusyBox firmware ([shell] text_libc: glibc, uClibc, musl)."""
    libc = CowrieConfig.get("shell", "text_libc", fallback="glibc")
    if kind == "long":
        return {"musl": f"unrecognized option: {option[2:].partition('=')[0]}",
                "uclibc": f"unrecognized option `{option}'"}.get(libc, f"unrecognized option '{option}'")
    if kind == "argument":
        return {"musl": f"option requires an argument: {option}",
                "uclibc": f"option requires an argument -- {option}"}.get(
            libc, f"option requires an argument -- '{option}'")
    return {"musl": f"unrecognized option: {option}", "uclibc": f"invalid option -- {option}"}.get(
        libc, f"invalid option -- '{option}'")


def tpot_awk_busybox_usage(banner: str) -> str:
    head = f"{banner} multi-call binary.\n\n" if banner else ""
    found = re.match(r"BusyBox v(\d+)\.(\d+)", banner or "")
    version = (int(found.group(1)), int(found.group(2))) if found else (1, 24)
    # bb_show_usage before 1.21 ends with a blank line (recorded for 1.19.4)
    end = "\n" if version < (1, 21) else ""
    if version < (1, 15):
        return (f"{head}Usage: awk [OPTION]... [program-text] [FILE...]\n\nOptions:\n"
                "\t-v var=val\tSet variable\n\t-F sep\t\tUse sep as field separator\n"
                "\t-f file\t\tRead program from file\n" + end)
    text = (f"{head}Usage: awk [OPTIONS] [AWK_PROGRAM] [FILE]...\n\n\t-v VAR=VAL\tSet variable\n"
            "\t-F SEP\t\tUse SEP as field separator\n\t-f FILE\t\tRead program from FILE\n")
    # -e: BusyBox 1.24.1 built from its release tarball has it, the sources of 1.19.4 not (unverified between)
    return text + ("\t-e AWK_PROGRAM\n" if version >= (1, 24) else "") + end


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
        awk = TpotAwk(style, self.version, self.tpot_read, self.input_data, tpot_deadline(getattr(self, "protocol", None)))
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
                    return self.tpot_usage("", f"not an option: {arg}", tpot_awk_getopt("long", arg))
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
                                                       tpot_awk_getopt("argument", letter))
                            value, index = args[index], index + 1
                        pairs.append((letter, value))
                        break
                    if gawk and letter == "V":
                        return self.tpot_version()
                    if gawk and letter == "h":
                        awk.out.append(tpot_awk_gawk_usage(self.version))
                        return 0
                    if not (gawk and letter in "bcCdDgMnNoOpPrsStL"):
                        return self.tpot_usage("", f"not an option: -{letter}", tpot_awk_getopt("invalid", letter))
            for letter, value in pairs:
                if letter == "F":
                    value = tpot_awk_unescape(value, gawk)
                    awk.vars["FS"] = "\t" if gawk and value == "t" else value
                elif letter == "v":
                    if not TPOT_AWK_ASSIGNMENT.match(value):
                        return self.tpot_usage(f"`{value}' argument to `-v' not in `var=value' form\n",
                                               f"improper assignment: -v {value}")
                    try:
                        awk.assign_operand(value)
                    except TpotAwkStop as stop:  # a variable left out (RS, SUBSEP): the usage error of run()
                        awk.err.append(stop.text)
                        return stop.code
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
                 'commands["/bin/awk"] = Command_awk\ncommands["awk"] = Command_awk\n' + AWK_COMMAND
                 + tpot_metering("Command_tpot_awk"))


# module code of the patched commands/wc.py after its commands: wc, sort and seq of the persona
TEXT_COMMAND = r'''

# T-Pot: wc, sort and seq of the persona. [shell] text_style gnu: GNU coreutils (text_version,
# text_year, text_packager, text_collate c or en for the order of sort); text_style busybox: the
# BusyBox of the firmware (text_banner, text_version, text_libc glibc, uclibc or musl for the texts
# of option errors, text_unicode). They read files of the emulated file system and the data of a pipe
# or of the terminal; input, output, lines and keys have limits.
import posixpath
import stat
import time
import unicodedata
from decimal import Context, Decimal, InvalidOperation, localcontext

from twisted.python import log

from cowrie.core.config import CowrieConfig
from cowrie.shell import fs as tpot_shell_fs
from cowrie.shell.command import tpot_deadline


def tpot_may_write(node, uid: int, gid: int) -> bool:
    """Whether a user may write a file, or make one in a folder (write and search), by the owner,
    group and mode of its node; root always may. A path without a node is writable."""
    if uid == 0 or not isinstance(node, list):
        return True
    mode = int(node[tpot_shell_fs.A_MODE] or 0)
    need = 0o3 if node[tpot_shell_fs.A_TYPE] == tpot_shell_fs.T_DIR else 0o2
    shift = 6 if node[tpot_shell_fs.A_UID] == uid else 3 if node[tpot_shell_fs.A_GID] == gid else 0
    return (mode >> shift) & need == need


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
# bytes the files sort -o writes may hold in the emulated file system, for all of a session
TPOT_WRITE_BUDGET = 4 << 20
# the words of C wc (blanks: space, \t, \n, \v, \f, \r) without a list of them: blanks to " ", the rest
# to "x"; a word starts at each " x"
TPOT_WC_C_WORD_TABLE = bytes(0x20 if b in b" \t\n\v\f\r" else 0x78 for b in range(256))
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
    if "'" in name and not re.search(r'["$`\\!]', name):
        # gnulib's shell-escape style: a name with a single quote in double quotes
        return '"' + name + '"'
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
        self.tpot_deadline = min(time.monotonic() + TPOT_TEXT_SECONDS, tpot_deadline(getattr(self, "protocol", None)))
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
        """Write data as a file of the emulated file system (sort -o); the error text, "" if written.
        As the persona's sort opens it: a file or a new file's folder the user may not write is
        "Permission denied" (root and the command sudo runs may); a new file is a regular file of
        mode 0644 of its user, an existing one keeps its owner and mode. The files of a session
        share TPOT_WRITE_BUDGET bytes: past it the part that fits is written and the error is the
        one of a full disk."""
        used = getattr(self.protocol, "tpot_written", 0)
        room = max(0, TPOT_WRITE_BUDGET - (used if isinstance(used, int) else 0))
        full = len(data) > room
        data = data[:room]
        path = self.fs.resolve_path(name, self.cwd)
        if self.fs.isdir(path):
            return "Is a directory"
        if not self.fs.isdir(posixpath.dirname(path)):
            return "No such file or directory"
        sudo = bool(getattr(getattr(self, "pp", None), "tpot_sudo", False))
        uid, gid = (0, 0) if sudo else (self.user.get("uid", 0), self.user.get("gid", 0))
        private = getattr(self.fs, "_ensure_private_fs", None)
        if callable(private):
            private()  # the session's own copy of the tree before a node changes
        node = self.fs.getfile(path)
        if isinstance(node, list) and node[tpot_shell_fs.A_TYPE] == tpot_shell_fs.T_FILE:
            if not tpot_may_write(node, uid, gid):
                return "Permission denied"
        else:
            if not tpot_may_write(self.fs.getfile(posixpath.dirname(path)), uid, gid):
                return "Permission denied"
            try:
                self.fs.mkfile(path, uid, gid, 0, stat.S_IFREG | 0o644)
            except Exception:  # noqa: BLE001 - the file system refuses the path
                return "No such file or directory"
            node = self.fs.getfile(path)
        if isinstance(node, list):
            node[tpot_shell_fs.A_CONTENTS] = bytes(data[:TPOT_TEXT_INPUT])
            node[tpot_shell_fs.A_REALFILE] = None  # the new text, not the file of the honeyfs
            node[tpot_shell_fs.A_SIZE] = len(data)
            node[tpot_shell_fs.A_CTIME] = time.time()  # the time of the write, as ls -l shows it
        try:
            self.protocol.tpot_written = TPOT_WRITE_BUDGET - room + len(data)
        except Exception:  # noqa: BLE001 - a protocol that takes no attribute: no budget across commands
            pass
        return "No space left on device" if full else ""

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
        # bb_show_usage before 1.21 ends with a blank line (recorded for 1.19.4)
        end = "\n" if self.version < (1, 21) else ""
        return TpotTextStop(f"{prefix}{head}\nUsage: {self.tool} {self.tpot_busybox_usage()}{end}", self.status)

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
        if self.version < (8, 31):
            # the program's name in the translation line up to 8.30, https from 8.29 (8.23 of Debian 8, 8.26 of
            #   Debian 9, 8.28 of Ubuntu 18.04, 8.30 of Debian 10; 8.31 of Alpine 3.11 writes the form below)
            web = "http" if self.version < (8, 29) else "https"
            return (f"\nGNU coreutils online help: <{web}://www.gnu.org/software/coreutils/>\n"
                    + ("" if self.collate.startswith("en") else
                       f"Report {name} translation bugs to <{web}://translationproject.org/team/>\n")
                    + f"Full documentation at: <{web}://www.gnu.org/software/coreutils/{name}>\n"
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
                f"License GPLv3+: GNU GPL version 3 or later <{'http' if self.version < (8, 29) else 'https'}"
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
        words = tpot_wc_c_words(data)
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
    words = tpot_wc_c_words(data) - len(TPOT_TEXT_C_NO_PRINT_RUNS.findall(data))
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
    counts bytes as characters. late(): the time is used up, the rest is not counted. The data is
    read by offsets: no copy of it beyond one piece."""
    totals, at = [0, 0, 0, 0, 0], 0
    head, tail_end = min(len(data), TPOT_WC_INPUT), min(len(data), TPOT_WC_TAIL)
    while at < head:
        end = min(head, at + TPOT_WC_PIECE)
        joined = False
        if end < head:
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
    if tail_end > head:
        # the rest of a large input: lines, words between ASCII blanks (one that goes on from the head
        # counts once), characters, bytes
        bytes_only = posix or (not gnu and not unicode)
        words, chars, before = 0, 0, data[head - 1:head] if head else b" "
        for at in range(head, tail_end, TPOT_WC_PIECE):
            piece = data[at:min(tail_end, at + TPOT_WC_PIECE)]
            words += tpot_wc_c_words(piece, before)
            chars += len(piece) if bytes_only else len(piece.translate(None, TPOT_TEXT_CONTINUATION))
            before = piece[-1:]
        totals = [totals[0] + data.count(b"\n", head, tail_end), totals[1] + words, totals[2] + chars,
                  totals[3] + tail_end - head, totals[4]]
    return totals


def tpot_wc_c_words(data: bytes, before: bytes = b" ") -> int:
    """The words of data between ASCII blanks (as len(data.split())), counted in pieces of
    TPOT_WC_PIECE bytes with no list of them; before: the byte ahead of the data (a word that goes on
    from it is not counted again)."""
    words = 0
    before = before[-1:].translate(TPOT_WC_C_WORD_TABLE) or b" "
    for at in range(0, len(data), TPOT_WC_PIECE):
        mapped = data[at:at + TPOT_WC_PIECE].translate(TPOT_WC_C_WORD_TABLE)
        words += mapped.count(b" x") + (before == b" " and mapped[:1] == b"x")
        before = mapped[-1:]
    return words


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
        each = "FILEs" if self.version >= (1, 34) else "each FILE"  # busybox:1.33.1 / 1.34.1
        return (f"[-c{'m' if self.unicode else ''}lwL] [FILE]...\n\n"
                f"Count lines, words, and bytes for {each} (or stdin)\n\n\t-c\tCount bytes\n"
                + ("\t-m\tCount characters\n" if self.unicode else "")
                + "\t-l\tCount newlines\n\t-w\tCount words\n\t-L\tPrint longest line length\n")

    def tpot_gnu_help(self) -> str:
        v = self.version
        if v < (8, 24):
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
        longest = "print the length of the longest line" if v < (8, 24) else "print the maximum display width"
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
        zero = "" if self.version < (8, 24) else \
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
        return "".join(out)  # coreutils prints the backslashes of FORMAT as they are

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
        return ("ngh" if self.version >= (1, 36) else "ng") + ("MV" if self.version >= (1, 30) else "M") + \
            "ucszbrdfimS:T:o:k:t:"

    def tpot_busybox_usage(self) -> str:
        v = self.version
        if v < (1, 24):
            # the sources of 1.14.1 and 1.19.4 (1.14 heads the list with "Options:")
            return ("[-nrugMcszbdfimSTokt] [-o FILE] [-k start[.offset][opts][,end[.offset][opts]] [-t CHAR] "
                    "[FILE]...\n\nSort lines of text\n\n" + ("Options:\n" if v < (1, 15) else "")
                    + "\t-b\tIgnore leading blanks\n\t-c\tCheck whether input is "
                    "sorted\n\t-d\tDictionary order (blank or alphanumeric only)\n\t-f\tIgnore case\n\t-g\tGeneral "
                    "numerical sort\n\t-i\tIgnore unprintable characters\n\t-k\tSort key\n\t-M\tSort month\n\t-n\t"
                    "Sort numbers\n\t-o\tOutput to file\n\t-k\tSort by key\n\t-t CHAR\tKey separator\n\t-r\tReverse "
                    "sort order\n\t-s\tStable (don't sort ties alphabetically)\n\t-u\tSuppress duplicate lines\n\t-z\t"
                    "Lines are terminated by NUL, not newline\n\t-mST\tIgnored for GNU compatibility\n")
        if v < (1, 25):
            # BusyBox 1.24.1 built from its release tarball (unverified: the releases between 1.19 and 1.24)
            return ("[-nrugMcszbdfimSTokt] [-o FILE] [-k start[.offset][opts][,end[.offset][opts]] [-t CHAR] [FILE]...\n"
                    "\n"
                    "Sort lines of text\n"
                    "\n"
                    "\t-b\tIgnore leading blanks\n"
                    "\t-c\tCheck whether input is sorted\n"
                    "\t-d\tDictionary order (blank or alphanumeric only)\n"
                    "\t-f\tIgnore case\n"
                    "\t-g\tGeneral numerical sort\n"
                    "\t-i\tIgnore unprintable characters\n"
                    "\t-M\tSort month\n"
                    "\t-n\tSort numbers\n"
                    "\t-o\tOutput to file\n"
                    "\t-t CHAR\tField separator\n"
                    "\t-k N[,M] Sort by Nth field\n"
                    "\t-r\tReverse sort order\n"
                    "\t-s\tStable (don't sort ties alphabetically)\n"
                    "\t-u\tSuppress duplicate lines\n"
                    "\t-z\tLines are terminated by NUL, not newline\n"
                    "\t-mST\tIgnored for GNU compatibility\n")
        head = ("[-nru" + ("gh" if v >= (1, 36) else "g") + "Mcszbdfiokt] [-o FILE] [-k "
                + ("START[.OFS][OPTS][,END[.OFS][OPTS]]" if v >= (1, 33) else "start[.offset][opts][,end[.offset][opts]]")
                + " [-t CHAR] [FILE]...\n\nSort lines of text\n\n\t-o FILE\tOutput to FILE\n\t-c\tCheck whether input is"
                " sorted\n\t-b\tIgnore leading blanks\n\t-f\tIgnore case\n\t-i\tIgnore unprintable characters\n\t-d\t"
                "Dictionary order (blank or alphanumeric only)\n")
        if v < (1, 29):
            # busybox:1.25.1 to 1.28.4; from 1.29.3 -n heads the numeric keys, -V comes with 1.30.1
            return head + ("\t-g\tGeneral numerical sort\n\t-M\tSort month\n\t-n\tSort numbers\n\t-t CHAR\tField "
                           "separator\n\t-k N[,M] Sort by Nth field\n\t-r\tReverse sort order\n\t-s\tStable (don't sort "
                           "ties alphabetically)\n\t-u\tSuppress duplicate lines\n\t-z\tLines are terminated by NUL, not "
                           "newline\n")
        return head + ("\t-n\tSort numbers\n\t-g\tGeneral numerical sort\n"
                       + ("\t-h\tSort human readable numbers (2K 1G)\n" if v >= (1, 36) else "")
                       + "\t-M\tSort month\n" + ("\t-V\tSort version\n" if v >= (1, 30) else "")
                       + "\t-t CHAR\tField separator\n\t-k N[,M] Sort by Nth field\n\t-r\tReverse sort order\n"
                       "\t-s\tStable (don't sort ties alphabetically)\n\t-u\tSuppress duplicate lines\n\t-z\t" + ("NUL terminated input and output" if v >= (1, 34) else
                                                   "Lines are terminated by NUL, not newline") + "\n")

    def tpot_gnu_help(self) -> str:
        v = self.version
        old = v < (8, 24)
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
                + ("  -z, --zero-terminated     end lines with 0 byte, not newline\n" if v < (8, 23) else
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
        shown = tpot_text_quotef(target) if self.version >= (8, 23) else target
        if error == "No space left on device":
            # as in containers with a full tmpfs: coreutils fails the write and the close of stdout;
            # BusyBox fails the flush of stdout (exit 2 from 1.36), uClibc's says nothing
            if self.gnu:
                raise TpotTextStop(f"sort: write failed: {shown}: {error}\nsort: write error\n", 2)
            if self.libc == "uclibc":
                return 0
            raise TpotTextStop(f"sort: standard output: {error}\n", 2 if self.version >= (1, 36) else 0)
        if error:
            if self.gnu:
                raise TpotTextStop(f"sort: open failed: {shown}: {error}\n", 2)
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
                 'commands["wc"] = Command_wc\n' + TEXT_COMMAND
                 + tpot_metering("TpotTextTool", "Command_tpot_wc", "Command_tpot_seq", "Command_tpot_sort"))


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
            elif CowrieConfig.get("shell", "prompt_style", fallback="debian") == "qnap":
                # T-Pot: QTS (/etc/profile): [\\w] \\$ -> "[~] # "
                prompt = f"[{cwd}] "
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
        """        if cmd[:1] in (".", "/"):
            path = self.protocol.fs.resolve_path(cmd, self.cwd)
""",
        """        if cmd[:1] in (".", "/") and CowrieConfig.get("shell", "not_found_style", fallback="bash") == "ash":
            # T-Pot: BusyBox ash names a folder "Permission denied" and a missing path "not found"
            path = self.protocol.fs.resolve_path(cmd, self.cwd)
            if self.protocol.fs.isdir(path):
                return f"{self.error_prefix()}{cmd}: Permission denied\\n"
            if not self.protocol.fs.exists(path):
                return f"{self.error_prefix()}{cmd}: not found\\n"
        if cmd[:1] in (".", "/"):
            path = self.protocol.fs.resolve_path(cmd, self.cwd)
""",
    )
    replace_once(
        path,
        """        return f"{self.error_prefix()}{cmd}: command not found\\n"
""",
        """        if CowrieConfig.get("shell", "not_found_style", fallback="bash") == "ash":
            return f"{self.error_prefix()}{cmd}: not found\\n"
        # T-Pot: command-not-found of Ubuntu in an interactive shell (the command_not_found_handle of
        # /etc/bash.bashrc); bash -c and scripts have none
        if not self.line_numbers and cmd[:1] not in (".", "/") and CowrieConfig.get(
            "shell", "command_not_found", fallback=""
        ):
            user = getattr(getattr(self, "protocol", None), "user", None)
            advice = tpot_command_not_found(cmd, getattr(user, "uid", 0), getattr(user, "username", ""))
            if advice is not None:
                # what the handler prints to stdout follows its error (_write_shell_error)
                self.tpot_cnf_stdout = advice[1].encode("utf-8")
                return advice[0]
            # the handler's own text for a command it does not know: no name of the shell
            return f"{cmd}: command not found\\n"
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


AUTH_REFUSED = '''

def tpot_refused_pairs(value: str) -> tuple[frozenset[tuple[bytes, bytes]], int]:
    """T-Pot: the login pairs of [honeypot] auth_refused_pairs, a JSON list of [login, password]
    (empty: none), and the number of entries of another form, which are left out. These pairs
    never log in. ValueError for a value that is no JSON list."""
    pairs = json.loads(value) if value.strip() else []
    if not isinstance(pairs, list):
        raise ValueError("auth_refused_pairs is not a list of [login, password]")
    kept = [
        pair
        for pair in pairs
        if isinstance(pair, list) and len(pair) == 2 and all(isinstance(part, str) for part in pair)
    ]
    return (
        frozenset((login.encode("utf-8"), password.encode("utf-8")) for login, password in kept),
        len(pairs) - len(kept),
    )


import functools  # noqa: E402 - T-Pot: tpot_refused_logins


@functools.cache
def tpot_refused_logins() -> frozenset[tuple[bytes, bytes]]:
    """T-Pot: the pairs of [honeypot] auth_refused_pairs, read and parsed once (the configuration
    does not change while Cowrie runs, as with the shell's history size). A value of another form
    is logged once and ignored: AuthRandom then works as upstream. An entry of another form is
    left out (one warning); the other pairs of the list stay refused."""
    try:
        pairs, left_out = tpot_refused_pairs(CowrieConfig.get("honeypot", "auth_refused_pairs", fallback=""))
    except ValueError:
        Logger().warn("[honeypot] auth_refused_pairs is not a list of [login, password], ignored")
        return frozenset()
    if left_out:
        entries = "entry is" if left_out == 1 else "entries are"
        Logger().warn(f"[honeypot] auth_refused_pairs: {left_out} {entries} not [login, password], left out")
    return pairs


class AuthRandom:
'''

AUTH_CHECK = '''        # T-Pot: a pair of [honeypot] auth_refused_pairs never logs in; it is matched exactly and
        # leaves the state (source addresses, tries, cache) as it is, so it changes no other login
        if (thelogin, thepasswd) in tpot_refused_logins():
            self._log.info("login pair of auth_refused_pairs, refused")
            return False

        auth: bool = False
        # Decode once and store/compare the text form everywhere, so an
'''


def patch_auth(cowrie_root):
    """AuthRandom refuses the pairs of [honeypot] auth_refused_pairs (rendered by tpot_personas,
    render.REFUSED_LOGINS; parsed once) on every attempt, before it changes its state; every other
    pair takes upstream's path. The login events stay those of core/checkers.py. Only AuthRandom
    checks the list: render.py writes auth_class = AuthRandom for every persona and the validator
    expects it."""
    path = cowrie_root / "src" / "cowrie" / "core" / "auth.py"
    replace_once(path, "\n\nclass AuthRandom:\n", AUTH_REFUSED)
    replace_once(
        path,
        "        auth: bool = False\n        # Decode once and store/compare the text form everywhere, so an\n",
        AUTH_CHECK,
    )


# core/ttylog.py after TTYSTRUCT: the tty logs ttylog_open opened, up to their ttylog_close
TTYLOG_FILES = '''TTYSTRUCT = "<iLiiLL"

# T-Pot: the tty logs ttylog_open opened (path -> file), up to their ttylog_close. A record is a
# write into the buffer of its file; the buffers are written out once per reactor turn and at the
# close, not with an open and a close of the file for every record (every echoed key is one).
_tpot_files: dict = {}
_tpot_unflushed: set = set()
_tpot_flush_pending = False


def _tpot_schedule(function) -> None:
    """Run function after this turn of the reactor."""
    from twisted.internet import reactor

    reactor.callLater(0, function)


def _tpot_flush() -> None:
    """Write the buffered records of the open tty logs out."""
    global _tpot_flush_pending
    _tpot_flush_pending = False
    failed = None
    while _tpot_unflushed:
        f = _tpot_files.get(_tpot_unflushed.pop())
        if f is None:
            continue
        try:
            f.flush()
        except OSError as error:
            failed = failed or error
    if failed is not None:
        raise failed


def _tpot_record(logfile: str, *parts: bytes) -> bool:
    """Append parts to the open tty log logfile; False when ttylog_open did not open it."""
    global _tpot_flush_pending
    f = _tpot_files.get(logfile)
    if f is None:
        return False
    for part in parts:
        f.write(part)
    _tpot_unflushed.add(logfile)
    if not _tpot_flush_pending:
        _tpot_flush_pending = True
        _tpot_schedule(_tpot_flush)
    return True
'''


def patch_ttylog(cowrie_root):
    """The tty log of a session stays open from ttylog_open to ttylog_close (TTYLOG_FILES): a record is
    a write into its buffer, written out once per reactor turn and at the close. Records and files stay
    those of upstream. ttylog_inputhash hashes the input while it reads it (linear in the records)."""
    path = cowrie_root / "src" / "cowrie" / "core" / "ttylog.py"
    replace_once(path, 'TTYSTRUCT = "<iLiiLL"\n', TTYLOG_FILES)
    replace_once(
        path,
        """    with open(logfile, "ab") as f:
        sec, usec = int(stamp), int(1000000 * (stamp - int(stamp)))
        f.write(struct.pack(TTYSTRUCT, OP_OPEN, 0, 0, 0, sec, usec))
""",
        """    # T-Pot: open up to ttylog_close, with a buffer of 8 KiB (Python 3.14 would take its
    # io.DEFAULT_BUFFER_SIZE of 128 KiB for every open session)
    f = open(logfile, "ab", buffering=8192)  # noqa: SIM115
    try:
        sec, usec = int(stamp), int(1000000 * (stamp - int(stamp)))
        f.write(struct.pack(TTYSTRUCT, OP_OPEN, 0, 0, 0, sec, usec))
        f.flush()
    except BaseException:
        f.close()
        raise
    _tpot_unflushed.discard(logfile)
    previous = _tpot_files.pop(logfile, None)
    if previous is not None:
        previous.close()
    _tpot_files[logfile] = f
""",
    )
    replace_once(
        path,
        """    with open(logfile, "ab") as f:
        sec, usec = int(stamp), int(1000000 * (stamp - int(stamp)))
        f.write(struct.pack(TTYSTRUCT, OP_WRITE, 0, length, direction, sec, usec))
        f.write(data)
""",
        """    sec, usec = int(stamp), int(1000000 * (stamp - int(stamp)))
    head = struct.pack(TTYSTRUCT, OP_WRITE, 0, length, direction, sec, usec)
    if _tpot_record(logfile, head, data):
        return
    with open(logfile, "ab") as f:
        f.write(head)
        f.write(data)
""",
    )
    replace_once(
        path,
        """    with open(logfile, "ab") as f:
        sec, usec = int(stamp), int(1000000 * (stamp - int(stamp)))
        f.write(struct.pack(TTYSTRUCT, OP_CLOSE, 0, 0, 0, sec, usec))
""",
        """    _tpot_unflushed.discard(logfile)
    f = _tpot_files.pop(logfile, None)
    if f is None:
        f = open(logfile, "ab")  # noqa: SIM115 - closed by the with below
    with f:
        sec, usec = int(stamp), int(1000000 * (stamp - int(stamp)))
        f.write(struct.pack(TTYSTRUCT, OP_CLOSE, 0, 0, 0, sec, usec))
""",
    )
    replace_once(
        path,
        '    inputbytes: bytes = b""\n',
        """    digest = hashlib.sha256()
    if logfile in _tpot_files:
        _tpot_files[logfile].flush()
""",
    )
    replace_once(path, "            inputbytes = inputbytes + data\n", "            digest.update(data)\n")
    replace_once(path, "        shasum: str = hashlib.sha256(inputbytes).hexdigest()\n",
                 "        shasum: str = digest.hexdigest()\n")


def patch_line_input(cowrie_root):
    """A line of the interactive shell holds at most TPOT_LINE_MAX (64 KiB) bytes. A key beyond it is
    echoed as bash echoes it but not kept: the session goes on, the line runs and is logged
    (cowrie.command.input) cut to the bound. Past the bound the screen holds more than the buffer:
    backspace, HOME, ctrl-U, the history and tab completion work only on the kept 64 KiB. Below it,
    characterReceived acts as upstream.
    The history keeps the last [shell] history_size lines (history_size_root for uid 0), the persona's
    HISTSIZE (tpot_personas.system.history_sizes); without a size it grows as upstream. Below the cap,
    up / down and `history` act as upstream. Runs after patch_protocol_load."""
    path = cowrie_root / "src" / "cowrie" / "shell" / "protocol.py"
    replace_once(
        path,
        "class HoneyPotInteractiveProtocol(HoneyPotBaseProtocol, recvline.HistoricRecvLine):\n",
        """@functools.cache
def tpot_history_size(root: bool) -> int:
    \"\"\"T-Pot: the lines the shell's history keeps ([shell] history_size, history_size_root for
    root; parsed once), 0 for no limit. It holds at most 1000 lines whatever HISTSIZE says (EdgeOS:
    10000), which bounds the memory of a session's history.\"\"\"
    value = CowrieConfig.get("shell", "history_size_root" if root else "history_size", fallback="")
    try:
        size = max(int(value), 0)
    except (TypeError, ValueError):
        return 0
    return min(size, 1000) if size else 0


@functools.cache
def tpot_history_control(root: bool) -> frozenset[str]:
    \"\"\"T-Pot: what the shell's history leaves out ([shell] history_control, history_control_root
    for root; HISTCONTROL of bash, colon separated, ignoreboth for both; parsed once): ignoredups (a
    line as the one before), ignorespace (a line that starts with a space).\"\"\"
    value = CowrieConfig.get("shell", "history_control_root" if root else "history_control", fallback="")
    words = {word.strip() for word in (value or "").split(":")}
    if "ignoreboth" in words:
        words |= {"ignoredups", "ignorespace"}
    return frozenset(words & {"ignoredups", "ignorespace"})


class HoneyPotInteractiveProtocol(HoneyPotBaseProtocol, recvline.HistoricRecvLine):
    # T-Pot: the longest line the line editor keeps (bytes)
    TPOT_LINE_MAX = 65536

""",
    )
    replace_once(
        path,
        """        if self.mode == "insert":
            self.lineBuffer.insert(self.lineBufferIndex, ch)
        else:
            self.lineBuffer[self.lineBufferIndex : self.lineBufferIndex + 1] = [ch]
        self.lineBufferIndex += 1
""",
        """        # T-Pot: a line holds at most TPOT_LINE_MAX bytes; a key beyond it is echoed as bash
        # echoes it, but not kept (the line runs and is logged cut to the bound)
        if len(self.lineBuffer) < self.TPOT_LINE_MAX or (
            self.mode != "insert" and self.lineBufferIndex < len(self.lineBuffer)
        ):
            if self.mode == "insert":
                self.lineBuffer.insert(self.lineBufferIndex, ch)
            else:
                self.lineBuffer[self.lineBufferIndex : self.lineBufferIndex + 1] = [ch]
            self.lineBufferIndex += 1
""",
    )
    replace_once(
        path,
        """                self.historyLines.append(b"".join(self.lineBuffer))
            self.historyPosition = len(self.historyLines)
""",
        """                # T-Pot: HISTCONTROL of the persona's shell leaves out a repeated line or one that
                # starts with a space; the history keeps the last HISTSIZE lines, `history` numbers
                # them on from those it dropped (tpot_history_base)
                root = getattr(self.user, "uid", None) == 0
                line = b"".join(self.lineBuffer)
                control = tpot_history_control(root)
                if not (
                    ("ignorespace" in control and line[:1] == b" ")
                    or ("ignoredups" in control and self.historyLines and self.historyLines[-1] == line)
                ):
                    self.historyLines.append(line)
                    # the time of the line, for HISTTIMEFORMAT (patch_history)
                    times = getattr(self, "tpot_history_times", None)
                    if times is None:
                        times = self.tpot_history_times = []
                    times.append(time.time())
                size = tpot_history_size(root)
                if size and len(self.historyLines) > size:
                    dropped = len(self.historyLines) - size
                    del self.historyLines[:dropped]
                    del getattr(self, "tpot_history_times", [])[:dropped]
                    self.tpot_history_base = getattr(self, "tpot_history_base", 0) + dropped
            self.historyPosition = len(self.historyLines)
""",
    )


def patch_line_echo(cowrie_root):
    """A move in the history (up / down: HOME, the erase and the line) and the erase of ctrl-U reach
    the terminal in one write, as bash redraws them at once; upstream writes one key (or one pair of
    escapes) at a time, one SSH packet and one tty log record each. The bytes stay upstream's."""
    path = cowrie_root / "src" / "cowrie" / "shell" / "protocol.py"
    replace_once(
        path,
        """    def handle_CTRL_U(self) -> None:
        assert self.terminal is not None
        for _ in range(self.lineBufferIndex):
            self.terminal.cursorBackward()
            self.terminal.deleteCharacter()
        self.lineBuffer = self.lineBuffer[self.lineBufferIndex :]
        self.lineBufferIndex = 0
""",
        """    def tpot_one_write(self, function, *args):
        \"\"\"T-Pot: run function with the terminal's writes taken together into one write.\"\"\"
        terminal = self.terminal
        if terminal is None or "write" in vars(terminal):
            return function(*args)
        parts: list[bytes] = []
        terminal.write = parts.append
        try:
            return function(*args)
        finally:
            del terminal.write
            if parts:
                terminal.write(b"".join(parts))

    def _deliverBuffer(self, buf):
        # T-Pot: a line of the history in one write
        self.tpot_one_write(recvline.HistoricRecvLine._deliverBuffer, self, buf)

    def handle_UP(self):
        # T-Pot: the move to the line before in one write (HOME, erase, the line), Twisted's bytes
        self.tpot_one_write(recvline.HistoricRecvLine.handle_UP, self)

    def handle_DOWN(self):
        # T-Pot: the move to the line after in one write, Twisted's bytes
        self.tpot_one_write(recvline.HistoricRecvLine.handle_DOWN, self)

    def handle_CTRL_U(self) -> None:
        assert self.terminal is not None

        def erase():
            for _ in range(self.lineBufferIndex):
                self.terminal.cursorBackward()
                self.terminal.deleteCharacter()

        # T-Pot: the erase in one write
        self.tpot_one_write(erase)
        self.lineBuffer = self.lineBuffer[self.lineBufferIndex :]
        self.lineBufferIndex = 0
""",
    )


def patch_history(cowrie_root):
    """`history` numbers the lines as the persona's shell does: bash from the number of the first line
    kept (the lines dropped at HISTSIZE count, tpot_history_base; history -c starts at 1 again),
    BusyBox ash ([shell] not_found_style = ash) "%4d %s" from 0."""
    path = cowrie_root / "src" / "cowrie" / "commands" / "base.py"
    replace_once(
        path,
        """                self.protocol.historyLines = []
                self.protocol.historyPosition = 0
                return
            count = 1
            for line in self.protocol.historyLines:
                # A typed line need not be valid UTF-8.
                self.write(f" {count:4d}  {line.decode(errors='replace')}\\n")
                count += 1
""",
        """                self.protocol.historyLines = []
                self.protocol.historyPosition = 0
                self.protocol.tpot_history_base = 0
                self.protocol.tpot_history_times = []
                return
            # T-Pot: bash numbers on from the lines HISTSIZE dropped, BusyBox ash from 0
            ash = CowrieConfig.get("shell", "not_found_style", fallback="bash") == "ash"
            count = 0 if ash else 1 + getattr(self.protocol, "tpot_history_base", 0)
            lines = self.protocol.historyLines
            # HISTTIMEFORMAT of the shell (the login sets the persona's, EdgeOS; `unset HISTTIMEFORMAT` takes
            #   the times away as in bash): each line after the time it was typed, in UTC as /etc/localtime
            environ = getattr(self, "environ", None) or {}
            stamp = "" if ash else str(environ.get("HISTTIMEFORMAT", "") or "")[:64]
            if stamp:
                import time as tpot_time

                stamp = stamp.replace("%F", "%Y-%m-%d").replace("%T", "%H:%M:%S").replace("%z", "+0000")
            times = list(getattr(self.protocol, "tpot_history_times", []))[-len(lines):] if lines else []
            times = [None] * (len(lines) - len(times)) + times
            word = self.args[0] if self.args and not ash else ""
            digits = word.lstrip("0") or "0"
            if word and not word.startswith("-") and not (word.isascii() and word.isdigit() and len(digits) < 20
                                                          and int(digits) < 1 << 63):
                # bash's count is a number of intmax_t (legal_number), else its error
                self.errorWrite(f"{self.shell.error_prefix()}history: {word}: numeric argument required\\n")
                self.exit_code = 1
                return
            if word and not word.startswith("-"):
                # bash's history N: the last N lines (BusyBox ash takes no count)
                skip = max(len(lines) - int(digits), 0)
                count += skip
                lines = lines[skip:]
                times = times[skip:]
            for line, typed in zip(lines, times):
                # A typed line need not be valid UTF-8.
                if ash:
                    self.write(f"{count:4d} {line.decode(errors='replace')}\\n")
                else:
                    when = ""
                    if stamp:
                        # a line without its time: "??", as histtime of bash 4.4
                        try:
                            when = "??" if typed is None else tpot_time.strftime(stamp, tpot_time.gmtime(typed))
                        except ValueError:
                            when = "??"
                    self.write(f" {count:4d}  {when}{line.decode(errors='replace')}\\n")
                count += 1
""",
    )


TYPE_COMMAND = '''

# T-Pot: the session's time (TPOT_BUDGET) of type
from cowrie.shell.command import tpot_deadline  # noqa: E402


class Command_tpot_type(HoneyPotCommand):
    """T-Pot: type of BusyBox ash (describe_command of shell/ash.c; recorded with the ash of BusyBox
    v1.19.4 of the Archer C7 v5): keywords, special and regular builtins ([shell] builtins, or the table
    of 1.19.4 with history), the first program of PATH, "NAME: not found" on stdout and status 127.
    On the other personas type of bash (tpot_bash)."""

    SPECIAL = frozenset({".", ":", "break", "continue", "eval", "exec", "exit", "export", "readonly", "return",
                         "set", "shift", "source", "times", "trap", "unset"})
    # the folders of PATH and the names of one call type looks through; past them, or past the session's time, a
    #   name is not found
    PATH_ENTRIES = 256
    LOOKUP_NAMES = 256
    KEYWORDS = frozenset({"!", "case", "do", "done", "elif", "else", "esac", "fi", "for", "if", "in", "then",
                          "until", "while", "{", "}"})
    BUILTINS = frozenset({".", ":", "[", "[[", "alias", "bg", "break", "cd", "chdir", "command", "continue",
                          "echo", "eval", "exec", "exit", "export", "false", "fg", "getopts", "hash", "help",
                          "history", "jobs", "kill", "let", "local", "printf", "pwd", "read", "readonly",
                          "return", "set", "shift", "source", "test", "times", "trap", "true", "type", "ulimit",
                          "umask", "unalias", "unset", "wait"})

    # compgen -k / -b of bash 4.2.46 (centos:7), 4.4.12, 5.1.4, 5.1.16 and 5.2.15 / 5.2.21: the same lists
    BASH_KEYWORDS = frozenset({"if", "then", "else", "elif", "fi", "case", "esac", "for", "select", "while", "until",
                               "do", "done", "in", "function", "time", "{", "}", "!", "[[", "]]", "coproc"})
    BASH_BUILTINS = frozenset({
        ".", ":", "[", "alias", "bg", "bind", "break", "builtin", "caller", "cd", "command", "compgen", "complete",
        "compopt", "continue", "declare", "dirs", "disown", "echo", "enable", "eval", "exec", "exit", "export",
        "false", "fc", "fg", "getopts", "hash", "help", "history", "jobs", "kill", "let", "local", "logout",
        "mapfile", "popd", "printf", "pushd", "pwd", "read", "readarray", "readonly", "return", "set", "shift",
        "shopt", "source", "suspend", "test", "times", "trap", "true", "type", "typeset", "ulimit", "umask",
        "unalias", "unset", "wait"})

    def call(self) -> None:
        self.tpot_until = tpot_deadline(getattr(self, "protocol", None))
        self.tpot_names = 0
        if CowrieConfig.get("shell", "not_found_style", fallback="bash") != "ash":
            self.tpot_bash()
            return
        value = CowrieConfig.get("shell", "builtins", fallback=None)
        builtins = frozenset(n.strip() for n in value.split(",") if n.strip()) if value else self.BUILTINS
        status = 0
        for name in self.args:
            if name in self.KEYWORDS:
                self.write(f"{name} is a shell keyword\\n")
            elif name in builtins:
                kind = "special shell builtin" if name in self.SPECIAL else "shell builtin"
                self.write(f"{name} is a {kind}\\n")
            else:
                found = self.tpot_find(name)
                if found is None:
                    self.write(f"{name}: not found\\n")
                    status = 127
                else:
                    self.write(f"{name} is {found}\\n")
        self.exit_code = status

    def tpot_bash(self) -> None:
        """type of bash (describe_command of builtins/type.def; recorded with bash 4.2 / 5.1 / 5.2): a
        keyword, a function (its first line), a builtin, the programs of PATH (-a all of them), -t the kind,
        -p / -P the path; a name it cannot find is an error with status 1, a wrong option status 2."""
        flags, names = set(), list(self.args)
        while names and names[0].startswith("-") and names[0] != "-":
            arg = names.pop(0)
            if arg == "--":
                break
            for letter in arg[1:]:
                if letter not in "afptP":
                    prefix = self.shell.error_prefix()
                    self.errorWrite(f"{prefix}type: -{letter}: invalid option\\n")
                    self.errorWrite("type: usage: type [-afptP] name [name ...]\\n")
                    self.exit_code = 2
                    return
                flags.add(letter)
        functions = {} if "f" in flags else getattr(self.shell, "functions", {})
        status = 0
        for name in names:
            kinds = []
            if "P" not in flags:
                if name in self.BASH_KEYWORDS:
                    kinds.append(("keyword", f"{name} is a shell keyword"))
                if name in functions:
                    kinds.append(("function", f"{name} is a function"))
                if name in self.BASH_BUILTINS:
                    kinds.append(("builtin", f"{name} is a shell builtin"))
            files = self.tpot_files(name)
            kinds += [("file", path) for path in files]
            if "a" not in flags:
                kinds = kinds[:1]
            if not kinds:
                status = 1
                if not flags & {"t", "p", "P"}:
                    self.errorWrite(f"{self.shell.error_prefix()}type: {name}: not found\\n")
                continue
            for kind, text in kinds:
                if "t" in flags:
                    self.write(f"{kind}\\n")
                elif flags & {"p", "P"}:
                    if kind == "file":
                        self.write(f"{text}\\n")
                else:
                    self.write(f"{name} is {text}\\n" if kind == "file" else f"{text}\\n")
        self.exit_code = status

    def tpot_spent(self) -> bool:
        """Past the names of a call or the session's time: the name is not found."""
        self.tpot_names = getattr(self, "tpot_names", 0) + 1
        return self.tpot_names > self.LOOKUP_NAMES or time.monotonic() >= getattr(self, "tpot_until", float("inf"))

    def tpot_files(self, name: str) -> list:
        """The programs bash finds for name: the name itself with a slash, else each folder of PATH."""
        if self.tpot_spent():
            return []
        if "/" in name:
            return [name] if self.fs.exists(name) and not self.fs.isdir(name) else []
        found = []
        for folder in self.environ.get("PATH", "").split(":")[:self.PATH_ENTRIES]:
            if time.monotonic() >= getattr(self, "tpot_until", float("inf")):
                return []
            path = f"{folder.rstrip('/')}/{name}"
            if folder and path not in found and self.fs.exists(path) and not self.fs.isdir(path):
                found.append(path)
        return found

    def tpot_find(self, name: str) -> str | None:
        if self.tpot_spent():
            return None
        if "/" in name:
            return name if self.fs.exists(name) and not self.fs.isdir(name) else None
        # the folders of a device with folders of its own ([shell] which_dirs, as which reads them)
        home = None
        table = CowrieConfig.get("shell", "which_dirs", fallback="")
        if table:
            try:
                with open(table, encoding="utf-8") as handle:
                    for row in handle.read().splitlines()[:4096]:
                        if row.count(" ") == 1 and row.split(" ", 1)[0] == name:
                            home = tuple(row.split(" ", 1)[1].split(","))
            except OSError:
                home = None
        for folder in self.environ.get("PATH", "").split(":")[:self.PATH_ENTRIES]:
            if time.monotonic() >= getattr(self, "tpot_until", float("inf")):
                return None
            if home is not None and folder.strip("/") not in home:
                continue
            path = f"{folder.rstrip('/')}/{name}"
            if folder and self.fs.exists(path) and not self.fs.isdir(path):
                return path
        return None


commands["type"] = Command_tpot_type
'''


def patch_type(cowrie_root):
    """type of BusyBox ash after the history command of base.py (TYPE_COMMAND)."""
    path = cowrie_root / "src" / "cowrie" / "commands" / "base.py"
    replace_once(path, 'commands["history"] = Command_history\n', 'commands["history"] = Command_history\n' + TYPE_COMMAND
                 + tpot_metering("Command_tpot_type"))


def patch_ls(cowrie_root):
    """ls writes one name per line when its stdout is no terminal of an interactive session (a
    pipe, a file, an exec session), as GNU and BusyBox ls do; in long format (-l) a link named on the
    command line is listed itself with its target (GNU ls dereferences no command line link then),
    without -l a link to a directory still lists the directory."""
    path = cowrie_root / "src" / "cowrie" / "commands" / "ls.py"
    replace_once(
        path,
        "        for arg in args:\n            resolved = self.protocol.fs.resolve_path(arg, self.cwd)\n",
        "        # T-Pot: -l lists a link of the command line itself\n"
        "        self.tpot_long = func == self.do_ls_l\n"
        "        for arg in args:\n            resolved = self.protocol.fs.resolve_path(arg, self.cwd)\n",
    )
    replace_once(
        path,
        "            if self.protocol.fs.isdir(path) and not self.showDirectories:\n",
        """            link = None
            if getattr(self, "tpot_long", False):
                link = self.protocol.fs.getfile(path, follow_symlinks=False)
                if link is not None and link[fs.A_TYPE] != fs.T_LINK:
                    link = None
            if self.protocol.fs.isdir(path) and not self.showDirectories and link is None:
""",
    )
    replace_once(
        path,
        "                file = self.protocol.fs.getfile(path)[:]\n",
        "                file = (link or self.protocol.fs.getfile(path))[:]\n",
    )
    replace_once(
        path,
        "        line = [x[fs.A_NAME] for x in files]\n        if not line:\n            return\n",
        """        line = [x[fs.A_NAME] for x in files]
        if not line:
            return
        # T-Pot: no terminal (a pipe, a file, an exec session): one name per line
        targets = getattr(getattr(self, "pp", None), "targets", None) or {}
        if (targets.get(1) or ("",))[0] != "terminal" or hasattr(self.protocol, "execcmd"):
            for f in line:
                self.write(f"{f}\\n")
            return
""",
    )


LS_LONG_OLD = """            line = "{} 1 {} {} {} {} {}{}".format(
                permstr,
                self.uid2name(file[fs.A_UID]).ljust(user_name_str_extent),
                self.gid2name(file[fs.A_GID]).ljust(group_name_str_extent),
                formatted_sizes[i].rjust(filesize_str_extent),
                time.strftime("%Y-%m-%d %H:%M", ctime),
                file[fs.A_NAME],
                linktarget,
            )
"""
LS_LONG_NEW = """            if CowrieConfig.get("shell", "ls_style", fallback="gnu") == "busybox":
                line = self.tpot_bb_long(permstr, file, formatted_sizes[i], linktarget)
            else:
""" + "".join("    " + row if row.strip() else row for row in LS_LONG_OLD.splitlines(keepends=True))
LS_TOTAL_OLD = """        if not files:
            return
        # Create a list to hold formatted sizes for display
"""
LS_TOTAL_NEW = """        # T-Pot: the total line of BusyBox from 1.16 with CONFIG_DESKTOP ([shell] ls_total) for a listed folder:
        #   (1 + its 512 byte blocks) / 2, a folder and a file in 4 KiB blocks, a link none
        if (files is not None and CowrieConfig.get("shell", "ls_style", fallback="gnu") == "busybox"
                and CowrieConfig.get("shell", "ls_total", fallback="no") == "yes" and not self.showDirectories
                and self.protocol.fs.isdir(path)):
            node = self.protocol.fs.getfile(path, follow_symlinks=False)
            if node is not None and node[fs.A_TYPE] != fs.T_LINK:
                blocks = 1 + sum(8 if f[fs.A_TYPE] == fs.T_DIR else -(-f[fs.A_SIZE] // 4096) * 8
                                 for f in files if f[fs.A_TYPE] in (fs.T_DIR, fs.T_FILE))
                self.write(f"total {blocks // 2}\\n")
""" + LS_TOTAL_OLD
LS_BB_LONG = """
    def tpot_bb_long(self, permstr, file, size, linktarget):
        \"\"\"T-Pot: a line of BusyBox ls -l (list_single of coreutils/ls.c; recorded with BusyBox v1.19.4 of
        the Archer C7 v5): "%-10s %4u %-8.8s %-8.8s %9s", the month and day, then the time for a file of the
        last half year (up to 15 minutes ahead) or the year. A folder counts its subfolders and . and ..\"\"\"
        links = 1
        if file[fs.A_TYPE] == fs.T_DIR:
            links = 2 + sum(1 for child in (file[fs.A_CONTENTS] or []) if child[fs.A_TYPE] == fs.T_DIR)
        stamp = file[fs.A_CTIME]
        age = time.time() - stamp
        clock = time.localtime(stamp)
        when = time.strftime("%b %e", clock) + (time.strftime(" %H:%M", clock) if -15 * 60 < age < 3600 * 24 * 365 / 2
                                               else time.strftime("  %Y", clock))
        user, group = self.uid2name(file[fs.A_UID]), self.gid2name(file[fs.A_GID])
        return f"{permstr} {links:4d} {user[:8]:<8} {group[:8]:<8} {size:>9} {when} {file[fs.A_NAME]}{linktarget}"
"""


def patch_ls_long(cowrie_root):
    """ls -l of BusyBox where its ls answers ([shell] ls_style = busybox, the BusyBox devices): its
    columns and its date (tpot_bb_long); GNU's line of upstream elsewhere."""
    path = cowrie_root / "src" / "cowrie" / "commands" / "ls.py"
    replace_once(path, "from cowrie.shell import fs\n", "from cowrie.core.config import CowrieConfig\nfrom cowrie.shell import fs\n")
    replace_once(path, LS_LONG_OLD, LS_LONG_NEW)
    replace_once(path, LS_TOTAL_OLD, LS_TOTAL_NEW)
    replace_once(path, "    def do_ls_l(self, path: str, name: str) -> None:\n",
                 LS_BB_LONG.lstrip("\n") + "\n    def do_ls_l(self, path: str, name: str) -> None:\n")


BB_USAGE = """

# T-Pot: the usage of BusyBox where the persona has its recorded texts ([shell] busybox_usage)
import functools as tpot_functools  # noqa: E402
import json as tpot_json  # noqa: E402

from cowrie.core.config import CowrieConfig  # noqa: E402


@tpot_functools.cache
def tpot_bb_usage_table(path: str) -> dict:
    \"\"\"T-Pot: {applet: {usage, flags, values, numbers, noargs}} of the persona's BusyBox, read once.\"\"\"
    try:
        with open(path, encoding="utf-8") as handle:
            return tpot_json.load(handle).get("applets", {})
    except (OSError, ValueError, AttributeError):
        return {}


def tpot_bb_usage_getopt(applet: str, kind: str, option: str) -> str:
    \"\"\"T-Pot: the getopt message of the firmware's C library ([shell] text_libc).\"\"\"
    libc = CowrieConfig.get("shell", "text_libc", fallback="glibc")
    if kind == "long":
        text = {"musl": f"unrecognized option: {option[2:].partition('=')[0]}",
                "uclibc": f"unrecognized option `{option}'"}.get(libc, f"unrecognized option '{option}'")
    elif kind == "argument":
        text = {"musl": f"option requires an argument: {option}",
                "uclibc": f"option requires an argument -- {option}"}.get(
            libc, f"option requires an argument -- '{option}'")
    else:
        text = {"musl": f"unrecognized option: {option}", "uclibc": f"invalid option -- {option}"}.get(
            libc, f"invalid option -- '{option}'")
    return f"{applet}: {text}\\n"


def tpot_bb_usage_stop(command, applet: str, args: list) -> bool:
    \"\"\"T-Pot: --help, no argument where the applet needs one, or an option its getopt32 has not: the
    message and the usage of the persona's BusyBox on stderr, status 1 (True); False lets the command
    answer.\"\"\"
    path = CowrieConfig.get("shell", "busybox_usage", fallback="")
    entry = tpot_bb_usage_table(path).get(applet) if path else None
    if not entry:
        return False
    message = None
    if "--help" in args:
        message = ""
    elif not args and entry.get("noargs"):
        message = ""
    elif "flags" in entry:
        flags, values, index = entry["flags"], entry.get("values", ""), 0
        while index < len(args) and message is None:
            arg = args[index]
            index += 1
            if arg == "--":
                break
            if not arg.startswith("-") or arg == "-":
                continue
            if arg.startswith("--"):
                message = tpot_bb_usage_getopt(applet, "long", arg)
                break
            body = arg[1:]
            if entry.get("numbers") and body.isdigit():
                continue
            for position, letter in enumerate(body):
                if letter in values:
                    if position + 1 == len(body):
                        if index >= len(args):
                            message = tpot_bb_usage_getopt(applet, "argument", letter)
                        index += 1
                    break
                if letter not in flags:
                    message = tpot_bb_usage_getopt(applet, "invalid", letter)
                    break
    if message is None:
        return False
    banner = CowrieConfig.get("shell", "text_banner", fallback="BusyBox")
    command.errorWrite(f"{message}{banner} multi-call binary.\\n\\nUsage: {applet} {entry['usage']}")
    command.exit_code = 1
    return True


def tpot_bb_usage_wrap(cls, applet: str):
    \"\"\"T-Pot: the command class with the usage of BusyBox before its own answer.\"\"\"

    class Wrapped(cls):
        def start(self) -> None:
            if tpot_bb_usage_stop(self, applet, list(self.args)):
                self.exit()
                return
            cls.start(self)

    Wrapped.__name__ = cls.__name__
    return Wrapped
"""


def patch_bb_usage(cowrie_root):
    """uname, head, grep and which: the usage of the persona's BusyBox (BB_USAGE) at the end of their
    modules, after every other patch of them."""
    for rel, registration, applets in (("uname.py", 'commands["uname"] = Command_uname\n', ("uname",)),
                                       ("which.py", 'commands["which"] = Command_which\n', ("which",)),
                                       ("fs.py", 'commands["grep"] = Command_grep\n', ("grep", "head"))):
        path = cowrie_root / "src" / "cowrie" / "commands" / rel
        text = path.read_text(encoding="utf-8")
        if registration not in text:
            raise RuntimeError(f"Could not find expected text in {path}")
        # every spelling of the command (grep, /bin/grep, /usr/bin/head; not egrep) gets the wrapped class
        wraps = "".join(f'if "{name}" in commands:\n'
                        f'    tpot_class = commands["{name}"]\n'
                        f'    commands["{name}"] = tpot_bb_usage_wrap(commands["{name}"], "{name}")\n'
                        f'    for tpot_key in [k for k, v in commands.items()\n'
                        f'                     if v is tpot_class and k.rsplit("/", 1)[-1] == "{name}"]:\n'
                        f'        commands[tpot_key] = commands["{name}"]\n' for name in applets)
        path.write_text(text + BB_USAGE + "\n\n" + wraps, encoding="utf-8")


def patch_command_not_found(cowrie_root):
    """The table of Ubuntu's command-not-found ([shell] command_not_found, the runtime file of the
    persona's release: "<command> install <pkg>=<version>,..." or the spelling suggestions); the
    interactive shell answers a missing command of it as command-not-found 22.04 / 23.04 does
    (patch_shell_errors calls tpot_command_not_found). The texts go to stderr as the rest of the
    message ("Please ask your administrator." is stdout on the real system: users outside sudo
    only)."""
    path = cowrie_root / "src" / "cowrie" / "shell" / "honeypot.py"
    replace_once(
        path,
        "\n\nclass HoneyPotShell:\n",
        """


@functools.cache
def tpot_command_not_found_table(path: str) -> dict:
    \"\"\"T-Pot: {command: (kind, entries)} of the command-not-found table (parsed once; a line of
    another form is left out, an unreadable file is no table).\"\"\"
    table: dict = {}
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return table
    for line in text.splitlines()[:4096]:
        fields = line.split(" ")
        if len(fields) != 3 or fields[1] not in ("install", "similar", "similar-count"):
            continue
        command, kind, value = fields
        if kind == "similar-count":
            if value.isdigit():
                table[command] = (kind, int(value))
            continue
        entries = []
        for entry in value.split(","):
            name, sep, version = entry.partition("=")
            parts = (name, version) if kind == "install" else (*name.split("/", 1), version)
            if not (sep and name) or len(parts) != (2 if kind == "install" else 3):
                break
            entries.append(parts)
        else:
            if entries:
                table[command] = (kind, entries)
    return table


def tpot_command_not_found(cmd: str, uid: int, username: str) -> tuple[str, str] | None:
    \"\"\"T-Pot: what Ubuntu's command-not-found says of cmd (CommandNotFound.advise): (stderr,
    stdout), None when it says nothing (no table, a command it does not know). Only "Please ask your
    administrator." is a print() to stdout.\"\"\"
    path = CowrieConfig.get("shell", "command_not_found", fallback="")
    if not path or len(cmd) > 256:
        return None
    if cmd == "python":
        return (
            "Command 'python' not found, did you mean:\\n"
            "  command 'python3' from deb python3\\n"
            "  command 'python' from deb python-is-python3\\n",
            "",
        )
    found = tpot_command_not_found_table(path).get(cmd)
    if found is None:
        return None
    sudo = uid != 0 and username in CowrieConfig.get("shell", "command_not_found_sudo", fallback="").split(",")
    prefix = "sudo " if sudo else ""
    kind, entries = found
    if kind == "similar-count":
        return f"Command '{cmd}' not found, but there are {entries} similar ones.\\n", ""
    if kind == "similar":
        lines = [f"Command '{cmd}' not found, did you mean:"]
        lines += [f"  command '{word}' from deb {name} ({version})" for word, name, version in entries]
        lines.append(f"Try: {prefix}apt install <deb name>")
        return "".join(f"{line}\\n" for line in lines), ""
    lines = [f"Command '{cmd}' not found, but can be installed with:"]
    stdout = ""
    if len(entries) == 1:
        lines.append(f"{prefix}apt install {entries[0][0]}")
        if uid != 0 and not sudo:
            stdout = "Please ask your administrator.\\n"
    else:
        pad = max(len(name) for name, _ in entries)
        for i, (name, version) in enumerate(entries):
            lines.append(f"{prefix}apt install {name:<{pad}}  # version {version}{', or' if i == 0 else ''}")
        if uid != 0 and not sudo:
            # (advice_multi_deb_package prints this one to stderr)
            lines.append("Ask your administrator to install one of them.")
    return "".join(f"{line}\\n" for line in lines), stdout


class HoneyPotShell:
""",
    )
    replace_once(
        path,
        "        pp.errReceived(message)\n",
        "        pp.errReceived(message)\n"
        "        # T-Pot: the stdout of command-not-found after its error (tpot_command_not_found)\n"
        "        extra = getattr(self, \"tpot_cnf_stdout\", b\"\")\n"
        "        if extra:\n"
        "            self.tpot_cnf_stdout = b\"\"\n"
        "            pp.outReceived(extra)\n",
    )
    replace_once(path, "\nimport copy\nimport enum\n", "\nimport copy\nimport enum\nimport functools\n")
    replace_once(path, "\nfrom cowrie.core.config import CowrieConfig\n",
                 "\nfrom pathlib import Path\n\nfrom cowrie.core.config import CowrieConfig\n")


DD_COMMAND = r'''


# T-Pot: dd of the persona, GNU coreutils or BusyBox ([shell] text_style / text_version)
import random as tpot_random  # noqa: E402
import time as tpot_time  # noqa: E402

from cowrie.core.config import CowrieConfig  # noqa: E402

TPOT_DD_OUTPUT = 1 << 20  # the bytes dd writes to stdout at most (a count of zeros may be TB)
TPOT_DD_OPERANDS = frozenset({"if", "of", "bs", "ibs", "obs", "count", "skip", "seek", "conv", "status", "iflag",
                              "oflag", "cbs"})
TPOT_DD_SUFFIXES = {"": 1, "c": 1, "w": 2, "b": 512, "kB": 1000, "k": 1024, "K": 1024, "KB": 1000, "MB": 1000 ** 2,
                    "M": 1 << 20, "xM": 1 << 20, "GB": 1000 ** 3, "G": 1 << 30, "TB": 1000 ** 4, "T": 1 << 40}
TPOT_DD_ENDLESS = frozenset({"/dev/zero", "/dev/urandom", "/dev/random"})
TPOT_DD_BB_USAGE = """Usage: dd [if=FILE] [of=FILE] [ibs=N] [obs=N] [bs=N] [count=N] [skip=N]
\t[seek=N] [conv=notrunc|noerror|sync|fsync] [iflag=skip_bytes]

Copy a file with converting and formatting

\tif=FILE\t\tRead from FILE instead of stdin
\tof=FILE\t\tWrite to FILE instead of stdout
\tbs=N\t\tRead and write N bytes at a time
\tibs=N\t\tRead N bytes at a time
\tobs=N\t\tWrite N bytes at a time
\tcount=N\t\tCopy only N input blocks
\tskip=N\t\tSkip N input blocks
\tseek=N\t\tSkip N output blocks
\tconv=notrunc\tDon't truncate output file
\tconv=noerror\tContinue after read errors
\tconv=sync\tPad blocks with zeros
\tconv=fsync\tPhysically write data out before finishing
\tconv=swab\tSwap every pair of bytes
\tiflag=skip_bytes\tskip=N is in bytes
\tstatus=noxfer\tSuppress rate output
\tstatus=none\tSuppress all output

N may be suffixed by c (1), w (2), b (512), kB (1000), k (1024), MB, M, GB, G
"""

# the usage of 1.19 (no iflag=, no status=; recorded: BusyBox v1.19.4 of the Archer C7 v5), taken for the
#   releases before 1.24 (unverified for 1.14)
TPOT_DD_BB_USAGE_119 = """Usage: dd [if=FILE] [of=FILE] [ibs=N] [obs=N] [bs=N] [count=N] [skip=N]
\t[seek=N] [conv=notrunc|noerror|sync|fsync]

Copy a file with converting and formatting

\tif=FILE\t\tRead from FILE instead of stdin
\tof=FILE\t\tWrite to FILE instead of stdout
\tbs=N\t\tRead and write N bytes at a time
\tibs=N\t\tRead N bytes at a time
\tobs=N\t\tWrite N bytes at a time
\tcount=N\t\tCopy only N input blocks
\tskip=N\t\tSkip N input blocks
\tseek=N\t\tSkip N output blocks
\tconv=notrunc\tDon't truncate output file
\tconv=noerror\tContinue after read errors
\tconv=sync\tPad blocks with zeros
\tconv=fsync\tPhysically write data out before finishing

Numbers may be suffixed by c (x1), w (x2), b (x512), kD (x1000), k (x1024),
MD (x1000000), M (x1048576), GD (x1000000000) or G (x1073741824)
"""


def tpot_dd_size(text: str) -> int | None:
    """A size operand of dd (512, 1k, 2M, 1kB ...), None for one of another form."""
    match = re.fullmatch(r"([0-9]{1,18})([A-Za-z]{0,2})", text)
    if not match or match.group(2) not in TPOT_DD_SUFFIXES:
        return None
    return int(match.group(1)) * TPOT_DD_SUFFIXES[match.group(2)]


def tpot_dd_si(value: float, base: int, units: str, integer: bool) -> str:
    """human_readable of coreutils, rounded to the nearest: one decimal below 10 (an exact amount)
    or below 100 (a rate), whole above; "3 B", "3.1 kB", "105 MB", "23.6 GB"."""
    unit = 0
    while value >= base and unit < len(units) - 1:
        value /= base
        unit += 1
    prefix = units[unit].strip()
    if not prefix:
        return f"{round(value)} B"
    limit = 10 if integer else 100
    if value < limit and round(value, 1) < limit:
        return f"{value:.1f} {prefix}B"
    return f"{round(value)} {prefix}B"


def tpot_dd_bb_human(value: int) -> str:
    """make_human_readable_str(value, 1, 0) of BusyBox: 1024-based, one decimal with a unit."""
    units, unit, frac = "\0KMGTPE", 0, 0
    while value >= 1024 and unit < len(units) - 1:
        frac = ((value % 1024) * 10 + 512) // 1024
        value //= 1024
        unit += 1
    if frac >= 10:
        value, frac = value + 1, 0
    return f"{value}" if unit == 0 else f"{value}.{frac}{units[unit]}"


class Command_tpot_dd(HoneyPotCommand):
    """dd: the records of the input (full + partial ones of ibs), the output and the status lines of
    the persona's dd; /dev/zero and /dev/urandom are endless, a file of the tree is its contents, no
    if= reads stdin. Bounded: at most TPOT_DD_OUTPUT bytes go to stdout or into of=FILE; the counts
    are those of the whole transfer. An endless input without count= waits for ctrl-C, as dd does."""

    consumes_stdin = True

    def tpot_busybox(self) -> bool:
        return CowrieConfig.get("shell", "text_style", fallback="gnu") == "busybox"

    def tpot_version(self) -> tuple:
        version = CowrieConfig.get("shell", "text_version", fallback="9.1")
        return tuple(int(n) for n in re.findall(r"[0-9]+", version)[:3])

    def tpot_bb_usage(self) -> str:
        """The usage of BusyBox dd under its banner; bb_show_usage before 1.21 ends with a blank line."""
        version = self.tpot_version()
        banner = CowrieConfig.get("shell", "text_banner", fallback="") or \
            f"BusyBox v{CowrieConfig.get('shell', 'text_version', fallback='1.24.1')}"
        usage = TPOT_DD_BB_USAGE_119 if version < (1, 24) else TPOT_DD_BB_USAGE
        if version < (1, 25):
            # 1.24 has no iflag= yet (BusyBox 1.24.1 built from its release tarball, busybox:1.25.1 has it)
            usage = usage.replace(" [iflag=skip_bytes]", "").replace("\tiflag=skip_bytes\tskip=N is in bytes\n", "")
        return f"{banner} multi-call binary.\n\n{usage}" + ("\n" if version < (1, 21) else "")

    def tpot_quote(self, value: str) -> str:
        if CowrieConfig.get("shell", "text_collate", fallback="c") == "posix":
            return f"'{value}'"
        return f"‘{value}’"

    def tpot_fail(self, text: str) -> None:
        self.errorWrite(text)
        self.exit(1)

    def start(self) -> None:
        busybox = self.tpot_busybox()
        self.tpot_ops = {}
        for arg in self.args:
            operand, sep, value = arg.partition("=")
            if not sep or operand not in TPOT_DD_OPERANDS:
                if busybox:
                    self.errorWrite(self.tpot_bb_usage())
                    self.exit(1)
                    return
                self.tpot_fail(f"dd: unrecognized operand {self.tpot_quote(arg)}\n"
                               "Try 'dd --help' for more information.\n")
                return
            self.tpot_ops[operand] = value
        sizes = {}
        for name, fallback in (("bs", None), ("ibs", 512), ("obs", 512), ("count", None), ("skip", 0), ("seek", 0)):
            text = self.tpot_ops.get(name)
            if text is None:
                sizes[name] = fallback
                continue
            value = tpot_dd_size(text)
            if value is None or (value == 0 and name in ("bs", "ibs", "obs")):
                if busybox:
                    # xatoul_range_sfx dies with its message (busybox:1.27.2 / 1.36.1, the Archer C7's
                    #   1.19.4): the range ends at ((size_t)-1L)/2, 2147483647 on a 32-bit system
                    wide = CowrieConfig.get("shell", "arch", fallback="linux-x64-lsb") in ("linux-x64-lsb",
                                                                                         "linux-aarch64-lsb")
                    self.errorWrite(f"dd: number {text} is not in 1..{9223372036854775807 if wide else 2147483647}"
                                    " range\n")
                    self.exit(1)
                    return
                colon = ":" if self.tpot_version() >= (8, 23) else ""
                self.tpot_fail(f"dd: invalid number{colon} {self.tpot_quote(text)}\n")
                return
            sizes[name] = value
        if sizes["bs"]:
            sizes["ibs"] = sizes["obs"] = sizes["bs"]
        self.tpot_sizes = sizes
        self.tpot_started = tpot_time.monotonic()
        source = self.tpot_ops.get("if")
        if source is None:
            if self.input_data is not None:
                self.tpot_transfer(bytes(self.input_data), [len(self.input_data)] if self.input_data else [])
            else:
                self.tpot_lines = []  # typed lines up to EOF
            return
        path = self.fs.resolve_path(source, self.cwd)
        if path in TPOT_DD_ENDLESS:
            if sizes["count"] is None:
                self.tpot_endless = path
                return
            self.tpot_endless_copy(path, sizes["count"])
            return
        if self.fs.isdir(path):
            reading = "error reading" if not busybox else "can't read"
            self.tpot_fail(f"dd: {reading} '{source}': Is a directory\n")
            return
        try:
            data = self.fs.file_contents(path)
        except FileNotFound:
            if busybox:
                self.tpot_fail(f"dd: can't open '{source}': No such file or directory\n")
            elif self.tpot_version() >= (8, 25):
                self.tpot_fail(f"dd: failed to open '{source}': No such file or directory\n")
            else:
                self.tpot_fail(f"dd: failed to open {self.tpot_quote(source)}: No such file or directory\n")
            return
        data = bytes(data or b"")
        reads, ibs = [], sizes["ibs"]
        for start in range(0, len(data), ibs):
            reads.append(min(ibs, len(data) - start))
        self.tpot_transfer(data, reads)

    def tpot_endless_copy(self, path: str, blocks: int, seconds: float | None = None) -> None:
        # an endless input: skip= reads past blocks of it, count= blocks are copied after them
        ibs = self.tpot_sizes["ibs"]
        total = blocks * ibs
        keep = min(total, TPOT_DD_OUTPUT)
        data = bytes(keep) if path == "/dev/zero" else tpot_random.randbytes(keep)
        self.tpot_finish(data, blocks, 0, total, seconds)

    def tpot_transfer(self, data: bytes, reads: list) -> None:
        sizes = self.tpot_sizes
        skip = sizes["skip"]
        data = data[sum(reads[:skip]):]
        reads = reads[skip:]
        if sizes["count"] is not None:
            reads = reads[: sizes["count"]]
        total = sum(reads)
        full = sum(1 for r in reads if r == sizes["ibs"])
        self.tpot_finish(data[:total], full, len(reads) - full, total)

    def tpot_finish(self, data: bytes, full: int, part: int, total: int, seconds: float | None = None) -> None:
        sizes = self.tpot_sizes
        target = self.tpot_ops.get("of")
        if target is None:
            self.writeBytes(data[:TPOT_DD_OUTPUT])
        elif self.fs.resolve_path(target, self.cwd) != "/dev/null":
            error = self.tpot_write(target, data[:TPOT_DD_OUTPUT])
            if error:
                self.errorWrite(f"dd: failed to open '{target}': {error}\n" if not self.tpot_busybox()
                                else f"dd: can't open '{target}': {error}\n")
                self.exit(1)
                return
        if sizes["bs"]:
            out_full, out_part = full, part
        else:
            out_full, out_part = total // sizes["obs"], 1 if total % sizes["obs"] else 0
        self.tpot_status(full, part, out_full, out_part, total, seconds)
        self.exit(0)

    def tpot_write(self, target: str, data: bytes) -> str:
        """Write data as the file target (the write of sort -o: owner, mode, the session's budget)."""
        try:
            from cowrie.commands.wc import TpotTextTool
        except ImportError:
            return ""
        return TpotTextTool.tpot_write_file(self, target, data)

    def tpot_status(self, full, part, out_full, out_part, total, seconds=None) -> None:
        status = self.tpot_ops.get("status", "")
        if status == "none":
            return
        self.errorWrite(f"{full}+{part} records in\n{out_full}+{out_part} records out\n")
        busybox, version = self.tpot_busybox(), self.tpot_version()
        if status == "noxfer" or (busybox and version < (1, 19)):
            return
        if busybox and CowrieConfig.get("shell", "dd_status_line", fallback="yes") == "no":
            # a build without FEATURE_DD_THIRD_STATUS_LINE (the Archer C7 v5)
            return
        if seconds is None:
            # the time of the copy: a few microseconds and a fast memory copy
            seconds = (0.000008 + total / 2.5e10) * tpot_random.uniform(0.8, 1.6)
        rate = total / seconds if seconds > 0 else 0.0
        if busybox:
            self.errorWrite(f"{total} bytes ({tpot_dd_bb_human(total)}B) copied, {seconds:f} seconds, "
                            f"{tpot_dd_bb_human(int(rate))}B/s\n")
            return
        si = tpot_dd_si(total, 1000, " kMGTPE", True)
        iec = tpot_dd_si(total, 1024, " KMGTPE", True).replace("B", "iB") if total >= 1024 else f"{total} B"
        speed = tpot_dd_si(rate, 1000, " kMGTPE", False) + "/s"
        word = "byte" if total == 1 else "bytes"
        if version < (8, 24):
            amount = f"{total} {word} ({si})"
        elif total < 1000:
            amount = f"{total} {word}"
        elif total < 1024:
            amount = f"{total} {word} ({si})"
        else:
            amount = f"{total} {word} ({si}, {iec})"
        self.errorWrite(f"{amount} copied, {seconds:g} s, {speed}\n")

    def lineReceived(self, line: str) -> None:
        self.protocol.events.dispatch("cowrie.session.input", "INPUT (%(realm)s): %(input)s", realm="dd",
                                      input=line)
        lines = getattr(self, "tpot_lines", None)
        if lines is not None and sum(len(x) for x in lines) < TPOT_DD_OUTPUT:
            lines.append(line.encode("utf-8", "surrogateescape") + b"\n")

    def eofReceived(self) -> None:
        lines = getattr(self, "tpot_lines", None)
        if lines is not None:
            self.tpot_lines = None
            self.tpot_transfer(b"".join(lines), [len(x) for x in lines])

    def handle_CTRL_C(self) -> None:
        path = getattr(self, "tpot_endless", None)
        if path is None:
            HoneyPotCommand.handle_CTRL_C(self)
            return
        # dd prints its records when interrupted (SIGINT): what it copied in the time it ran
        self.write("^C")
        seconds = max(tpot_time.monotonic() - self.tpot_started, 0.001)
        blocks = int(seconds * min(2.5e9, 1e5 * self.tpot_sizes["ibs"]) / self.tpot_sizes["ibs"])
        self.tpot_ops["of"] = self.tpot_ops.get("of", "/dev/null")
        self.tpot_endless_copy(path, blocks, seconds)
        self.exit_code = 130


commands["/bin/dd"] = Command_tpot_dd
commands["/usr/bin/dd"] = Command_tpot_dd
commands["dd"] = Command_tpot_dd
'''


def patch_dd(cowrie_root):
    """dd of the persona (DD_COMMAND after the commands of dd.py): the records it reads and writes and
    the status lines of GNU coreutils or BusyBox of its version."""
    path = cowrie_root / "src" / "cowrie" / "commands" / "dd.py"
    replace_once(path, 'commands["/bin/dd"] = Command_dd\ncommands["dd"] = Command_dd\n',
                 'commands["/bin/dd"] = Command_dd\ncommands["dd"] = Command_dd\n' + DD_COMMAND
                 + tpot_metering("Command_tpot_dd"))


WGET_COMMAND = r'''


# T-Pot: wget of a BusyBox firmware ([shell] wget_style = busybox, text_banner / text_version)
import re as tpot_re  # noqa: E402

# the usage of BusyBox wget per release (the //usage of networking/wget.c, include/usage.h of 1.14;
#   FEATURE_WGET_LONG_OPTIONS and FEATURE_WGET_TIMEOUT on as in the default configuration, HTTPS from
#   1.32 as there); recorded with busybox:1.25.1 to 1.36.1 and BusyBox 1.24.1 built from its release
#   tarball (refs t9.json bb*-wget-help), 1.14 and 1.19 from the sources
TPOT_BB_WGET_USAGE = (
    # 1.35.0 and 1.36.1: --post-file and the short --header
    ((1, 35), "[-cqS] [--spider] [-O FILE] [-o LOGFILE] [--header STR]\n"
              "\t[--post-data STR | --post-file FILE] [-Y on/off]\n"
              "\t[--no-check-certificate] [-P DIR] [-U AGENT] [-T SEC] URL...\n\n"
              "Retrieve files via HTTP or FTP\n\n"
              "\t--spider\tOnly check URL existence: $? is 0 if exists\n"
              "\t--header STR\tAdd STR (of form 'header: value') to headers\n"
              "\t--post-data STR\tSend STR using POST method\n\t--post-file FILE\tSend FILE using POST method\n"
              "\t--no-check-certificate\tDon't validate the server's certificate\n"
              "\t-c\t\tContinue retrieval of aborted transfer\n\t-q\t\tQuiet\n\t-P DIR\t\tSave to DIR (default .)\n"
              "\t-S    \t\tShow server response\n\t-T SEC\t\tNetwork read timeout is SEC seconds\n"
              "\t-O FILE\t\tSave to FILE ('-' for stdout)\n\t-o LOGFILE\tLog messages to FILE\n"
              "\t-U STR\t\tUse STR for User-Agent header\n\t-Y on/off\tUse proxy\n"),
    # 1.33.1 and 1.34.1: the short form
    ((1, 33), "[-cqS] [--spider] [-O FILE] [-o LOGFILE] [--header 'HEADER: VALUE'] [-Y on/off]\n"
              "\t[--no-check-certificate] [-P DIR] [-U AGENT] [-T SEC] URL...\n\n"
              "Retrieve files via HTTP or FTP\n\n"
              "\t--spider\tOnly check URL existence: $? is 0 if exists\n"
              "\t--no-check-certificate\tDon't validate the server's certificate\n"
              "\t-c\t\tContinue retrieval of aborted transfer\n\t-q\t\tQuiet\n\t-P DIR\t\tSave to DIR (default .)\n"
              "\t-S    \t\tShow server response\n\t-T SEC\t\tNetwork read timeout is SEC seconds\n"
              "\t-O FILE\t\tSave to FILE ('-' for stdout)\n\t-o LOGFILE\tLog messages to FILE\n"
              "\t-U STR\t\tUse STR for User-Agent header\n\t-Y on/off\tUse proxy\n"),
    # 1.32.1: the form of 1.31 with HTTPS
    ((1, 32), "[-c|--continue] [--spider] [-q|--quiet] [-O|--output-document FILE]\n"
              "\t[-o|--output-file FILE] [--header 'header: value'] [-Y|--proxy on/off]\n"
              "\t[--no-check-certificate]\n"
              "\t[-P DIR] [-S|--server-response] [-U|--user-agent AGENT] [-T SEC] URL...\n\n"
              "Retrieve files via HTTP or FTP\n\n"
              "\t--spider\tOnly check URL existence: $? is 0 if exists\n"
              "\t--no-check-certificate\tDon't validate the server's certificate\n"
              "\t-c\t\tContinue retrieval of aborted transfer\n\t-q\t\tQuiet\n\t-P DIR\t\tSave to DIR (default .)\n"
              "\t-S    \t\tShow server response\n\t-T SEC\t\tNetwork read timeout is SEC seconds\n"
              "\t-O FILE\t\tSave to FILE ('-' for stdout)\n\t-o FILE\t\tLog messages to FILE\n"
              "\t-U STR\t\tUse STR for User-Agent header\n\t-Y on/off\tUse proxy\n"),
    # 1.31.1: -o
    ((1, 31), "[-c|--continue] [--spider] [-q|--quiet] [-O|--output-document FILE]\n"
              "\t[-o|--output-file FILE] [--header 'header: value'] [-Y|--proxy on/off]\n"
              "\t[-P DIR] [-S|--server-response] [-U|--user-agent AGENT] [-T SEC] URL...\n\n"
              "Retrieve files via HTTP or FTP\n\n"
              "\t--spider\tOnly check URL existence: $? is 0 if exists\n\t-c\t\tContinue retrieval of aborted transfer\n"
              "\t-q\t\tQuiet\n\t-P DIR\t\tSave to DIR (default .)\n\t-S    \t\tShow server response\n"
              "\t-T SEC\t\tNetwork read timeout is SEC seconds\n\t-O FILE\t\tSave to FILE ('-' for stdout)\n"
              "\t-o FILE\t\tLog messages to FILE\n\t-U STR\t\tUse STR for User-Agent header\n\t-Y on/off\tUse proxy\n"),
    ((1, 27), "[-c|--continue] [--spider] [-q|--quiet] [-O|--output-document FILE]\n"
              "\t[--header 'header: value'] [-Y|--proxy on/off] [-P DIR]\n"
              "\t[-S|--server-response] [-U|--user-agent AGENT] [-T SEC] URL...\n\n"
              "Retrieve files via HTTP or FTP\n\n"
              "\t--spider\tOnly check URL existence: $? is 0 if exists\n\t-c\t\tContinue retrieval of aborted transfer\n"
              "\t-q\t\tQuiet\n\t-P DIR\t\tSave to DIR (default .)\n\t-S    \t\tShow server response\n"
              "\t-T SEC\t\tNetwork read timeout is SEC seconds\n\t-O FILE\t\tSave to FILE ('-' for stdout)\n"
              "\t-U STR\t\tUse STR for User-Agent header\n\t-Y on/off\tUse proxy\n"),
    ((1, 26), "[-c|--continue] [--spider] [-q|--quiet] [-O|--output-document FILE]\n"
              "\t[--header 'header: value'] [-Y|--proxy on/off] [-P DIR]\n"
              "\t[-U|--user-agent AGENT] [-T SEC] URL...\n\n"
              "Retrieve files via HTTP or FTP\n\n"
              "\t--spider\tSpider mode - only check file existence\n\t-c\t\tContinue retrieval of aborted transfer\n"
              "\t-q\t\tQuiet\n\t-P DIR\t\tSave to DIR (default .)\n\t-T SEC\t\tNetwork read timeout is SEC seconds\n"
              "\t-O FILE\t\tSave to FILE ('-' for stdout)\n\t-U STR\t\tUse STR for User-Agent header\n"
              "\t-Y on/off\tUse proxy\n"),
    ((1, 20), "[-c|--continue] [-s|--spider] [-q|--quiet] [-O|--output-document FILE]\n"
              "\t[--header 'header: value'] [-Y|--proxy on/off] [-P DIR]\n"
              "\t[-U|--user-agent AGENT] [-T SEC] URL...\n\n"
              "Retrieve files via HTTP or FTP\n\n"
              "\t-s\tSpider mode - only check file existence\n\t-c\tContinue retrieval of aborted transfer\n"
              "\t-q\tQuiet\n\t-P DIR\tSave to DIR (default .)\n\t-T SEC\tNetwork read timeout is SEC seconds\n"
              "\t-O FILE\tSave to FILE ('-' for stdout)\n\t-U STR\tUse STR for User-Agent header\n"
              "\t-Y\tUse proxy ('on' or 'off')\n"),
    ((1, 15), "[-c|--continue] [-s|--spider] [-q|--quiet] [-O|--output-document FILE]\n"
              "\t[--header 'header: value'] [-Y|--proxy on/off] [-P DIR]\n"
              "\t[--no-check-certificate] [-U|--user-agent AGENT] [-T SEC] URL...\n\n"
              "Retrieve files via HTTP or FTP\n\n"
              "\t-s\tSpider mode - only check file existence\n\t-c\tContinue retrieval of aborted transfer\n"
              "\t-q\tQuiet\n\t-P DIR\tSave to DIR (default .)\n\t-T SEC\tNetwork read timeout is SEC seconds\n"
              "\t-O FILE\tSave to FILE ('-' for stdout)\n\t-U STR\tUse STR for User-Agent header\n"
              "\t-Y\tUse proxy ('on' or 'off')\n"),
    ((0, 0), "[-c|--continue] [-s|--spider] [-q|--quiet] [-O|--output-document file]\n"
             "\t[--header 'header: value'] [-Y|--proxy on/off] [-P DIR]\n"
             "\t[-U|--user-agent agent] url\n\n"
             "Retrieve files via HTTP or FTP\n\nOptions:\n"
             "\t-s\tSpider mode - only check file existence\n\t-c\tContinue retrieval of aborted transfer\n"
             "\t-q\tQuiet\n\t-P\tSet directory prefix to DIR\n\t-O\tSave to filename ('-' for stdout)\n"
             "\t-U\tAdjust 'User-Agent' field\n\t-Y\tUse proxy ('on' or 'off')\n"),
)


# the options getopt32 of BusyBox wget takes, per release: (since, flags, with a value, with an optional
#   value, long flags, long options with a value); from the getopt32 strings and wget_longopts of
#   networking/wget.c 1.14.1 / 1.19.4 / 1.24.1 / 1.26.2 / 1.27.2 and recorded with busybox:1.27.2 /
#   1.31.1 / 1.36.1 and the BusyBox v1.19.4 of the Archer C7 v5 (qemu-mips-static): -T and -t are taken
#   and ignored by every release (a build without FEATURE_WGET_TIMEOUT has no --timeout), -s goes with
#   1.26 (busybox:1.25.1 takes it, 1.26.2 not), -S comes with 1.27, -o / --output-file with 1.31
#   (busybox:1.30.1 refuses it), --post-file with 1.35 (busybox:1.34.1 refuses it, 1.35.0 takes it)
_TPOT_BB_WGET_LONG_114 = frozenset({"output-document", "directory-prefix", "proxy", "user-agent", "header",
                                    "post-data"})
_TPOT_BB_WGET_FLAGS_126 = frozenset({"continue", "quiet", "spider", "passive-ftp", "no-check-certificate",
                                     "no-cache", "no-verbose", "no-clobber", "no-host-directories", "no-parent"})
TPOT_BB_WGET_OPTIONS = (
    ((1, 35), "cqS", "OoPYUTt", "n", _TPOT_BB_WGET_FLAGS_126 | {"server-response"},
     _TPOT_BB_WGET_LONG_114 | {"tries", "output-file", "post-file"}),
    ((1, 31), "cqS", "OoPYUTt", "n", _TPOT_BB_WGET_FLAGS_126 | {"server-response"},
     _TPOT_BB_WGET_LONG_114 | {"tries", "output-file"}),
    ((1, 27), "cqS", "OPYUTt", "n", _TPOT_BB_WGET_FLAGS_126 | {"server-response"}, _TPOT_BB_WGET_LONG_114 | {"tries"}),
    ((1, 26), "cq", "OPYUTt", "n", _TPOT_BB_WGET_FLAGS_126, _TPOT_BB_WGET_LONG_114 | {"tries"}),
    ((1, 24), "csq", "OPYUTt", "", frozenset({"continue", "spider", "quiet", "passive-ftp", "no-check-certificate",
                                              "no-cache"}), _TPOT_BB_WGET_LONG_114),
    ((1, 15), "csq", "OPYUTt", "", frozenset({"continue", "spider", "quiet", "passive-ftp", "no-check-certificate"}),
     _TPOT_BB_WGET_LONG_114),
    ((0, 0), "csq", "OPYUtT", "", frozenset({"continue", "spider", "quiet", "passive-ftp"}), _TPOT_BB_WGET_LONG_114),
)


def tpot_bb_wget_version() -> tuple[int, int] | None:
    """T-Pot: (major, minor) of the BusyBox whose wget this is, None for GNU wget."""
    if CowrieConfig.get("shell", "wget_style", fallback="gnu") != "busybox":
        return None
    found = tpot_re.match(r"(\d+)\.(\d+)", CowrieConfig.get("shell", "text_version", fallback="1.24.1"))
    return (int(found.group(1)), int(found.group(2))) if found else (1, 24)


# T-Pot: the address BusyBox wget names ("Connecting to HOST (IP:PORT)") is the one of upstream's address check, which
#   upstream's start makes after its rate limit: the command that asks is the one start() runs (it calls the check
#   before it yields anything)
tpot_upstream_allowed = communication_allowed
tpot_wget_asking: list = []


@inlineCallbacks
def communication_allowed(address: str):
    command = tpot_wget_asking.pop() if tpot_wget_asking else None
    if command is None:
        allowed = yield tpot_upstream_allowed(address)
        return allowed
    resolved = yield resolve_allowed(address)
    if resolved is not None:
        command.tpot_ip = command.tpot_ip or str(resolved)
        return True
    if command.tpot_ip is None:
        # an address the honeypot does not contact: the one BusyBox connects to (refused), else "bad address"
        try:
            command.tpot_ip = yield reactor.resolve(address)
        except Exception:  # noqa: BLE001 - a name that does not resolve
            command.tpot_ip = None
    return False


class Command_tpot_wget(Command_wget):
    """wget as BusyBox writes it (tpot_bb_wget_version): the download stays upstream's, the texts
    are BusyBox's: "Connecting to HOST (IP:PORT)", its progress line at the end, "bad address",
    "can't connect to remote host", "server returned error" (busybox:1.27.2 / 1.36.1, the sources of
    the older releases); without BusyBox upstream's GNU wget."""

    tpot_ip: str | None = None

    @inlineCallbacks
    def start(self):
        version = tpot_bb_wget_version()
        if version is not None:
            args = self.tpot_bb_args()
            if args is None:
                self.exit()
                return
            self.args = args
            # the address of "Connecting to": as BusyBox resolves the host before it connects
            words, index = [], 0
            while index < len(args):
                arg = args[index]
                index += 1
                if arg in ("-O", "-P", "--header"):
                    index += 1  # its value
                elif not arg.startswith("-"):
                    words.append(arg)
            if words:
                url = words[0] if "://" in words[0] else f"http://{words[0]}"
                self.tpot_url = url
                try:
                    host = parse.urlparse(url).hostname or ""
                except ValueError:
                    host = ""
                if host and is_ip_address(host):
                    self.tpot_ip = host
            # a name: its address comes from upstream's address check, after its rate limit (communication_allowed)
            tpot_wget_asking.append(self)
        try:
            yield Command_wget.start(self)
        finally:
            if self in tpot_wget_asking:
                tpot_wget_asking.remove(self)

    def tpot_bb_usage(self) -> str:
        """The usage of BusyBox wget under its banner."""
        version = tpot_bb_wget_version()
        usage = next(text for since, text in TPOT_BB_WGET_USAGE if version >= since)
        if CowrieConfig.get("shell", "wget_timeout", fallback="yes") == "no":
            # a build without FEATURE_WGET_TIMEOUT (the Archer C7 v5): no -T in the usage
            usage = usage.replace(" [-T SEC]", "").replace("\t-T SEC\tNetwork read timeout is SEC seconds\n", "")
        if version < (1, 21):
            # bb_show_usage before 1.21 ends with a blank line (recorded for 1.19.4)
            usage += "\n"
        banner = CowrieConfig.get("shell", "text_banner", fallback="BusyBox")
        return f"{banner} multi-call binary.\n\nUsage: wget {usage}"

    def tpot_bb_option_error(self, kind: str, option: str) -> None:
        """getopt32 of BusyBox: the message of the firmware's C library, then the usage."""
        libc = CowrieConfig.get("shell", "text_libc", fallback="glibc")
        if kind == "long":
            text = {"musl": f"unrecognized option: {option[2:].partition('=')[0]}",
                    "uclibc": f"unrecognized option `{option}'"}.get(libc, f"unrecognized option '{option}'")
        elif kind == "long-argument":
            text = {"musl": f"option requires an argument: {option[2:]}",
                    "uclibc": f"option `{option}' requires an argument"}.get(
                libc, f"option '{option}' requires an argument")
        elif kind == "argument":
            text = {"musl": f"option requires an argument: {option}",
                    "uclibc": f"option requires an argument -- {option}"}.get(
                libc, f"option requires an argument -- '{option}'")
        else:
            text = {"musl": f"unrecognized option: {option}", "uclibc": f"invalid option -- {option}"}.get(
                libc, f"invalid option -- '{option}'")
        self.exit_code = 1
        self.errorWrite(f"wget: {text}\n" + self.tpot_bb_usage())

    def tpot_bb_args(self) -> list[str] | None:
        """The arguments of BusyBox wget for upstream's getopt ("hcqO:TP:"): what getopt32 of the
        persona's BusyBox takes (TPOT_BB_WGET_OPTIONS); the options that do not change the download go
        with their values, -q and -o LOGFILE are kept as tpot_quiet / tpot_silent. None after an option
        that BusyBox has not (its message and usage written)."""
        version = tpot_bb_wget_version()
        flags, with_value, optional, long_flags, long_value = next(
            row[1:] for row in TPOT_BB_WGET_OPTIONS if version >= row[0])
        # FEATURE_WGET_TIMEOUT (from 1.18) brings --timeout and makes the value of -T a number;
        #   -T is in the getopt string of every build
        timeout = CowrieConfig.get("shell", "wget_timeout", fallback="yes") != "no" and version >= (1, 18)
        if timeout:
            long_value = long_value | {"timeout"}
        args, out, index = list(self.args), [], 0
        quiet = silent = False
        while index < len(args):
            arg = args[index]
            index += 1
            if arg == "--":
                out += args[index - 1:]
                break
            if arg.startswith("--"):
                name, eq, value = arg[2:].partition("=")
                if name not in long_value and (name not in long_flags or eq):
                    self.tpot_bb_option_error("long", arg)
                    return None
                if name in long_value and not eq:
                    if index >= len(args):
                        self.tpot_bb_option_error("long-argument", arg)
                        return None
                    value, index = args[index], index + 1
                if name == "timeout" and not self.tpot_bb_seconds(value):
                    return None
                if name == "output-document":
                    out += ["-O", value]
                elif name == "directory-prefix":
                    out += ["-P", value]
                elif name == "header":
                    out.append(f"--header={value}")
                elif name == "continue":
                    out.append("-c")
                elif name == "quiet":
                    out.append("--quiet")
                    quiet = True
                elif name == "output-file":
                    silent = True
                continue
            if not arg.startswith("-") or arg == "-":
                out.append(arg)
                continue
            position = 1
            while position < len(arg):
                letter = arg[position]
                position += 1
                if letter in optional:
                    break  # -n[ARG]: the rest of the word is its argument
                if letter in with_value:
                    value = arg[position:]
                    if not value:
                        if index >= len(args):
                            self.tpot_bb_option_error("argument", letter)
                            return None
                        value, index = args[index], index + 1
                    if letter == "T" and timeout and not self.tpot_bb_seconds(value):
                        return None
                    if letter in "OP":
                        out += [f"-{letter}", value]
                    elif letter == "o":
                        silent = True
                    break
                if letter not in flags:
                    self.tpot_bb_option_error("invalid", letter)
                    return None
                if letter == "c":
                    out.append("-c")
                elif letter == "q":
                    out.append("-q")
                    quiet = True
        self.tpot_quiet, self.tpot_silent = quiet, silent
        return out

    def tpot_bb_seconds(self, value: str) -> bool:
        """-T SEC of a build with FEATURE_WGET_TIMEOUT: getopt32 converts it with xatoi_positive, which
        stops BusyBox on anything but digits ("invalid number", a value past an unsigned int too) and on a
        value past an int (its range); status 1, no usage (busybox:1.26.2 / 1.36.1)."""
        if tpot_re.fullmatch(r"[0-9]+", value) and int(value) <= 4294967295:
            if int(value) <= 2147483647:
                return True
            self.errorWrite(f"wget: number {value} is not in 0..2147483647 range\n")
        else:
            self.errorWrite(f"wget: invalid number '{value}'\n")
        self.exit_code = 1
        return False

    def print_usage_error(self, error_msg: str = "") -> None:
        version = tpot_bb_wget_version()
        if version is None or error_msg != "missing URL":
            Command_wget.print_usage_error(self, error_msg)
            return
        self.exit_code = 1
        self.errorWrite(self.tpot_bb_usage())

    def tpot_hostport(self) -> str:
        """HOST, or HOST:PORT when the URL names its port (as BusyBox prints what it was given)."""
        url = getattr(self, "tpot_url", None) or (self.url.decode(errors="replace") if hasattr(self, "url") else "")
        explicit = tpot_re.search(r"//[^/]*:(\d+)", url)
        return f"{self.host}:{self.port}" if explicit else self.host

    def tpot_cannot(self, reason: str) -> str:
        version = tpot_bb_wget_version()
        word = "cannot" if version < (1, 15) else "can't"
        return f"wget: {word} connect to remote host ({self.tpot_ip or self.host}): {reason}\n"

    def errorWrite(self, data: str) -> None:
        version = tpot_bb_wget_version()
        if version is None:
            Command_wget.errorWrite(self, data)
            return
        if data.startswith(("--", "Resolving ", "HTTP request sent", "FTP request sent", "Length: ", "Saving to: ",
                            "\r", "Retrying.")) or data == "\n\n" or tpot_re.fullmatch(r"\d{3}( .*)?\n", data):
            return
        if data.startswith("Connecting to ") and data.endswith("... connected.\n"):
            data = f"Connecting to {self.tpot_hostport()} ({self.tpot_ip or self.host}:{self.port})\n"
            if version >= (1, 30):
                dest = "stdout" if self.outfile in (None, "-") else self.outfile
                data += "writing to stdout\n" if dest == "stdout" else f"saving to '{dest}'\n"
        elif data.startswith("Connecting to ") and (data.endswith("... ") or "failed:" in data):
            # (the rate limit of upstream writes "Connecting to H:P... " and its failure apart)
            data = f"Connecting to {self.tpot_hostport()} ({self.tpot_ip or self.host}:{self.port})\n"
        elif data.startswith("failed: Connection timed out"):
            data = self.tpot_cannot("Connection timed out")
        elif data.startswith("failed: Network is unreachable"):
            data = self.tpot_cannot("Network is unreachable")
        elif data.startswith("wget: unable to resolve host address"):
            if self.tpot_ip is not None:
                # an address the honeypot does not contact: refused, as a closed port of a real host
                data = (f"Connecting to {self.tpot_hostport()} ({self.tpot_ip}:{self.port})\n"
                        + self.tpot_cannot("Connection refused"))
            else:
                data = f"wget: bad address '{self.host}'\n"
        elif " saved [" in data:
            data = self.tpot_progress(version)
        if getattr(self, "tpot_silent", False):
            return  # -o LOGFILE: the messages go to that file
        if getattr(self, "tpot_quiet", False):
            # -q: no Connecting line, no progress, the errors stay
            data = "".join(line for line in data.splitlines(keepends=True)
                           if line.startswith("wget: "))
            if not data:
                return
        Command_wget.errorWrite(self, data)

    def tpot_progress(self, version) -> str:
        """The last line of BusyBox's progress meter (80 columns) and what follows it."""
        name = (self.outfile or "-").rsplit("/", 1)[-1] if self.outfile not in (None, "-") else "-"
        size, unit = self.currentlength, " "
        if size >= 100000:
            size, unit = size >> 10, "k"
            while size >= 100000:
                size, unit = size >> 10, "MGTP"["kMGT".index(unit)]
        if version >= (1, 30):
            amount = f"{size:6d}" if unit == " " else f"{size:5d}{unit}"
            line = f"{name:<20.20} 100% |{'*' * 32}|{amount}  0:00:00 ETA\n"
            dest = "stdout" if self.outfile in (None, "-") else self.outfile
            return line + ("written to stdout\n" if dest == "stdout" else f"'{dest}' saved\n")
        return f"{name:<20.20} 100% |{'*' * 31}|{size:6d}{unit}  0:00:00 ETA\n\n"

    def success(self, response):
        if tpot_bb_wget_version() is not None:
            code = getattr(response, "code", 200) or 200
            if code >= 400:
                phrase = getattr(response, "phrase", b"") or b""
                phrase = phrase.decode(errors="replace") if isinstance(phrase, bytes) else str(phrase)
                version = getattr(response, "version", (b"HTTP", 1, 1))
                proto = f"{version[0].decode(errors='replace')}/{version[1]}.{version[2]}"
                Command_wget.errorWrite(self, f"wget: server returned error: {proto} {code} {phrase}\n")
                abort_body(response)
                self.exit(1)
                return None
        return Command_wget.success(self, response)

    def error(self, response):
        if tpot_bb_wget_version() is not None and response.check(error.ConnectionRefusedError) is not None \
                and not self.exited:
            self.exit_code = 1
            report_download_failure(self, self.url.decode())
            Command_wget.errorWrite(self, self.tpot_cannot("Connection refused"))
            self.exit()
            return
        Command_wget.error(self, response)


commands["/usr/bin/wget"] = Command_tpot_wget
commands["wget"] = Command_tpot_wget
'''


def patch_wget(cowrie_root):
    """wget of a BusyBox firmware (WGET_COMMAND after the commands of wget.py): BusyBox's texts per
    version over upstream's download; without [shell] wget_style = busybox GNU wget as upstream."""
    path = cowrie_root / "src" / "cowrie" / "commands" / "wget.py"
    # upstream's address check that names the address (communication_allowed of WGET_COMMAND)
    replace_once(path, "    communication_allowed,\n    is_ip_address,\n",
                 "    communication_allowed,\n    is_ip_address,\n    resolve_allowed,\n")
    replace_once(path, 'commands["/usr/bin/dget"] = Command_wget\ncommands["dget"] = Command_wget\n',
                 'commands["/usr/bin/dget"] = Command_wget\ncommands["dget"] = Command_wget\n' + WGET_COMMAND)


LOGIN_PATH_NEEDLE = """        if self.uid == 0:
            self.environ["PATH"] = (
                "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
            )
        else:
            self.environ["PATH"] = (
                "/usr/local/bin:/usr/bin:/bin:/usr/local/games:/usr/games"
            )
"""


TPOT_HISTORY_ENV = """        # T-Pot: an interactive bash has the HISTSIZE, HISTFILESIZE and HISTCONTROL of the persona
        #   (BusyBox ash sets none); the shell exports only those of [shell] history_export, which are
        #   set also when empty (EdgeOS: vyatta-cfg exports HISTCONTROL= and HISTTIMEFORMAT)
        if CowrieConfig.get("shell", "not_found_style", fallback="bash") != "ash":
            tpot_root = "_root" if self.uid == 0 else ""
            tpot_exports = {tpot_name.strip() for tpot_name in
                            CowrieConfig.get("shell", "history_export", fallback="").split(",")}
            for tpot_name, tpot_key in (("HISTSIZE", "history_size"), ("HISTFILESIZE", "history_file_size"),
                                        ("HISTCONTROL", "history_control")):
                tpot_value = CowrieConfig.get("shell", tpot_key + tpot_root, fallback="")
                if tpot_value or tpot_name in tpot_exports:
                    self.environ[tpot_name] = tpot_value
            # [shell] history_time_format: a JSON string, as the format ends in a space
            tpot_value = CowrieConfig.get("shell", "history_time_format", fallback="")
            if tpot_value:
                import json as tpot_json

                try:
                    tpot_value = tpot_json.loads(tpot_value)
                except ValueError:
                    tpot_value = ""
                if isinstance(tpot_value, str) and tpot_value and len(tpot_value) <= 64:
                    self.environ["HISTTIMEFORMAT"] = tpot_value
"""


def patch_login_env(cowrie_root):
    """PATH and SHELL of a login over SSH (shell/session.py) and Telnet (telnet/session.py): those of
    the persona ([shell] login_path / login_shell, the Archer C7: the PATH of its /etc/profile for
    every user and its /bin/ash), upstream's Debian PATH and /bin/bash without the keys."""
    for rel in ("shell/session.py", "telnet/session.py"):
        path = cowrie_root / "src" / "cowrie" / rel
        replace_once(path, "from cowrie.insults import insults\n",
                     "from cowrie.core.config import CowrieConfig\nfrom cowrie.insults import insults\n")
        replace_once(path, LOGIN_PATH_NEEDLE, LOGIN_PATH_NEEDLE + """        # T-Pot: the PATH and SHELL of the persona's login
        tpot_path = CowrieConfig.get("shell", "login_path", fallback="")
        if tpot_path:
            self.environ["PATH"] = tpot_path
        tpot_shell = CowrieConfig.get("shell", "login_shell", fallback="")
        if tpot_shell:
            self.environ["SHELL"] = tpot_shell
""" + (TPOT_HISTORY_ENV if rel == "telnet/session.py" else ""))
    # SSH: the shell of openShell is interactive, that of execCommand not
    replace_once(cowrie_root / "src" / "cowrie" / "shell" / "session.py",
                 "    def openShell(self, processprotocol):\n",
                 "    def openShell(self, processprotocol):\n" + TPOT_HISTORY_ENV)
    # the first shell exports the login's environment, but the history variables bash sets itself
    replace_once(cowrie_root / "src" / "cowrie" / "shell" / "honeypot.py",
                 "            self.exported = set(protocol.environ.keys())\n",
                 """            # T-Pot: HISTSIZE, HISTFILESIZE and HISTCONTROL are shell variables of bash, exported
            #   where the persona's profile exports them ([shell] history_export: Red Hat)
            tpot_export = {name.strip() for name in
                           CowrieConfig.get("shell", "history_export", fallback="").split(",")}
            self.exported = set(protocol.environ.keys()) - (
                {"HISTSIZE", "HISTFILESIZE", "HISTCONTROL"} - tpot_export)
""")


# module code of the patched shell/command.py before HoneyPotCommand: the time budget of a session
TPOT_BUDGET = r'''

# T-Pot: one time budget per session for the commands T-Pot emulates. The commands of a line run back to back on
# the reactor, so what they use counts for the session in a sliding window: at most TPOT_BUDGET_CPU s of process
# time and TPOT_BUDGET_WALL s of wall-clock time in any TPOT_BUDGET_WINDOW s (in TPOT_BUDGET_SLOTS steps). A call
# is charged TPOT_BUDGET_CALL at its start and the time it runs; a call made inside another (sudo, a command a
# command runs) counts with it. A command checks the session's deadline where it checks its own bounds and, past
# it, ends as it does at them.
import contextlib
import math
import time

TPOT_BUDGET_CPU = 0.4
TPOT_BUDGET_WALL = 0.5
TPOT_BUDGET_WINDOW = 5.0
TPOT_BUDGET_SLOTS = 10
TPOT_BUDGET_CALL = 0.0002


class TpotBudget:
    """The time the commands of one session used in the last TPOT_BUDGET_WINDOW seconds."""

    def __init__(self) -> None:
        # [step, process time, wall-clock time] of the last TPOT_BUDGET_SLOTS steps
        self.slots = [[-TPOT_BUDGET_SLOTS, 0.0, 0.0] for _ in range(TPOT_BUDGET_SLOTS)]
        self.depth = 0
        self.opened = (0.0, 0.0)  # process time and time.monotonic of the outermost running call

    @staticmethod
    def step(now: float) -> int:
        return math.floor(now * TPOT_BUDGET_SLOTS / TPOT_BUDGET_WINDOW)

    def charge(self, now: float, cpu: float, wall: float) -> None:
        step = self.step(now)
        slot = self.slots[step % TPOT_BUDGET_SLOTS]
        if slot[0] != step:
            slot[:] = [step, 0.0, 0.0]
        slot[1] += cpu
        slot[2] += wall

    def used(self, now: float) -> tuple[float, float]:
        """Process and wall-clock time of the window, the running call's so far included."""
        step = self.step(now)
        cpu = sum(slot[1] for slot in self.slots if step - slot[0] < TPOT_BUDGET_SLOTS)
        wall = sum(slot[2] for slot in self.slots if step - slot[0] < TPOT_BUDGET_SLOTS)
        if self.depth:
            cpu += time.process_time() - self.opened[0]
            wall += now - self.opened[1]
        return cpu, wall

    def left(self, now: float) -> float:
        """Seconds the session has left: of process time and of wall-clock time, the less."""
        cpu, wall = self.used(now)
        return max(0.0, min(TPOT_BUDGET_CPU - cpu, TPOT_BUDGET_WALL - wall))

    def open(self) -> None:
        now = time.monotonic()
        self.charge(now, TPOT_BUDGET_CALL, TPOT_BUDGET_CALL)
        if not self.depth:
            self.opened = (time.process_time(), now)
        self.depth += 1

    def close(self) -> None:
        self.depth -= 1
        if not self.depth:
            now = time.monotonic()
            self.charge(now, max(0.0, time.process_time() - self.opened[0]), max(0.0, now - self.opened[1]))


def tpot_budget(protocol) -> TpotBudget | None:
    """The budget of a connection: on its avatar (protocol.user), which every channel of the connection shares,
    else on the protocol; None without a protocol."""
    if protocol is None:
        return None
    for owner in (getattr(protocol, "user", None), protocol):
        if owner is None:
            continue
        budget = getattr(owner, "tpot_budget", None)
        if budget is not None and hasattr(budget, "left"):
            return budget
        try:
            owner.tpot_budget = budget = TpotBudget()
        except (AttributeError, TypeError):
            continue
        return budget
    return None


def tpot_deadline(protocol, seconds: float | None = None) -> float:
    """The deadline (time.monotonic) of work on the reactor: what the session has left, at most seconds."""
    now = time.monotonic()
    budget = tpot_budget(protocol)
    left = budget.left(now) if budget is not None else math.inf
    return now + (left if seconds is None else min(left, seconds))


@contextlib.contextmanager
def tpot_metered(protocol):
    """A call of an emulated command: its start and the time it runs count for the session."""
    budget = tpot_budget(protocol)
    if budget is None:
        yield
        return
    budget.open()
    try:
        yield
    finally:
        budget.close()


def tpot_meter_method(method):
    def metered(self, *args, **kwargs):
        with tpot_metered(getattr(self, "protocol", None)):
            return method(self, *args, **kwargs)

    metered.tpot_metered = True
    metered.__name__, metered.__doc__ = method.__name__, method.__doc__
    return metered


def tpot_meter_class(cls, *names: str):
    """A command class whose start(), lineReceived() and eofReceived() (or the methods named) count as calls."""
    for name in names or ("start", "lineReceived", "eofReceived"):
        method = getattr(cls, name, None)
        if method is not None and not getattr(method, "tpot_metered", False):
            setattr(cls, name, tpot_meter_method(method))
    return cls


def tpot_scanned(protocol, items):
    """The items of a name scan (apt, yum, dnf) while the session's time lasts: a scan past it finds nothing.
    The scan is a call of its own (it may run after a command's sleep, outside its start); inside a call it
    counts with it."""
    with tpot_metered(protocol):
        deadline = tpot_deadline(protocol)
        for item in items:
            if time.monotonic() >= deadline:
                return
            yield item

'''


def patch_budget(cowrie_root):
    """One time budget per session for the emulated commands (TPOT_BUDGET in shell/command.py, used by the
    command modules)."""
    path = cowrie_root / "src" / "cowrie" / "shell" / "command.py"
    replace_once(path, "\n\nclass HoneyPotCommand:\n", TPOT_BUDGET + "\nclass HoneyPotCommand:\n")


# module code of the patched shell/protocol.py before HoneyPotBaseProtocol: the commands of one connection
TPOT_SESSION_COMMANDS = r'''

import collections.abc


class TpotCommands(collections.abc.MutableMapping):
    """T-Pot: the commands of one connection. Cowrie's commands (the dict every session reads) stay as they are;
    what the connection's package installs add is in added, what its removals take away in removed. Every
    protocol of a connection (its channels) has its view on the same added and removed (tpot_commands)."""

    def __init__(self, shared, added=None, removed=None) -> None:
        self.tpot_shared = shared
        self.tpot_added = {} if added is None else added
        self.tpot_removed = set() if removed is None else removed

    def __getitem__(self, key):
        if key in self.tpot_added:
            return self.tpot_added[key]
        if key in self.tpot_removed:
            raise KeyError(key)
        return self.tpot_shared[key]

    def __contains__(self, key) -> bool:
        return key in self.tpot_added or (key not in self.tpot_removed and key in self.tpot_shared)

    def __setitem__(self, key, value) -> None:
        self.tpot_removed.discard(key)
        if key in self.tpot_shared and self.tpot_shared[key] is value:
            self.tpot_added.pop(key, None)   # Cowrie's own command again
        else:
            self.tpot_added[key] = value

    def __delitem__(self, key) -> None:
        if key not in self:
            raise KeyError(key)
        self.tpot_added.pop(key, None)
        if key in self.tpot_shared:
            self.tpot_removed.add(key)

    def __iter__(self):
        yield from list(self.tpot_added)
        for key in list(self.tpot_shared):
            if key not in self.tpot_added and key not in self.tpot_removed:
                yield key

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def tpot_restore(self, program: str) -> bool:
        """Cowrie's own commands of the name of program that a removal took away answer again (a reinstall);
        True if Cowrie has one of them."""
        name = program.rsplit("/", 1)[-1]
        back = {key for key in self.tpot_removed if key.rsplit("/", 1)[-1] == name}
        self.tpot_removed.difference_update(back)
        return any(key in self.tpot_shared and key not in self.tpot_added for key in back)


def tpot_commands(avatar, shared) -> TpotCommands:
    """The commands of the connection of avatar (protocol.user, which every channel of the connection shares, as
    the time budget of shell/command.py): its own added and removed on Cowrie's commands."""
    overlay = getattr(avatar, "tpot_commands", None)
    if not (isinstance(overlay, tuple) and len(overlay) == 2):
        overlay = ({}, set())
        try:
            avatar.tpot_commands = overlay
        except (AttributeError, TypeError):
            pass
    return TpotCommands(shared, *overlay)

'''


def patch_protocol_commands(cowrie_root):
    """The commands of a session are those of its connection (TPOT_SESSION_COMMANDS): a package install or removal
    of one connection changes its own view, never Cowrie's commands of the other sessions."""
    path = cowrie_root / "src" / "cowrie" / "shell" / "protocol.py"
    replace_once(path, "\n\nclass HoneyPotBaseProtocol(insults.TerminalProtocol, TimeoutMixin):\n",
                 TPOT_SESSION_COMMANDS + "\nclass HoneyPotBaseProtocol(insults.TerminalProtocol, TimeoutMixin):\n")
    replace_once(path, "    def __init__(self, avatar):\n        self.user = avatar\n        self.environ = avatar.environ\n",
                 "    def __init__(self, avatar):\n        self.user = avatar\n"
                 "        # T-Pot: the commands of this connection on Cowrie's (TpotCommands)\n"
                 "        self.commands = tpot_commands(avatar, HoneyPotBaseProtocol.commands)\n"
                 "        self.environ = avatar.environ\n")


def tpot_metering(*classes: str) -> str:
    """Module code after the commands of a module: the calls of these command classes count for the session's
    time (TPOT_BUDGET)."""
    return ("\n\n# T-Pot: the calls of these commands count for the session's time (TPOT_BUDGET)\n"
            "from cowrie.shell.command import tpot_meter_class  # noqa: E402\n\n"
            + "".join(f"tpot_meter_class({name})\n" for name in classes))


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
    patch_ssh_kex(cowrie_root)
    patch_ssh_sig_algs(cowrie_root)
    patch_netstat(cowrie_root)
    patch_net_tools(cowrie_root)
    patch_ps_session(cowrie_root)
    patch_shell_pid(cowrie_root)
    patch_pipeline_words(cowrie_root)
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
    patch_auth(cowrie_root)
    patch_ttylog(cowrie_root)
    patch_line_input(cowrie_root)
    patch_line_echo(cowrie_root)
    patch_history(cowrie_root)
    patch_ls(cowrie_root)
    patch_command_not_found(cowrie_root)
    patch_dd(cowrie_root)
    patch_wget(cowrie_root)
    patch_login_env(cowrie_root)
    patch_type(cowrie_root)
    patch_ls_long(cowrie_root)
    patch_bb_usage(cowrie_root)
    patch_budget(cowrie_root)
    patch_protocol_commands(cowrie_root)


if __name__ == "__main__":
    main()
