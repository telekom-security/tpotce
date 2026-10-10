#!/usr/bin/env python3

import argparse
from pathlib import Path


def replace_once(path, old, new):
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"Could not find expected text in {path}")
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
        """        if path in self.commands:
            return self.commands[path]
""",
        """        if path in self.commands and not skip_python_command(path):
            return self.commands[path]
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


def patch_telnet_banner(cowrie_root):
    """Device personas have no /etc/issue.net: upstream already sends an empty
    telnet banner then, but logs a traceback on every start."""
    path = cowrie_root / "src" / "cowrie" / "telnet" / "factory.py"
    replace_once(
        path,
        """        except FileNotFoundError:
            self._log.failure("ERROR: Failed to load /etc/issue.net")
            self.banner = b""
""",
        """        except FileNotFoundError:
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
    patch_ssh(cowrie_root)
    patch_netstat(cowrie_root)
    patch_service(cowrie_root)
    patch_free(cowrie_root)
    patch_telnet_banner(cowrie_root)


if __name__ == "__main__":
    main()
