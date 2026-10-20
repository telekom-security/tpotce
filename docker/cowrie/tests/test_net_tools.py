"""ip, ss, netstat and ifconfig of the patched Cowrie (patch_net_tools) over the persona's network
([shell] network). The reference texts (fixtures/net_tools.json) are the outputs of the real programs
in containers of ubuntu:24.04, ubuntu:22.04, debian:12, debian:13 and centos:7 with sshd listening,
and of busybox images with `busybox httpd -p 8080`: the network of a test is the container's."""
import ipaddress
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_patch import FIXTURE, PATCH, FakeConfig, module_all
from tpot_personas import catalog, network
from tpot_personas.identity import draw

REFS = json.loads((Path(__file__).parent / "fixtures" / "net_tools.json").read_text(encoding="utf-8"))
MAIN = json.loads((Path(__file__).parent / "fixtures" / "net_main.json").read_text(encoding="utf-8"))
JSON = json.loads((Path(__file__).parent / "fixtures" / "net_json.json").read_text(encoding="utf-8"))
STATES = {"ESTABLISHED": "ESTAB", "TIME_WAIT": "TIME-WAIT", "FIN_WAIT2": "FIN-WAIT-2", "CLOSE_WAIT": "CLOSE-WAIT"}


def real_card(text):
    """The reference without what makes eth0 a veth: its peer (eth0@if1536, padded to the same width)
    and the netns of the peer."""
    text = re.sub(r"^(\d+: \S+?)@if\d+:", r"\1:", text, flags=re.M)
    text = re.sub(r"^(\S+?)@if\d+( +)", lambda m: m.group(1).ljust(len(m.group(0))), text, flags=re.M)
    return text.replace(" link-netnsid 0", "")


def main_network(image):
    """The network of the reference container of net_main.json: eth0 with its MAC, address and
    counters, sshd listening, the connections of netstat -tnp."""
    cases = MAIN["images"][image]["cases"]
    data = container_network(image, MAIN)
    numbers = re.findall(r"^ +(\d+)\s+(\d+)", cases["ip -s link show dev eth0"][0], re.M)
    (rx_bytes, rx_packets), (tx_bytes, tx_packets) = [(int(a), int(b)) for a, b in numbers]
    lo = int(re.search(r"^lo +\d+ +(\d+)", cases["netstat -i"][0], re.M).group(1))
    iface = data["interfaces"][0]
    index = int(re.match(r"(\d+):", cases["ip a show dev eth0"][0]).group(1))
    iface.update(mac="52:54:00:12:34:56", mtu=1500, counters=[rx_bytes, rx_packets, tx_bytes, tx_packets], index=index)
    data["lo_counters"] = [0, lo, 0, lo]
    for line in cases["netstat -tnp"][0].splitlines()[2:]:
        proto, _, _, local, peer, state, owner = line.split()[:7]
        host, port = local.rsplit(":", 1)
        peer_host, peer_port = peer.rsplit(":", 1)
        pid, comm = (int(owner.split("/")[0]), owner.split("/")[1]) if owner != "-" else (0, "")
        fd = int(re.search(rf'pid={pid},fd=(\d+)', cases["ss -tnp"][0]).group(1)) if pid else 0
        data["sockets"].append({"proto": "tcp", "family": 4, "address": host, "port": int(port), "peer": peer_host,
                                "peer_port": int(peer_port), "state": STATES[state], "pid": pid, "comm": comm,
                                "cmdline": comm, "fd": fd, "backlog": 0})
    return data
NET_TOOLS_STYLE = {"ubuntu_24.04": ("2.10", False, False, True), "ubuntu_22.04": ("2.10-alpha", True, False, True),
                   "debian_13": ("2.10", False, False, False), "debian_12": ("2.10", False, False, False),
                   "centos_7": ("2.10-alpha", False, True, False)}


class Protocol:
    hostname = "honeypot"

    class user:  # noqa: N801 - as Cowrie's avatar
        uid = 0

    class events:  # noqa: N801
        @staticmethod
        def dispatch(*args, **kwargs):
            pass


class Pipe:
    """The command's pipe (Cowrie's pp); patched sudo marks it with tpot_sudo."""


class Base:
    def __init__(self, args, protocol=None, user=None, sudo=False):
        self.args = list(args)
        self.protocol = protocol or Protocol()
        # the shell's user (Cowrie's cmd.user, the target user after su), else the session's
        uid = getattr(getattr(self.protocol, "user", None), "uid", 0)
        self.user = user or {"uid": uid, "username": "root" if uid == 0 else "phil"}
        self.pp = Pipe()
        if sudo:
            self.pp.tpot_sudo = True
        self.out, self.err, self.exit_code, self.exited = "", "", 0, False

    def write(self, text):
        self.out += text

    def errorWrite(self, text):  # noqa: N802 - Cowrie's name
        self.err += text

    def exit(self, code=None):
        self.exited = True


_ROOT = None


def patched_source():
    global _ROOT
    if _ROOT is None:
        _ROOT = Path(tempfile.mkdtemp()) / "cowrie"
        shutil.copytree(FIXTURE, _ROOT)
        subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(_ROOT)], check=True, capture_output=True)
    return (_ROOT / "src/cowrie/commands/netstat.py").read_text()


class Log:
    @staticmethod
    def err(*args, **kwargs):
        raise AssertionError(f"internal error: {args}")


