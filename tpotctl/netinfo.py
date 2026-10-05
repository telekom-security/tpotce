"""The network interfaces of this host, and the one the NSM services capture on.

detect() follows docker/tpotinit/dist/bin/capture-if.sh, which Suricata, P0f and
Glutton use when TPOT_CAPTURE_INTERFACE is empty: the interface of the route to the
internet (IPv4, then IPv6), else the first interface that is up and has a global
IPv4 address. Keep both in sync.
"""

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Callable, List, Optional

INTERNAL = re.compile(r"^(lo|docker|br-|veth)")


@dataclass
class Interface:
    name: str
    up: bool
    addresses: List[str] = field(default_factory=list)
    global_v4: bool = False

    @property
    def internal(self) -> bool:
        return bool(INTERNAL.match(self.name))


def parse_ip_json(text: str) -> List[Interface]:
    """`ip -j address`."""
    out = []
    for item in json.loads(text or "[]"):
        flags = item.get("flags") or []
        name = item.get("ifname", "")
        addresses, global_v4 = [], False
        for info in item.get("addr_info") or []:
            if info.get("local"):
                addresses.append(f"{info['local']}/{info.get('prefixlen', '')}".rstrip("/"))
            if info.get("family") == "inet" and info.get("scope") == "global":
                global_v4 = True
        out.append(Interface(name, "UP" in flags or item.get("operstate") == "UP", addresses, global_v4))
    return out


def parse_route_dev(text: str) -> str:
    """The word after "dev" in `ip route get`."""
    words = text.split()
    for index, word in enumerate(words[:-1]):
        if word == "dev":
            return words[index + 1]
    return ""


def parse_gateways(text: str) -> List[str]:
    """The gateways of `ip -j route show default`."""
    try:
        routes = json.loads(text or "[]")
    except ValueError:
        return []
    return [route["gateway"] for route in routes if route.get("gateway")]


def default_gateways(run: Callable = subprocess.run) -> List[str]:
    return parse_gateways(_run(["ip", "-j", "route", "show", "default"], run)) + \
        parse_gateways(_run(["ip", "-6", "-j", "route", "show", "default"], run))


def _run(command: List[str], run: Callable) -> str:
    if not shutil.which(command[0]):
        return ""
    try:
        proc = run(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout if proc.returncode == 0 else ""


def interfaces(run: Callable = subprocess.run, sys_net: str = "/sys/class/net") -> List[Interface]:
    text = _run(["ip", "-j", "address"], run)
    if text:
        try:
            return parse_ip_json(text)
        except ValueError:
            pass
    out = []
    try:
        names = sorted(os.listdir(sys_net))
    except OSError:
        return out
    for name in names:
        try:
            with open(os.path.join(sys_net, name, "operstate"), encoding="utf-8") as handle:
                up = handle.read().strip() in ("up", "unknown")
        except OSError:
            up = False
        out.append(Interface(name, up))
    return out


def detect(run: Callable = subprocess.run, found: Optional[List[Interface]] = None) -> str:
    """The interface capture-if.sh picks for an empty TPOT_CAPTURE_INTERFACE, "" if none."""
    name = parse_route_dev(_run(["ip", "route", "get", "1.1.1.1"], run))
    if not name:
        name = parse_route_dev(_run(["ip", "-6", "route", "get", "2606:4700:4700::1111"], run))
    if name:
        return name
    for interface in found if found is not None else interfaces(run):
        if interface.up and interface.global_v4 and not interface.internal:
            return interface.name
    return ""
