"""The network of a persona as ip, ss, netstat and ifconfig show it (the runtime file of [shell]
network): its interfaces with one MAC and one address per identity, its routes, the programs and
their styles, and the sockets the daemons of its ps listen on. The address is private: a cloud VM
behind NAT (AWS takes it from the host name ip-172-31-A-B), a host or NAS in a home network, a
router with its default LAN address and a WAN address of its upstream network."""
import ipaddress
import re

from . import system
from .common import rng_for

NETWORK_FILE = "network.json"

# the names of the network programs Cowrie's patched commands answer
NET_TOOLS = ("ip", "ss", "ifconfig", "netstat")

# vendor prefixes of MAC addresses (IEEE OUI registry; unverified which one a given unit has):
#   QEMU/KVM, Raspberry Pi Trading (Pi 4 / Pi 5), Supermicro, Mellanox, D-Link, TP-Link, Zyxel,
#   MediaTek, QNAP, Synology, Ubiquiti, Hikvision
OUIS = {"kvm": ("52:54:00",), "pi4": ("dc:a6:32", "e4:5f:01", "d8:3a:dd"), "pi5": ("2c:cf:67", "d8:3a:dd"),
        "gpu": ("3c:ec:ef",), "dgx": ("b8:ce:f6",), "dlink": ("1c:7e:e5", "b0:c5:54"),
        "tplink": ("50:c7:bf", "98:da:c4", "c0:06:c3"), "zyxel": ("bc:99:11", "5c:f4:ab"),
        "openwrt": ("00:0c:43",), "qnap": ("24:5e:be", "00:08:9b"), "synology": ("00:11:32", "90:09:d0"),
        "ubiquiti": ("24:a4:3c", "78:8a:20", "fc:ec:da"), "ipcam": ("44:19:b6", "c0:56:e3")}
# the default LAN address of the routers (vendor manuals); the Archer C7 bridges its LAN as br-lan
#   (/etc/init.d/networkx of the image); unverified: the names of the WAN interfaces
ROUTER_LAN = {"openwrt": ("br-lan", "192.168.1.1"), "dlink": ("br0", "192.168.0.1"),
              "tplink": ("br-lan", "192.168.0.1"), "ubiquiti": ("switch0", "192.168.1.1")}
ROUTER_WAN = {"openwrt": "wan", "dlink": "eth1", "tplink": "eth1", "ubiquiti": "eth0"}
DEVICE_IFACE = {"zyxel": "egiga0"}


def _mac(rng, oui=None):
    """A MAC address: the vendor prefix and three drawn bytes, or a locally administered unicast one."""
    if oui:
        return oui + "".join(f":{rng.randrange(256):02x}" for _ in range(3))
    first = rng.choice((0x02, 0x06, 0x0a, 0x0e, 0x12, 0x16))
    return f"{first:02x}" + "".join(f":{rng.randrange(256):02x}" for _ in range(5))


def link_local(mac):
    """The EUI-64 IPv6 link-local address of a MAC."""
    parts = [int(x, 16) for x in mac.split(":")]
    parts[0] ^= 0x02
    eui = parts[:3] + [0xff, 0xfe] + parts[3:]
    return str(ipaddress.IPv6Address(bytes([0xfe, 0x80] + [0] * 6 + eui)))


def _lan(rng):
    """An address of a home network (the router at .1)."""
    third = rng.choice((0, 1, 1, 1, 2, 178))
    return f"192.168.{third}.{rng.randint(20, 199)}", 24


def _private(rng):
    return f"10.{rng.randint(0, 254)}.{rng.randint(0, 254)}.{rng.randint(2, 250)}", 24


def _iface(name, mac, address, prefix, **extra):
    net = ipaddress.IPv4Interface(f"{address}/{prefix}").network
    found = {"name": name, "mac": mac, "inet": address, "prefix": prefix, "broadcast": str(net.broadcast_address),
             "network": str(net.network_address), "netmask": str(net.netmask), "inet6": link_local(mac),
             "mtu": 1500, "qdisc": "fq_codel", "state": "UP", "dynamic": True}
    found.update(extra)
    return found