def container_network(image, refs=None):
    """The network of the reference container: eth0 with its address, sshd listening on 22."""
    refs = refs or REFS
    cases = refs["images"][image]["cases"] if refs is REFS else {**REFS["images"][image]["cases"], **refs["images"][image]["cases"]}
    pid, fd = (int(x) for x in re.search(r'pid=(\d+),fd=(\d+)', cases["ss -tlnp"][0]).groups())
    address = re.search(r"src (\S+)", cases["ip r s" if "ip r s" in cases else "ip r"][0]).group(1)
    version, narrow, basename, si = NET_TOOLS_STYLE[image]
    net = ipaddress.IPv4Interface(f"{address}/16").network
    proc = "/usr/sbin/sshd -D" if image == "centos_7" else "sshd: /usr/sbin/sshd -D [listener] 0 of 10-100 startups"
    sshd = {"pid": pid, "comm": "sshd", "cmdline": proc, "backlog": 128, "proto": "tcp", "port": 22}
    libbpf = (re.findall(r"libbpf (\S+)", REFS["images"][image]["cases"]["ip -V"][0]) or [""])[0]
    return {"tools": {"ip": {"style": "iproute2", "version": REFS["images"][image]["iproute2"], "libbpf": libbpf},
                      "ss": {"style": "iproute2", "version": REFS["images"][image]["iproute2"], "libbpf": ""},
                      "netstat": {"style": "net-tools", "version": version, "narrow": narrow, "basename": basename,
                                  "si": si},
                      "ifconfig": {"style": "net-tools", "version": version, "narrow": narrow, "basename": basename,
                                   "si": si}},
            "interfaces": [{"name": "eth0", "mac": "02:42:ac:11:00:03", "inet": address, "prefix": 16,
                            "broadcast": str(net.broadcast_address), "network": str(net.network_address),
                            "netmask": str(net.netmask), "inet6": None, "mtu": 65535, "qdisc": "noqueue",
                            "state": "UP", "dynamic": False}],
            "gateway": address.rsplit(".", 2)[0] + ".0.1", "gateway_mac": "d6:82:59:7f:54:31", "manager": "device",
            "kernel": [7, 0], "booted_at": 0, "family": "debian", "resolved": False, "gateway_name": "",
            "sockets": [dict(sshd, family=4, address="0.0.0.0", fd=fd), dict(sshd, family=6, address="::", fd=fd + 1)]}


def busybox_network(image):
    banner = re.search(r"(BusyBox v[^\n]*\)) multi-call", REFS["images"][image]["cases"]["ip x"][1]).group(1)
    cases = REFS["images"][image]["cases"]
    address = re.search(r"src (\S+)", cases["ip r"][0]).group(1)
    pid = int(re.search(r"(\d+)/busybox", cases["netstat -tlnp"][0]).group(1))
    return banner, {
        "tools": {name: {"style": "busybox", "version": banner} for name in ("ip", "netstat", "ifconfig")},
        "interfaces": [{"name": "eth0", "mac": "02:42:ac:11:00:02", "inet": address, "prefix": 16,
                        "broadcast": "172.17.255.255", "network": "172.17.0.0", "netmask": "255.255.0.0",
                        "inet6": None, "mtu": 65535, "qdisc": "noqueue", "state": "UP", "dynamic": False}],
        "gateway": "172.17.0.1", "gateway_mac": "d6:82:59:7f:54:31", "manager": "device", "kernel": [7, 0],
        "booted_at": 0, "family": "openwrt", "resolved": False, "gateway_name": "",
        "sockets": [{"proto": "tcp", "family": 6, "address": "::", "port": 8080, "pid": pid, "comm": "busybox",
                     "cmdline": "busybox httpd -p 8080 -h /tmp/www", "fd": 3, "backlog": 9}]}


