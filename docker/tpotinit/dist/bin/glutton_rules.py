#!/usr/bin/env python3
"""T-Pot owns Glutton's IPv4 TPROXY rules; Glutton never edits the firewall."""

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys


TABLE = "tpot_glutton"
SYSTEM_PORTS = {7634, *range(64290, 64310)}


def port_range(value):
    text = os.path.expandvars(str(value))
    if not re.fullmatch(r"[0-9]+(?:-[0-9]+)?", text):
        raise ValueError(f"Invalid or unresolved published port: {text!r}")
    parts = [int(part) for part in text.split("-")]
    first, last = parts[0], parts[-1]
    if not 1 <= first <= last <= 65535:
        raise ValueError(f"Published port outside 1-65535: {text!r}")
    return range(first, last + 1)


def protected_ports(compose):
    if not isinstance(compose, dict) or not isinstance(compose.get("services"), dict):
        raise ValueError("Compose services must be a mapping")
    ports = {"tcp": set(SYSTEM_PORTS), "udp": set(SYSTEM_PORTS)}
    for name, service in compose["services"].items():
        if not isinstance(service, dict):
            raise ValueError(f"Compose service {name!r} must be a mapping")
        bindings = service.get("ports")
        if bindings is None:
            continue
        if not isinstance(bindings, list):
            raise ValueError(f"Compose ports for {name!r} must be a list")
        for binding in bindings:
            if isinstance(binding, dict):
                published = binding.get("published")
                protocol = binding.get("protocol", "tcp")
            elif isinstance(binding, (str, int)) and not isinstance(binding, bool):
                address, _, protocol = str(binding).partition("/")
                protocol = protocol or "tcp"
                parts = address.rsplit(":", 2)
                published = parts[-2] if len(parts) > 1 else None
            else:
                raise ValueError(f"Invalid Compose port binding for {name!r}: {binding!r}")
            if not isinstance(protocol, str) or protocol not in ports:
                if protocol == "sctp":
                    continue
                raise ValueError(f"Unsupported port protocol: {protocol!r}")
            if published is None:
                raise ValueError("Glutton requires explicit published Compose ports, not random host ports")
            ports[protocol].update(port_range(published))
    return ports


def rules(compose, interface):
    if not interface or len(interface.encode()) > 15 or "\x00" in interface:
        raise ValueError("Invalid capture interface")
    ports = protected_ports(compose)
    lines = [
        f"add table ip {TABLE}",
        f"flush table ip {TABLE}",
        f"add chain ip {TABLE} prerouting {{ type filter hook prerouting priority mangle; policy accept; }}",
    ]
    prefix = f"add rule ip {TABLE} prerouting"
    for expression in (
        f"iifname != {json.dumps(interface)} return",
        "fib daddr type != local return",
        "ip saddr 127.0.0.0/8 return",
        "ip daddr 127.0.0.0/8 return",
        "ct state established,related return",
        # Give real host services priority, including sockets opened after T-Pot starts.
        "socket transparent 0 return",
    ):
        lines.append(f"{prefix} {expression}")
    for protocol in ("tcp", "udp"):
        values = ", ".join(str(port) for port in sorted(ports[protocol]))
        lines.append(f"{prefix} {protocol} dport {{ {values} }} return")
    for protocol, port in (("tcp", 5000), ("udp", 5001)):
        # nft_tproxy breaks this rule when no listener exists: no blackhole after SIGKILL.
        lines.append(f"{prefix} meta l4proto {protocol} tproxy ip to 127.0.0.1:{port} accept")
    return "\n".join(lines) + "\n"


def run(*command, input=None):
    return subprocess.run(command, input=input, text=True, capture_output=True,
                          check=True, timeout=30).stdout


def remove():
    tables = json.loads(run("nft", "-j", "list", "tables"))
    if any(item.get("table", {}).get("family") == "ip"
           and item.get("table", {}).get("name") == TABLE for item in tables["nftables"]):
        run("nft", "delete", "table", "ip", TABLE)
    if any(item.get("table", {}).get("family") == "ip"
           and item.get("table", {}).get("name") == "mangle" for item in tables["nftables"]):
        for line in run("iptables", "-w", "-t", "mangle", "-S", "PREROUTING").splitlines():
            spec = legacy_rule(line)
            if spec:
                run("iptables", "-w", "-t", "mangle", "-D", *spec[1:])
                print("Removed historical Glutton TPROXY rule:", line)


def legacy_rule(line):
    """Only the exact historical T-Pot Glutton shape, never a foreign/custom rule."""
    spec = shlex.split(line)
    if len(spec) < 6 or spec[:3] != ["-A", "PREROUTING", "-i"] or spec[4] != "-p":
        return None
    interface, protocol = spec[3], spec[5]
    if protocol not in ("tcp", "udp"):
        return None
    normalized = []
    index = 0
    while index < len(spec):
        if spec[index:index + 2] == ["-m", protocol]:
            index += 2
        else:
            normalized.append(spec[index].replace("ESTABLISHED,RELATED", "RELATED,ESTABLISHED"))
            index += 1
    if normalized[-2:] == ["--tproxy-mark", "0x0/0x0"]:
        normalized = normalized[:-2]
    if "--dport" not in normalized:
        return None
    ssh_index = normalized.index("--dport") + 1
    if ssh_index >= len(normalized):
        return None
    ssh = normalized[ssh_index]
    if not ssh.isascii() or not ssh.isdecimal() or not 1 <= int(ssh) <= 65535:
        return None
    expected = ["-A", "PREROUTING", "-i", interface, "-p", protocol,
                "-m", "state", "!", "--state", "RELATED,ESTABLISHED", "!", "--dport", ssh,
                "-j", "TPROXY", "--on-port", "5000" if protocol == "tcp" else "5001",
                "--on-ip", "127.0.0.1"]
    return spec if normalized == expected else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("set", "unset"))
    parser.add_argument("compose", nargs="?")
    args = parser.parse_args()
    try:
        if args.action == "unset":
            remove()
        else:
            if not args.compose:
                parser.error("set requires a Compose file")
            # A failed reconfiguration must not leave the previous edition's catch-all active.
            remove()
            compose = json.loads(run("yq", "-o=json", ".", args.compose))
            interface = run("sh", str(Path(__file__).with_name("capture-if.sh"))).strip()
            # One transaction installs all exceptions and redirection, or changes nothing.
            run("nft", "-f", "-", input=rules(compose, interface))
            print(f"Glutton: native nftables TPROXY on {interface}; host and Compose services protected.")
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        detail = exc.stderr if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        print(f"Glutton firewall {args.action} failed: {detail}", file=sys.stderr)
        print("Glutton must not start without its firewall; other T-Pot services may continue.",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