def _server_interfaces(persona, ident, rng):
    hw = ident.hardware
    family = persona.family
    el = system.is_el(persona)
    if hw.provider == "aws":
        a, b = (int(x) for x in re.match(r"ip-172-31-(\d+)-(\d+)", ident.hostname).groups())
        iface = _iface("ens5", _mac(rng), f"172.31.{a}.{b}", 20, altname="enp0s5", mtu=9001, qdisc="mq",
                       lease=3600)
        gateway = f"172.31.{a & 0xF0}.1"
    elif hw.provider == "pi":
        oui = OUIS["pi5" if "Pi 5" in (hw.board or "") else "pi4"]
        address, prefix = _lan(rng)
        iface = _iface("eth0", _mac(rng, rng.choice(oui)), address, prefix, qdisc="mq", lease=86400)
        gateway = address.rsplit(".", 1)[0] + ".1"
    elif hw.provider in ("dgx", "gpu"):
        address, prefix = _private(rng)
        iface = _iface("eno1", _mac(rng, rng.choice(OUIS[hw.provider])), address, prefix, altname="enp1s0f0",
                       qdisc="mq", dynamic=False)
        gateway = address.rsplit(".", 1)[0] + ".1"
    else:
        address, prefix = _private(rng)
        mac = _mac(rng) if hw.provider == "do" else _mac(rng, OUIS["kvm"][0])
        if el or hw.provider == "do":
            # Red Hat's cloud images and DigitalOcean boot with net.ifnames=0
            iface = _iface("eth0", mac, address, prefix, lease=86400)
        else:
            iface = _iface("ens3", mac, address, prefix, altname="enp0s3", lease=86400)
        gateway = address.rsplit(".", 1)[0] + ".1"
    if family == "centos":
        iface["qdisc"] = "pfifo_fast"
    interfaces = [iface]
    if hw.provider == "pi":
        # the Wi-Fi of the board, not connected
        interfaces.append({"name": "wlan0", "mac": _mac(rng, iface["mac"][:8]), "mtu": 1500, "qdisc": "noqueue",
                           "state": "DOWN", "nocarrier": True})
    return interfaces, gateway


def _device_interfaces(persona, ident, rng):
    family = persona.family
    oui = rng.choice(OUIS.get(family, ("00:0c:43",)))
    if family == "qnap":
        # QNAP names a NAS after the last three bytes of its MAC (NAS5F3A2B)
        tail = ident.hostname[3:].lower()
        mac = oui + "".join(f":{tail[i:i + 2]}" for i in (0, 2, 4))
    else:
        mac = _mac(rng, oui)
    if family in ROUTER_LAN:
        name, address = ROUTER_LAN[family]
        lan = _iface(name, mac, address, 24, dynamic=False, qdisc="noqueue")
        wan_address, prefix = _private(rng)
        wan_mac = mac[:-2] + f"{(int(mac[-2:], 16) + 1) % 256:02x}"
        wan = _iface(ROUTER_WAN[family], wan_mac, wan_address, prefix, lease=86400)
        return [lan, wan], wan_address.rsplit(".", 1)[0] + ".1"
    address, prefix = _lan(rng)
    iface = _iface(DEVICE_IFACE.get(family, "eth0"), mac, address, prefix, lease=86400)
    return [iface], address.rsplit(".", 1)[0] + ".1"


