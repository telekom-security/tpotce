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
        """        skip_python_commands = {
            command.strip()
            for command in CowrieConfig.get(
                "shell", "skip_python_commands", fallback=""
            ).split(",")
            if command.strip()
        }

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
            for spelling in spellings:
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
    path = cowrie_root / "src" / "cowrie" / "commands" / "service.py"
    text = path.read_text(encoding="utf-8")
    start = text.find("        output = (\n")
    end = text.find("        )\n        for line in output:", start)
    if start < 0 or end < 0:
        raise RuntimeError(f"Could not find expected text in {path}")
    end += len("        )\n")
    replacement = """        output = (
            "[ + ]  cron",
            "[ + ]  dbus",
            "[ + ]  networking",
            "[ + ]  ssh",
            "[ + ]  rsyslog",
            "[ + ]  udev",
            "[ + ]  systemd-journald",
            "[ + ]  systemd-networkd",
            "[ - ]  bluetooth",
            "[ - ]  cups",
            "[ - ]  nfs-server",
            "[ - ]  postfix",
            "[ - ]  rpcbind",
        )
"""
    path.write_text(text[:start] + replacement + text[end:], encoding="utf-8")


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
    patch_ssh(cowrie_root)
    patch_netstat(cowrie_root)
    patch_service(cowrie_root)
    patch_free(cowrie_root)
    patch_grep(cowrie_root)
    patch_prompt(cowrie_root)
    patch_telnet_banner(cowrie_root)


if __name__ == "__main__":
    main()
