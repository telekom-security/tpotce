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
    replace_once(path, "import traceback\n", "import traceback\nfrom pathlib import Path\n")
    replace_once(
        path,
        """        try:
            binary_data = read_data_bytes("txtcmds", *path.lstrip("/").split("/"))
            return self.txtcmd(binary_data)
        except FileNotFoundError:
            pass
""",
        """        txtcmds_path = CowrieConfig.get("honeypot", "txtcmds_path", fallback="")
        if txtcmds_path:
            operator_cmd = Path(txtcmds_path) / path.lstrip("/")
            if operator_cmd.is_file():
                return self.txtcmd(operator_cmd.read_bytes())

        try:
            binary_data = read_data_bytes("txtcmds", *path.lstrip("/").split("/"))
            return self.txtcmd(binary_data)
        except FileNotFoundError:
            pass
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
    start = text.index("        output = (\n")
    end = text.index("        )\n        for line in output:", start) + len("        )\n")
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


def main():
    parser = argparse.ArgumentParser(description="Patch Cowrie v3 persona command support.")
    parser.add_argument("--cowrie-root", required=True, type=Path)
    args = parser.parse_args()
    cowrie_root = args.cowrie_root.resolve()
    patch_protocol(cowrie_root)
    patch_ssh(cowrie_root)
    patch_netstat(cowrie_root)
    patch_service(cowrie_root)


if __name__ == "__main__":
    main()