# the sockets of the daemons of ps: (pattern of the command, comm, [(proto, address, port, fd, backlog)])
#   address "0.0.0.0" / "::" / "127.0.0.1" or "lan" (the persona's main address); unverified: the
#   file descriptors and backlogs other than those of sshd (128, as in the containers of the
#   releases), the ports of the vendor daemons
DAEMONS = (
    (r"sshd: |/usr/s?bin/sshd\b|^/usr/bin/sshd", "sshd", (("tcp", "0.0.0.0", 22, 3, 128), ("tcp", "::", 22, 4, 128))),
    (r"dropbear", "dropbear", (("tcp", "0.0.0.0", 22, 3, 20), ("tcp", "::", 22, 4, 20))),
    (r"systemd-resolved", "systemd-resolve", (("udp", "127.0.0.53%lo", 53, 13, 0), ("tcp", "127.0.0.53%lo", 53, 14, 4096),
                                              ("udp", "127.0.0.54", 53, 15, 0), ("tcp", "127.0.0.54", 53, 16, 4096))),
    (r"systemd-networkd", "systemd-network", (("udp", "dhcp", 68, 18, 0),)),
    (r"chronyd", "chronyd", (("udp", "127.0.0.1", 323, 5, 0), ("udp", "::1", 323, 6, 0))),
    (r"postfix/master", "master", (("tcp", "127.0.0.1", 25, 13, 100), ("tcp", "::1", 25, 14, 100))),
    (r"avahi-daemon: running", "avahi-daemon", (("udp", "0.0.0.0", 5353, 12, 0), ("udp", "::", 5353, 13, 0))),
    (r"ollama serve", "ollama", (("tcp", "127.0.0.1", 11434, 3, 4096),)),
    (r"jupyter-lab", "jupyter-lab", (("tcp", "127.0.0.1", 8888, 7, 128), ("tcp", "::1", 8888, 8, 128))),
    (r"^nginx: master", "nginx", (("tcp", "0.0.0.0", 80, 6, 511), ("tcp", "0.0.0.0", 443, 7, 511),
                                  ("tcp", "0.0.0.0", 5000, 8, 511), ("tcp", "0.0.0.0", 5001, 9, 511))),
    # the uhttpd of the Archer C7: listen_http and listen_https 0.0.0.0 of its /etc/config/uhttpd
    (r"uhttpd .*-s 0\.0\.0\.0:443", "uhttpd", (("tcp", "0.0.0.0", 80, 3, 1024), ("tcp", "0.0.0.0", 443, 4, 1024))),
    (r"uhttpd", "uhttpd", (("tcp", "0.0.0.0", 80, 3, 1024), ("tcp", "::", 80, 4, 1024))),
    (r"dnsmasq", "dnsmasq", (("udp", "0.0.0.0", 67, 3, 0), ("udp", "0.0.0.0", 53, 4, 0), ("tcp", "0.0.0.0", 53, 5, 32))),
    (r"odhcpd", "odhcpd", (("udp", "::", 547, 7, 0),)),
    (r"lighttpd", "lighttpd", (("tcp", "0.0.0.0", 80, 4, 1024), ("tcp", "0.0.0.0", 443, 5, 1024))),
    (r"^/usr/sbin/httpd|^/usr/bin/httpd", "httpd", (("tcp", "0.0.0.0", 80, 3, 5),)),
    (r"apache/bin/httpd", "httpd", (("tcp", "0.0.0.0", 80, 4, 511), ("tcp", "0.0.0.0", 443, 5, 511))),
    (r"apache_proxy", "apache_proxy", (("tcp", "0.0.0.0", 8080, 4, 511), ("tcp", "0.0.0.0", 443, 5, 511))),
    (r"_thttpd_", "_thttpd_", (("tcp", "127.0.0.1", 58080, 3, 1024),)),
    (r"udhcpd", "udhcpd", (("udp", "0.0.0.0", 67, 3, 0),)),
    # EdgeOS: the telnetd of its web UI's console (gui_telnet.service)
    (r"telnetd -p 55523 -b 127\.0\.0\.101", "telnetd", (("tcp", "127.0.0.101", 55523, 3, 1),)),
    (r"telnetd", "telnetd", (("tcp", "0.0.0.0", 23, 3, 1),)),
    # openbsd-inetd (refs systemd-telnet *-ss-tlnp-enabled: fd 7 and 8, backlog 128; 0.20160825 of 22.04 has
    #   no IPv6 socket)
    (r"^/usr/sbin/inetd$", "inetd", (("tcp", "0.0.0.0", 23, 7, 128), ("tcp", "::", 23, 8, 128))),
    # dhclient of CentOS 7 (ref dhclient-c7-ifup-ss-ulnp: *:68 on fd 5)
    (r"^/sbin/dhclient ", "dhclient", (("udp", "0.0.0.0", 68, 5, 0),)),
    (r"dnsProxy", "dnsProxy", (("udp", "0.0.0.0", 53, 3, 0), ("tcp", "0.0.0.0", 53, 4, 5))),
    (r"smbd", "smbd", (("tcp", "0.0.0.0", 445, 33, 50), ("tcp", "0.0.0.0", 139, 34, 50))),
    (r"nmbd", "nmbd", (("udp", "0.0.0.0", 137, 14, 0), ("udp", "0.0.0.0", 138, 15, 0))),
    (r"/home/davinci", "davinci", (("tcp", "0.0.0.0", 80, 9, 10), ("tcp", "0.0.0.0", 554, 10, 10))),
)