class NetToolsTest(unittest.TestCase):
    def run_cmd(self, data, name, args, keys=None, protocol=None, user=None, sudo=False):
        path = Path(tempfile.mkdtemp()) / "network.json"
        path.write_text(json.dumps(data))
        values = {("shell", "network"): str(path), **{("shell", k): v for k, v in (keys or {}).items()}}
        namespace = module_all(patched_source(), {"HoneyPotCommand": Base, "CowrieConfig": FakeConfig(values),
                                                  "log": Log, "commands": {}})
        classes = {"ip": "Command_tpot_ip", "ss": "Command_tpot_ss", "netstat": "Command_tpot_netstat",
                   "ifconfig": "Command_tpot_ifconfig"}
        cmd = namespace[classes[name]](args, protocol, user, sudo)
        cmd.start()
        self.assertTrue(cmd.exited)
        return cmd.out, cmd.err, cmd.exit_code

    def test_the_output_of_the_real_programs(self):
        """The cases of the reference whose state the container's network reproduces."""
        for image, ref in REFS["images"].items():
            if image.startswith("busybox"):
                continue
            data = container_network(image)
            for case, (out, err, code) in ref["cases"].items():
                words = case.split()
                if case in ("ip -6 a",) or words[0] not in ("ip", "ss", "netstat", "ifconfig"):
                    continue
                with self.subTest(image=image, case=case):
                    self.assertEqual(self.run_cmd(data, words[0], words[1:]), (out, err, code))

    def test_the_main_interface_of_the_real_programs(self):
        """ip, ss, netstat and ifconfig on the reference's eth0 and connections (net_main.json)."""
        exact = ("ip a show dev eth0", "ip link show dev eth0", "ip -s link show dev eth0", "ip -br a show dev eth0",
                 "ip -br link show dev eth0", "ip -o a show dev eth0", "ip -o link show dev eth0", "ip a l dev eth0",
                 "ip r s", "ip -j a show dev lo", "ip -p a show dev lo", "ifconfig eth0", "netstat -i", "ss -tn",
                 "ss -tnp", "netstat -tn", "netstat -tnp")
        for image, ref in MAIN["images"].items():
            data = main_network(image)
            for case in exact:
                out, err, code = ref["cases"][case]
                words = case.split()
                with self.subTest(image=image, case=case):
                    self.assertEqual(self.run_cmd(data, words[0], words[1:]), (real_card(out), err, code))
            with self.subTest(image=image, case="ss -s"):
                out = self.run_cmd(data, "ss", ["-s"])[0]
                want = ref["cases"]["ss -s"][0]
                # the counts are those of the persona, the layout that of the release
                self.assertEqual([re.sub(r"\d+ *", "N", line) for line in out.splitlines()],
                                 [re.sub(r"\d+ *", "N", line) for line in want.splitlines()])

    def test_ip_6_shows_the_short_header(self):
        for image in ("ubuntu_24.04", "debian_13", "centos_7"):
            data = container_network(image)
            with self.subTest(image=image):
                self.assertEqual(self.run_cmd(data, "ip", ["-6", "a"]), tuple(REFS["images"][image]["cases"]["ip -6 a"]))

    def test_the_output_of_busybox(self):
        for image, ref in REFS["images"].items():
            if not image.startswith("busybox"):
                continue
            banner, data = busybox_network(image)
            libc = "musl" if "musl" in image else "uclibc" if ("uclibc" in image or "1.27" in image) else "glibc"
            for case, (out, err, code) in ref["cases"].items():
                words = case.split()
                with self.subTest(image=image, case=case):
                    self.assertEqual(self.run_cmd(data, words[0], words[1:], {"text_banner": banner, "text_libc": libc}),
                                     (out, err, code))

    def test_the_session_is_an_established_connection(self):
        data = container_network("ubuntu_24.04")

        class Session(Protocol):
            clientIP = "203.0.113.7"
            realClientPort = 51234

        out = self.run_cmd(data, "ss", ["-tn"], protocol=Session())[0]
        self.assertEqual(out.splitlines()[1].split(), ["ESTAB", "0", "0", "172.17.0.3:22", "203.0.113.7:51234"])
        out = self.run_cmd(data, "netstat", ["-antp"], protocol=Session())[0]
        self.assertIn("tcp        0      0 172.17.0.3:22           203.0.113.7:51234       ESTABLISHED ", out)
        out = self.run_cmd(data, "ss", ["-tln"], protocol=Session())[0]
        self.assertNotIn("ESTAB", out)

    def test_every_persona_shows_one_network(self):
        """ip, ifconfig, ss and netstat of a persona name the same MAC, address and listening ports."""
        for persona in catalog.PERSONAS:
            ident = draw(persona, 5)
            content = persona.builder(persona, ident)
            data = network.network(persona, ident, content.processes)
            main = data["interfaces"][0]
            ports = sorted({s["port"] for s in data["sockets"] if s["proto"] == "tcp"})
            with self.subTest(persona=persona.id):
                self.assertIn(22, ports)
                if "ip" in data["tools"]:
                    out = self.run_cmd(data, "ip", ["a"])[0]
                    self.assertIn(main["mac"], out)
                    self.assertIn(f"inet {main['inet']}/{main['prefix']}", out)
                    self.assertIn(f"default via {data['gateway']}", self.run_cmd(data, "ip", ["r"])[0])
                if "ifconfig" in data["tools"]:
                    out = self.run_cmd(data, "ifconfig", [])[0]
                    self.assertIn(main["mac"].upper() if data["tools"]["ifconfig"]["style"] == "busybox"
                                  else main["mac"], out)
                    self.assertIn(main["inet"], out)
                if "ss" in data["tools"]:
                    out = self.run_cmd(data, "ss", ["-tlnp"])[0]
                    self.assertEqual(sorted({int(m) for m in re.findall(r":(\d+)\s", out)}), ports)
                if "netstat" in data["tools"]:
                    out = self.run_cmd(data, "netstat", ["-tlnp"])[0]
                    self.assertEqual(sorted({int(m) for m in re.findall(r"^tcp6? .*?:(\d+) ", out, re.M)}), ports)
                if ident.hardware.provider == "aws":
                    self.assertEqual(main["inet"], ident.hostname[3:].replace("-", "."))

    def test_the_session_belongs_to_its_process_and_its_route(self):
        """A router's connection is on its WAN address (the interface of the default route); the
        connection belongs to the session's sshd, with the PID ps shows for it."""
        persona = catalog.by_id("openwrt")
        ident = draw(persona, 5)
        data = network.network(persona, ident, persona.builder(persona, ident).processes)
        data["tools"]["ss"] = {"style": "iproute2", "version": "6.1.0", "libbpf": ""}

        class Session(Protocol):
            clientIP = "203.0.113.7"
            realClientPort = 51234

        session = Session()
        out = self.run_cmd(data, "ss", ["-tnp"], protocol=session)[0]
        wan = data["interfaces"][1]["inet"]
        row = [line for line in out.splitlines() if line.startswith("ESTAB")][0]
        self.assertEqual(row.split()[3], f"{wan}:22")
        # dropbear's session holds the connection on fd 5 (ref network-dhcp dropbear-alp-session-root-ss-tanp-grep22)
        self.assertIn(f'users:(("sshd",pid={session.tpot_session_pid},fd=5))', row)
        self.assertIn(f"{wan}:22", self.run_cmd(data, "netstat", ["-tn"], protocol=session)[0])
        # netstat names the process by its title (net-tools 2.10) or the program (2.0 of CentOS 7)
        for basename, name in ((False, "sshd: root@pts"), (True, "sshd")):
            data["tools"]["netstat"] = {"style": "net-tools", "version": "2.10", "basename": basename}
            out = self.run_cmd(data, "netstat", ["-tnp"], protocol=session)[0]
            self.assertIn(f"ESTABLISHED {session.tpot_session_pid}/{name}".ljust(32), out + " " * 32)

    def test_json_in_the_layout_of_iproute2(self):
        """ip -j and -j -p as iproute2 6.1.0 writes them (net_json.json): -p in the layout of its
        json_writer, the metric of a DHCP address, linkmode for links, no link layer for -4."""
        import time as real_time
        data = container_network("ubuntu_24.04")
        data.update(manager="networkd", booted_at=real_time.time(), sockets=[])
        data["interfaces"][0].update(mac="e6:10:55:5e:64:26", inet="172.17.0.2", broadcast="172.17.255.255",
                                     network="172.17.0.0", mtu=1500, dynamic=True, lease=3600, index=11)
        veth = re.compile(r'\n *"link_index": \d+,|\n *"link_netnsid": \d+,|,\n *"link_netnsid": \d+(?=\n)|'
                          r'"link_index":\d+,|,"link_netnsid":\d+')
        for case, (out, err, code) in JSON["images"]["ubuntu_24.04"]["cases"].items():
            words = case.split()
            with self.subTest(case=case):
                self.assertEqual(self.run_cmd(data, words[0], words[1:]),
                                 (real_card(veth.sub("", out)), err, code))

    def session_persona(self, persona_id, telnet=False, ssh_version=None):
        """A session on the network of a persona, with its ps list."""
        persona = catalog.by_id(persona_id)
        ident = draw(persona, 5)
        processes = persona.builder(persona, ident).processes
        data = network.network(persona, ident, processes)
        if "ss" not in data["tools"]:
            data["tools"]["ss"] = {"style": "iproute2", "version": "6.1.0", "libbpf": ""}
        name = "HoneyPotInteractiveTelnetProtocol" if telnet else "HoneyPotInteractiveProtocol"

        class Server:
            process = processes

        session = type(name, (Protocol,), {"clientIP": "203.0.113.7", "realClientPort": 51234})()
        session.user = type("user", (), {"uid": 0, "server": Server})
        keys = {"ssh_version": ssh_version or persona.releases[0].ssh_version}
        if persona.kind != "server":
            keys["top_style"] = "busybox"
        return data, session, keys, processes

    def estab(self, data, session, keys):
        out = self.run_cmd(data, "ss", ["-tnp"], keys, protocol=session)[0]
        return [line for line in out.splitlines() if line.startswith("ESTAB")]

    def test_the_session_belongs_to_the_daemon_of_the_persona(self):
        """dropbear on a dropbear device, the sshd (sshd-session from OpenSSH 9.8) of a server, with
        one PID just below the login shell of the ps list."""
        # the fd of the connection: dropbear 5, OpenSSH 9.6 4, 10.0 7 (refs network-dhcp *-session-root-ss-tanp-grep22)
        for persona_id, ssh_version, comm, title, fd in (
                ("openwrt", None, "dropbear", "dropbear", 5),
                ("ubuntu-noble", "OpenSSH_9.6p1 Ubuntu-3ubuntu13.14, OpenSSL 3.0.13 30 Jan 2024", "sshd",
                 "sshd: root@pts/0", 4),
                ("debian-trixie", "OpenSSH_10.0p2 Debian-7, OpenSSL 3.5.1 1 Jul 2025", "sshd-session",
                 "sshd-session: root@pts/0", 7)):
            data, session, keys, processes = self.session_persona(persona_id, ssh_version=ssh_version)
            with self.subTest(persona=persona_id):
                row = self.estab(data, session, keys)
                self.assertEqual(len(row), 1, row)
                pid = session.tpot_session_pid
                # from OpenSSH 9.8 the [priv] process of root holds it too, 8 PIDs below on Debian
                # (ref sshd-d13-session-root-ss-tanp-grep22)
                also = f',("{comm}",pid={pid - 8},fd={fd})' if comm == "sshd-session" else ""
                self.assertIn(f'users:(("{comm}",pid={pid},fd={fd}){also})', row[0])
                shell = [p["PID"] for p in processes if str(p["TTY"]).startswith("pts/")]
                if shell:
                    self.assertEqual(session.tpot_session_pid, shell[0] - 1)
                data["tools"]["netstat"] = {"style": "net-tools", "version": "2.10", "basename": False}
                out = self.run_cmd(data, "netstat", ["-tnp"], keys, protocol=session)[0]
                # net-tools 2.10: the title, PID/title cut to 19 characters in a field of 20, the last column
                # net-tools names the first process that holds it, the lowest PID: root's [priv] from 9.8
                first, own = (pid - 8, "sshd-session: root [priv]") if comm == "sshd-session" else (pid, title)
                fields = [line.split("ESTABLISHED ", 1)[1] for line in out.splitlines() if "ESTABLISHED " in line]
                self.assertEqual(fields, [f"{f'{first}/{own}'[:19]:<20}"], out)
                self.assertNotIn("/sshd", out) if comm == "dropbear" else None

    def test_a_telnet_session_is_always_a_connection(self):
        """A telnet session is a connection to :23 that belongs to the session's server process
        (tpot_session: BusyBox telnetd where the persona has one, inetd's telnetd on Debian and Ubuntu,
        in.telnetd of telnet.socket on Red Hat), with the listener of the persona's daemon."""
        data, session, keys, _ = self.session_persona("ubuntu-noble", telnet=True)
        keys["telnet_server"] = "inetutils"
        row = self.estab(data, session, keys)
        self.assertEqual(len(row), 1, row)
        self.assertIn(":23 ", row[0])
        # ref ubuntu-24-04-session-ss-tanp: stderr, stdout and stdin of telnetd
        pid = session.tpot_session_pid
        self.assertIn(f'users:(("telnetd",pid={pid},fd=2),("telnetd",pid={pid},fd=1),("telnetd",pid={pid},fd=0))',
                      row[0])
        self.assertIn(":23 ", self.run_cmd(data, "ss", ["-tlnp"], keys, protocol=session)[0])
        out = self.run_cmd(data, "netstat", ["-tnp"], keys, protocol=session)[0]
        self.assertIn(f"ESTABLISHED {session.tpot_session_pid}/telnetd", out)
        for persona in catalog.PERSONAS:
            ident = draw(persona, 5)
            processes = persona.builder(persona, ident).processes
            daemon = [p for p in processes if re.search(r"(^|/)telnetd\b", p["COMMAND"])]
            if not daemon:
                continue
            data, session, keys, _ = self.session_persona(persona.id, telnet=True)
            with self.subTest(persona=persona.id):
                row = self.estab(data, session, keys)
                self.assertEqual(len(row), 1, row)
                self.assertIn(":23 ", row[0])
                self.assertIn(f'users:(("telnetd",pid={daemon[0]["PID"]},fd=4))', row[0])
                self.assertEqual(session.tpot_session_pid, daemon[0]["PID"])
                break
        else:
            self.fail("no persona with a telnet daemon")
        for persona in catalog.PERSONAS:
            data, session, keys, _ = self.session_persona(persona.id, telnet=True)
            with self.subTest(every=persona.id):
                self.assertEqual(len(self.estab(data, session, keys)), 1)

    def test_ss_shows_processes_to_root_only(self):
        data = container_network("ubuntu_24.04")

        class User(Protocol):
            class user:  # noqa: N801
                uid = 1000

        out = self.run_cmd(data, "ss", ["-tlnp"], protocol=User())[0]
        self.assertNotIn("users:", out)
        self.assertIn("users:", self.run_cmd(data, "ss", ["-tlnp"])[0])

    def test_changes_need_root(self):
        data = container_network("ubuntu_24.04")

        class User(Protocol):
            class user:  # noqa: N801
                uid = 1000

        self.assertEqual(self.run_cmd(data, "ip", ["link", "set", "eth0", "down"], protocol=User())[1:],
                         ("RTNETLINK answers: Operation not permitted\n", 2))
        self.assertEqual(self.run_cmd(data, "ip", ["link", "set", "eth0", "down"])[2], 0)
        self.assertEqual(self.run_cmd(data, "ifconfig", ["eth0", "up"], protocol=User())[1:],
                         ("SIOCSIFFLAGS: Operation not permitted\n", 255))

    def test_sudo_and_su_count_as_root(self):
        """The command sudo runs (pp.tpot_sudo) and the shell of su - (its user is root) may change
        interfaces and see every process, as root does; the same user without them may not."""
        data = container_network("ubuntu_24.04")

        class User(Protocol):
            class user:  # noqa: N801
                uid = 1000

        root = {"uid": 0, "username": "root"}
        for how in ({"sudo": True}, {"user": root}):
            with self.subTest(how=how):
                self.assertEqual(self.run_cmd(data, "ip", ["link", "set", "eth0", "down"], protocol=User(), **how),
                                 ("", "", 0))
                self.assertEqual(self.run_cmd(data, "ifconfig", ["eth0", "down"], protocol=User(), **how),
                                 ("", "", 0))
                self.assertIn("users:", self.run_cmd(data, "ss", ["-tlnp"], protocol=User(), **how)[0])
                out, err, _ = self.run_cmd(data, "netstat", ["-tlnp"], protocol=User(), **how)
                self.assertEqual(err, "")
                self.assertIn("/sshd", out)
        # su to another user than root takes root's rights away from a root session
        phil = {"uid": 1000, "username": "phil"}
        self.assertEqual(self.run_cmd(data, "ip", ["link", "set", "eth0", "down"], user=phil)[1:],
                         ("RTNETLINK answers: Operation not permitted\n", 2))
        self.assertNotIn("users:", self.run_cmd(data, "ss", ["-tlnp"], user=phil)[0])
        self.assertIn("Not all processes could be identified", self.run_cmd(data, "netstat", ["-tlnp"], user=phil)[1])
        self.assertEqual(self.run_cmd(data, "ifconfig", ["eth0", "down"], user=phil)[1:],
                         ("SIOCSIFFLAGS: Operation not permitted\n", 255))

    def test_ss_options_with_a_value(self):
        """-f/-A/-N/-F/-D take a value (attached, the next word, --long=value or --long value), as
        recorded with iproute2 6.1.0 (ubuntu:24.04) and ss170501 (centos:7)."""
        data = container_network("ubuntu_24.04")
        data["sockets"].append({"proto": "udp", "family": 4, "address": "0.0.0.0", "port": 5353, "pid": 9,
                                "comm": "avahi-daemon", "cmdline": "avahi-daemon", "fd": 12, "backlog": 0})
        listeners = self.run_cmd(data, "ss", ["-tlnp"])
        self.assertIn("0.0.0.0:22", listeners[0])
        v4 = self.run_cmd(data, "ss", ["-4tlnp"])
        self.assertNotIn("[::]:22", v4[0])
        for args in (["-f", "inet", "-tlnp"], ["-finet", "-tlnp"], ["--family=inet", "-tlnp"],
                     ["--family", "inet", "-tlnp"], ["--fam=inet", "-tlnp"], ["-tlnp", "-f", "inet"],
                     ["-tlnpf", "inet"]):
            with self.subTest(args=args):
                self.assertEqual(self.run_cmd(data, "ss", args), v4)
        v6 = self.run_cmd(data, "ss", ["-f", "inet6", "-tln"])[0]
        self.assertIn("[::]:22", v6)
        self.assertNotIn("0.0.0.0:22", v6)
        self.assertEqual(self.run_cmd(data, "ss", ["-A", "tcp", "-ln"]), self.run_cmd(data, "ss", ["-tln"]))
        for args in (["--query=tcp", "-ln"], ["--query", "tcp", "-ln"], ["--qu=tcp", "-ln"], ["-Atcp", "-ln"],
                     ["--socket=tcp", "-ln"]):
            with self.subTest(args=args):
                self.assertEqual(self.run_cmd(data, "ss", args), self.run_cmd(data, "ss", ["-tln"]))
        udp = self.run_cmd(data, "ss", ["-A", "udp", "-ln"])[0]
        self.assertTrue(udp.startswith("State ") and "0.0.0.0:5353" in udp and ":22" not in udp, udp)
        both = self.run_cmd(data, "ss", ["-A", "tcp,udp", "-ln"])[0]
        self.assertTrue(both.startswith("Netid ") and ":5353" in both and ":22" in both, both)
        # -f inet alone: every inet table (Netid); -f inet with -A udp: udp of IPv4 only
        self.assertTrue(self.run_cmd(data, "ss", ["-f", "inet", "-ln"])[0].startswith("Netid "))
        self.assertEqual(self.run_cmd(data, "ss", ["-f", "inet", "-A", "udp", "-ln"])[0], udp)
        # -f unix with -t: tcp and unix (Netid), -4 with -f inet6: nothing
        self.assertTrue(self.run_cmd(data, "ss", ["-f", "unix", "-tln"])[0].startswith("Netid "))
        # of -4, -6, -f inet and -f inet6 the last one counts
        self.assertEqual(self.run_cmd(data, "ss", ["-4", "-f", "inet6", "-tln"]), (v6, "", 0))
        self.assertEqual(self.run_cmd(data, "ss", ["-6", "-f", "inet", "-tlnp"]), v4)
        self.assertEqual(self.run_cmd(data, "ss", ["-f", "inet", "-6", "-tln"]), (v6, "", 0))
        helptext = self.run_cmd(data, "ss", ["-h"])[0]
        self.assertEqual(self.run_cmd(data, "ss", ["-f", "help"]), (helptext, "", 0))
        for args, message in ((["-f"], "ss: option requires an argument -- 'f'\n"),
                              (["-tln", "-N"], "ss: option requires an argument -- 'N'\n"),
                              (["--family"], "ss: option '--family' requires an argument\n"),
                              (["-f", "bogus", "-tln"], 'ss: "bogus" is invalid family\n'),
                              (["-fl"], 'ss: "l" is invalid family\n'),
                              (["-A", "bogus"], 'ss: "bogus" is illegal socket table id\n'),
                              (["-A", "help"], 'ss: "help" is illegal socket table id\n'),
                              (["-q"], "ss: invalid option -- 'q'\n"),
                              (["--foo"], "ss: unrecognized option '--foo'\n")):
            with self.subTest(args=args):
                self.assertEqual(self.run_cmd(data, "ss", args), ("", message + helptext, 255))
        self.assertEqual(self.run_cmd(data, "ss", ["-N", "foo", "-tln"]),
                         ("", 'Cannot open network namespace "foo": No such file or directory\n', 1))
        self.assertEqual(self.run_cmd(data, "ss", ["--net=foo", "-tln"]),
                         ("", 'Cannot open network namespace "foo": No such file or directory\n', 1))
        self.assertEqual(self.run_cmd(data, "ss", ["-F", "/nonexist", "-tln"]),
                         ("", "fopen filter file: No such file or directory\n", 255))
        self.assertEqual(self.run_cmd(data, "ss", ["--diag=/tmp/d", "-tln"]), ("", "", 0))

    def test_ip_help_prints_the_usage_of_the_release(self):
        """ip -help, -hel, -he, help and ip alone: iproute2's usage of the release on stderr, 255
        (fixtures/ip_help.json, recorded in containers); -h is -human-readable."""
        refs = json.loads((Path(__file__).parent / "fixtures" / "ip_help.json").read_text(encoding="utf-8"))
        for image, ref in refs["images"].items():
            data = container_network(image)
            data["tools"]["ip"]["version"] = ref["iproute2"]
            for args in (["-help"], ["-hel"], ["-he"], ["-h"], ["help"], [], ["-help", "a"], ["-human"]):
                with self.subTest(image=image, args=args):
                    self.assertEqual(self.run_cmd(data, "ip", args), ("", ref["stderr"], 255))
            with self.subTest(image=image, args="-h a"):
                self.assertEqual(self.run_cmd(data, "ip", ["-h", "a"]), self.run_cmd(data, "ip", ["a"]))

    def test_ip_route_get_takes_the_route_that_matches(self):
        """ip route get as iproute2 (6.1.0 and ss170501 alike) and BusyBox print it: the default route
        with its interface and source, a connected network without via, local and broadcast addresses,
        the caller's uid; on a router the default route of its WAN, as ip route and the session."""
        data = container_network("ubuntu_24.04")
        address, gateway = data["interfaces"][0]["inet"], data["gateway"]
        near = address.rsplit(".", 1)[0] + ".77"
        brd = data["interfaces"][0]["broadcast"]
        cases = {
            ("8.8.8.8",): f"8.8.8.8 via {gateway} dev eth0 src {address} uid 0 \n    cache \n",
            ("to", "8.8.8.8"): f"8.8.8.8 via {gateway} dev eth0 src {address} uid 0 \n    cache \n",
            ("8.8.8.8", "oif", "eth0"): f"8.8.8.8 via {gateway} dev eth0 src {address} uid 0 \n    cache \n",
            ("1.2.3",): f"1.2.3.0 via {gateway} dev eth0 src {address} uid 0 \n    cache \n",
            (gateway,): f"{gateway} dev eth0 src {address} uid 0 \n    cache \n",
            (near,): f"{near} dev eth0 src {address} uid 0 \n    cache \n",
            (address,): f"local {address} dev lo src {address} uid 0 \n    cache <local> \n",
            ("127.0.0.1",): "local 127.0.0.1 dev lo src 127.0.0.1 uid 0 \n    cache <local> \n",
            ("127.5.5.5",): "local 127.5.5.5 dev lo src 127.0.0.1 uid 0 \n    cache <local> \n",
            ("0.0.0.0",): "local 0.0.0.0 dev lo src 127.0.0.1 uid 0 \n    cache <local> \n",
            (brd,): f"broadcast {brd} dev eth0 src {address} uid 0 \n    cache <local,brd> \n",
            ("255.255.255.255",): f"broadcast 255.255.255.255 dev eth0 src {address} uid 0 \n    cache <local,brd> \n",
            ("8.8.8.8", "from", address): f"8.8.8.8 from {address} via {gateway} dev eth0 uid 0 \n    cache \n",
        }
        for words, text in cases.items():
            for verb in (["route", "get"], ["r", "g"]):
                with self.subTest(words=words, verb=verb):
                    self.assertEqual(self.run_cmd(data, "ip", verb + list(words)), (text, "", 0))
        errors = {(): ("need at least a destination address\n", 1),
                  ("foo",): ('Error: any valid prefix is expected rather than "foo".\n', 1),
                  ("2001:db8::1",): ("RTNETLINK answers: Network is unreachable\n", 2)}
        for words, (text, code) in errors.items():
            with self.subTest(words=words):
                self.assertEqual(self.run_cmd(data, "ip", ["route", "get", *words]), ("", text, code))
        self.assertEqual(self.run_cmd(data, "ip", ["-6", "route", "get", "8.8.8.8"]),
                         ("", 'Error: inet6 prefix is expected rather than "8.8.8.8".\n', 1))
        self.assertEqual(self.run_cmd(data, "ip", ["-4", "route", "get", "::1"]),
                         ("", 'Error: inet prefix is expected rather than "::1".\n', 1))
        # the caller's uid; under sudo root's
        phil = {"uid": 1000, "username": "phil"}
        self.assertIn(" uid 1000 \n", self.run_cmd(data, "ip", ["route", "get", "8.8.8.8"], user=phil)[0])
        self.assertIn(" uid 0 \n", self.run_cmd(data, "ip", ["route", "get", "8.8.8.8"], user=phil, sudo=True)[0])
        # BusyBox
        _, bb = busybox_network("busybox_1.36.1-musl")
        address, gateway = bb["interfaces"][0]["inet"], bb["gateway"]
        for words, text in {("8.8.8.8",): f"8.8.8.8 via {gateway} dev eth0  src {address} \n",
                            (gateway,): f"{gateway} dev eth0  src {address} \n",
                            (address,): f"local {address} dev lo  src {address} \n",
                            ("127.0.0.1",): "local 127.0.0.1 dev lo  src 127.0.0.1 \n",
                            ("172.17.255.255",): f"broadcast 172.17.255.255 dev eth0  src {address} \n",
                            ("8.8.8.8", "from", address): f"8.8.8.8 from {address} via {gateway} dev eth0 \n"}.items():
            with self.subTest(busybox=words):
                self.assertEqual(self.run_cmd(bb, "ip", ["route", "get", *words]), (text, "", 0))
        for words in ((), ("foo",)):
            with self.subTest(busybox=words):
                self.assertEqual(self.run_cmd(bb, "ip", ["route", "get", *words]),
                                 ("", "ip: need at least destination address\n", 1))
        # a router: the default route is the one of its WAN, as in ip route and the session's connection
        persona = catalog.by_id("openwrt")
        ident = draw(persona, 5)
        net = network.network(persona, ident, persona.builder(persona, ident).processes)
        wan = net["interfaces"][1]
        default = self.run_cmd(net, "ip", ["route"])[0].splitlines()[0]
        self.assertIn(f"dev {wan['name']}", default)
        out = self.run_cmd(net, "ip", ["route", "get", "8.8.8.8"])[0]
        self.assertTrue(out.startswith(f"8.8.8.8 via {net['gateway']} dev {wan['name']}  src {wan['inet']} "), out)
        lan = net["interfaces"][0]
        host = lan["inet"].rsplit(".", 1)[0] + ".77"
        self.assertTrue(self.run_cmd(net, "ip", ["route", "get", host])[0]
                        .startswith(f"{host} dev {lan['name']}  src {lan['inet']} "))

    def test_ip_4_shows_the_altname_in_text_and_json(self):
        """ip -4 a names the altname after the header, as ip -j -4 a has altnames (recorded with
        iproute2 5.15.0, 6.1.0 and 6.15.0 after `ip link property add dev eth0 altname enp0s5`);
        -o prints the address only."""
        data = container_network("ubuntu_24.04")
        data["interfaces"][0]["altname"] = "enp0s5"
        address = data["interfaces"][0]["inet"]
        for version in ("5.15.0", "6.1.0", "6.15.0"):
            data["tools"]["ip"]["version"] = version
            with self.subTest(version=version):
                lines = self.run_cmd(data, "ip", ["-4", "a", "show", "dev", "eth0"])[0].splitlines()
                self.assertEqual(lines[1:3], ["    altname enp0s5",
                                              f"    inet {address}/16 brd 172.17.255.255 scope global eth0"])
                found = json.loads(self.run_cmd(data, "ip", ["-j", "-4", "a", "show", "dev", "eth0"])[0])
                self.assertEqual(found[0]["altnames"], ["enp0s5"])
                self.assertNotIn("altname", self.run_cmd(data, "ip", ["-o", "-4", "a", "show", "dev", "eth0"])[0])
                self.assertEqual(self.run_cmd(data, "ip", ["a", "show", "dev", "eth0"])[0].splitlines()[2],
                                 "    altname enp0s5")
        data["tools"]["ip"]["version"] = "5.3.0"
        self.assertNotIn("altname", self.run_cmd(data, "ip", ["-4", "a"])[0])

    def test_ip_json_of_ipv6_addresses_has_no_qdisc_and_group(self):
        """ip -j -6 address as its text: no qdisc and no group (iproute2 5.15.0, 6.1.0, 6.15.0 and 6.17.0,
        refs t9.json ipj-*-addr6); -4 keeps both."""
        data = container_network("ubuntu_24.04")
        for iface in data["interfaces"]:
            iface.setdefault("inet6", "fe80::42:acff:fe11:2" if not iface.get("loopback") else "::1")
        six = json.loads(self.run_cmd(data, "ip", ["-j", "-6", "a"])[0])
        self.assertTrue(six)
        for entry in six:
            self.assertEqual(list(entry), ["ifindex", "ifname", "flags", "mtu", "operstate", "txqlen", "addr_info"])
        four = json.loads(self.run_cmd(data, "ip", ["-j", "-4", "a"])[0])
        self.assertEqual(list(four[0]), ["ifindex", "ifname", "flags", "mtu", "qdisc", "operstate", "group", "txqlen",
                                         "addr_info"])

    def test_ip_json_routes_in_the_layout_of_iproute2(self):
        """ip -j route, -j -p route, -j -6 route and -j route get as iproute2 writes them
        (fixtures/net_route_json.json, recorded with 6.1.0; 5.15.0 and 6.15.0 give the same)."""
        refs = json.loads((Path(__file__).parent / "fixtures" / "net_route_json.json").read_text(encoding="utf-8"))
        data = container_network("ubuntu_24.04")
        data["interfaces"][0].update(inet="172.17.0.2", broadcast="172.17.255.255", network="172.17.0.0")
        data["gateway"] = "172.17.0.1"
        for manager, case in (("device", "device"), ("networkd", "networkd"), ("static", "static")):
            data["manager"] = manager
            ref = refs["cases"][case]
            with self.subTest(manager=manager):
                self.assertEqual(self.run_cmd(data, "ip", ["r"]), (ref["ip r"], "", 0))
                self.assertEqual(self.run_cmd(data, "ip", ["-j", "r"]), (ref["ip -j r"], "", 0))
                self.assertEqual(self.run_cmd(data, "ip", ["-j", "-p", "r"]), (ref["ip -j -p r"], "", 0))
                self.assertEqual(self.run_cmd(data, "ip", ["-j", "-4", "route", "show"]), (ref["ip -j r"], "", 0))
        # NetworkManager before src on the default route: its default route as recorded
        data["manager"] = "nm-old"
        default = json.loads(self.run_cmd(data, "ip", ["-j", "r"])[0])[0]
        self.assertEqual(json.dumps([default], separators=(",", ":")) + "\n", refs["cases"]["nm-old-default"]["ip -j r"])
        # IPv6: the kernel routes of lo and of the link-local network, in the recorded form
        data["manager"] = "device"
        data["interfaces"][0]["inet6"] = "fe80::42:acff:fe11:2"
        recorded = json.loads(refs["cases"]["ipv6"]["ip -j -6 r"])
        link = next(r for r in recorded if r["dst"] == "fe80::/64")
        want = [dict(link, dst="::1", dev="lo"), link]
        self.assertEqual(self.run_cmd(data, "ip", ["-j", "-6", "r"]),
                         (json.dumps(want, separators=(",", ":")) + "\n", "", 0))
        pretty = self.run_cmd(data, "ip", ["-j", "-p", "-6", "r"])[0]
        self.assertEqual(json.loads(pretty), want)
        self.assertTrue(refs["cases"]["ipv6"]["ip -j -p -6 r"].startswith(pretty.split("},{")[0].replace('"::1"', '"fd00:dead:beef::/64"')
                                                                            .replace('"lo"', '"eth0"')))
        for target, ref in refs["cases"]["get"].items():
            with self.subTest(get=target):
                words = target.split()
                self.assertEqual(self.run_cmd(data, "ip", ["-j", "route", "get", *words]), (ref["compact"], "", 0))
                self.assertEqual(self.run_cmd(data, "ip", ["-j", "-p", "route", "get", *words]), (ref["pretty"], "", 0))


if __name__ == "__main__":
    unittest.main()