def ssh_fds(persona, ident):
    """(listening fd of IPv4, of IPv6, fd of a session's connection) of the SSH server (refs network-dhcp
    sshd-* and dropbear-alp-*: OpenSSH 7.4 3/4/3, 8.9 and 9.6 3/4/4, 10.0 6/7/7, dropbear 3/4/5;
    unverified: OpenSSH 8.7 of RHEL 9 and 9.2 of Debian 12 as 8.9, the dropbear of the devices as Alpine's)."""
    if "dropbear" in persona.ssh_profile:
        return 3, 4, 5
    match = re.search(r"OpenSSH_(\d+)\.(\d+)", ident.release.ssh_banner)
    version = (int(match.group(1)), int(match.group(2))) if match else (8, 0)
    if version >= (9, 8):
        return 6, 7, 7
    if version < (8, 0):
        return 3, 4, 3
    return 3, 4, 4


def sockets(persona, ident, processes, interfaces, gateway=None):
    """The listening sockets of the daemons of ps, with their PID."""
    main = interfaces[0]
    out = []
    listen4, listen6, _ = ssh_fds(persona, ident)
    code = system.codename(persona, ident)
    for process in processes:
        command = process["COMMAND"]
        if process["TTY"] != "?" and not command.startswith("telnetd"):
            continue
        for pattern, comm, entries in DAEMONS:
            if not re.search(pattern, command):
                continue
            for proto, address, port, fd, backlog in entries:
                if address == "127.0.0.54" and code != "noble":
                    # the DNS proxy stub of systemd 251 and later
                    continue
                if comm == "inetd" and address == "::" and code == "jammy":
                    continue
                if comm in ("sshd", "dropbear"):
                    fd = listen4 if address == "0.0.0.0" else listen6
                if address == "dhcp":
                    if not main.get("dynamic"):
                        continue
                    address = f"{main['inet']}%{main['name']}"
                family = 6 if ":" in address.split("%")[0] else 4
                out.append({"proto": proto, "family": family, "address": address, "port": port,
                            "pid": process["PID"], "comm": comm, "cmdline": command.split()[0] if command.startswith("/")
                            else command, "fd": fd, "backlog": backlog})
            break
    init = processes[0]
    if code == "noble":
        # ssh.socket of 24.04: systemd holds the listening sockets and hands them to sshd (fd 3 and 4)
        # (ref systemd-telnet ubuntu-24-04-session-ss-tanp: ("sshd",fd=3),("systemd",pid=1,fd=80), backlog 4096)
        for entry in out:
            if entry["comm"] == "sshd":
                entry.update({"backlog": 4096, "also": [["systemd", init["PID"], 80 if entry["family"] == 4 else 82,
                                                        init["COMMAND"].split()[0]]]})
    if persona.family in ("rhel", "centos"):
        # telnet.socket: systemd itself listens on 23, one socket for both families (ss: *:23)
        # (unverified on CentOS 7: the fd and backlog of systemd 219)
        out.append({"proto": "tcp", "family": 6, "address": "*", "port": 23, "pid": init["PID"], "comm": "systemd",
                    "cmdline": init["COMMAND"].split()[0], "fd": 32 if persona.family == "rhel" else 25,
                    "backlog": 4096 if persona.family == "rhel" else 128})
    if main.get("dynamic") and any("NetworkManager" in p["COMMAND"] for p in processes) and persona.family != "centos":
        # the internal DHCP client of NetworkManager: a connected socket to the server, no listener
        # (refs nm-al9-after11s-ss-uanp, nm-d12-after11s-ss-uanp)
        manager = next(p for p in processes if "NetworkManager" in p["COMMAND"])
        # (the DHCP server: the gateway of the network)
        server = gateway or main["inet"].rsplit(".", 1)[0] + ".1"
        out.append({"proto": "udp", "family": 4, "address": f"{main['inet']}%{main['name']}", "port": 68,
                    "peer": server, "peer_port": 67, "state": "ESTAB", "pid": manager["PID"],
                    "comm": "NetworkManager", "cmdline": manager["COMMAND"], "fd": 26 if persona.family == "rhel" else 28,
                    "backlog": 0})
    return out


IPROUTE2_LIBBPF = {"noble": "1.3.0", "jammy": "0.5.0", "bookworm": "1.1.2", "trixie": "1.5.0"}
# RHEL 9 (unverified: the libbpf of each minor release)
RHEL_LIBBPF = {"9.4": "1.3.0", "9.5": "1.4.0", "9.6": "1.5.0"}


def _upstream(version):
    return version.split(":", 1)[-1].split("-", 1)[0]


def tool_styles(persona, ident):
    """{program: {"style", "version", ...}} of the network programs the persona has."""
    tools = {}
    family = persona.family
    if persona.kind == "server":
        installed = system.installed_tools(persona, ident)
        code = system.codename(persona, ident)
        if "iproute2" in installed:
            if family == "centos":
                version, libbpf = "ss170501", ""
            elif family == "rhel":
                version = _upstream(system.rpm_evr(system.RPM_INSTALLED["iproute"][3], ident.release.os_version_id))
                libbpf = RHEL_LIBBPF.get(ident.release.os_version_id, "1.3.0")
            else:
                version = _upstream(system.apt_packages(persona, ident)["installed"]["iproute2"])
                libbpf = IPROUTE2_LIBBPF.get(code, "")
            for name in ("ip", "ss"):
                tools[name] = {"style": "iproute2", "version": version, "libbpf": libbpf}
        if "net-tools" in installed:
            if code in ("bookworm", "trixie", "noble"):
                version = "2.10"
            else:
                version = "2.10-alpha"
            for name in ("ifconfig", "netstat"):
                tools[name] = {"style": "net-tools", "version": version,
                               "si": family == "ubuntu", "narrow": code == "jammy", "basename": family == "centos"}
        return tools
    applets = set(system.busybox_applets(persona, ident))
    if family == "ubiquiti":
        # EdgeOS: iproute2 4.14 (ss171113) and the net-tools of Debian 9
        tools["ip"] = tools["ss"] = {"style": "iproute2", "version": "ss171113", "libbpf": ""}
        for name in ("ifconfig", "netstat"):
            tools[name] = {"style": "net-tools", "version": "2.10-alpha", "si": False, "narrow": True,
                           "basename": True}
        return tools
    if family == "synology":
        for name in ("ifconfig", "netstat"):
            tools[name] = {"style": "net-tools", "version": "2.10-alpha", "si": False, "narrow": True,
                           "basename": True}
        return tools
    banner = ident.release.extra.get("busybox", "")
    for name in ("ip", "ifconfig", "netstat"):
        if name in applets:
            tools[name] = {"style": "busybox", "version": banner}
    if family == "tplink":
        # the Archer C7 has iproute2 3.3.0 as /usr/sbin/ip (ip -V: "ip utility, iproute2-ss3.3.0-1-openwrt")
        tools["ip"] = {"style": "iproute2", "version": "ss3.3.0-1-openwrt", "libbpf": ""}
    return tools


def tool_paths(persona, ident):
    """{program: (path, link target or None)} of the network programs of a device (servers: the
    tools of their packages, system.tool_programs)."""
    if persona.kind == "server":
        return {}
    tools = tool_styles(persona, ident)
    banner = ident.release.extra.get("busybox")
    paths = {"ip": "sbin/ip", "ifconfig": "sbin/ifconfig", "netstat": "bin/netstat", "ss": "sbin/ss"}
    found = {}
    for name, style in tools.items():
        if style["style"] == "busybox" and banner:
            target = "busybox" if name == "netstat" else "../bin/busybox"
            found[name] = (paths[name], target)
        elif persona.family == "tplink" and name == "ip":
            found[name] = ("usr/sbin/ip", None)
        else:
            found[name] = (paths[name], None)
    return found


def network(persona, ident, processes):
    """What the patched ip, ss, netstat and ifconfig read ([shell] network)."""
    rng = rng_for(ident, "network")
    if persona.kind == "server":
        interfaces, gateway = _server_interfaces(persona, ident, rng)
    else:
        interfaces, gateway = _device_interfaces(persona, ident, rng)
    kernel = tuple(int(x) for x in re.findall(r"\d+", ident.release.kernel_version)[:2])
    family = persona.family
    if persona.kind == "server":
        manager = "networkd" if any("systemd-networkd" in p["COMMAND"] for p in processes) else \
            "nm" if any("NetworkManager" in p["COMMAND"] for p in processes) else "static"
        if family == "centos":
            manager = "nm-old"
        if not interfaces[0].get("dynamic"):
            manager = "static"
    else:
        manager = "device"
    for iface in interfaces:
        if "inet" in iface and manager.startswith("nm"):
            iface["noprefixroute"] = True
        if persona.kind == "device" and family not in ("openwrt", "synology") and iface.get("qdisc") == "fq_codel":
            # no systemd sets fq_codel: the kernel's pfifo_fast (unverified on EdgeOS)
            iface["qdisc"] = "pfifo_fast"
    gateway_mac = _mac(rng, rng.choice(OUIS["tplink"])) if persona.kind == "device" or \
        ident.hardware.provider == "pi" else _mac(rng)
    # the traffic of the interfaces since boot (bytes per second and the mean packet size, drawn per identity)
    for iface in interfaces:
        if iface.get("state") == "UP":
            iface["rates"] = [rng.randint(800, 6000), rng.randint(380, 1100)]
    return {"tools": tool_styles(persona, ident), "interfaces": interfaces, "gateway": gateway,
            "session_fd": ssh_fds(persona, ident)[2], "telnet": telnet_server(persona, ident),
            "gateway_mac": gateway_mac, "manager": manager, "kernel": list(kernel), "booted_at": ident.booted_at,
            "sockets": sockets(persona, ident, processes, interfaces, gateway), "family": family,
            "resolved": any("systemd-resolved" in p["COMMAND"] for p in processes),
            "gateway_name": "gateway" if family == "centos" else "_gateway" if system.has_systemd(persona) else ""}


def telnet_server(persona, ident):
    """How the session of a telnet login looks in ps and ss ([shell] telnet_server, read by tpot_session):
    inetutils (telnetd of inetd, then /bin/login -p -h; trixie's login -- user), netkit (in.telnetd of
    telnet.socket, then login -- user), or "" (the BusyBox or vendor telnetd of a device serves it itself)."""
    if persona.kind != "server":
        return ""
    if system.is_el(persona):
        return "netkit"
    return "inetutils-2.6" if system.codename(persona, ident) == "trixie" else "inetutils"
