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


class Base:
    def __init__(self, args, protocol=None):
        self.args = list(args)
        self.protocol = protocol or Protocol()
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
    def run_cmd(self, data, name, args, keys=None, protocol=None):
        path = Path(tempfile.mkdtemp()) / "network.json"
        path.write_text(json.dumps(data))
        values = {("shell", "network"): str(path), **{("shell", k): v for k, v in (keys or {}).items()}}
        namespace = module_all(patched_source(), {"HoneyPotCommand": Base, "CowrieConfig": FakeConfig(values),
                                                  "log": Log, "commands": {}})
        classes = {"ip": "Command_tpot_ip", "ss": "Command_tpot_ss", "netstat": "Command_tpot_netstat",
                   "ifconfig": "Command_tpot_ifconfig"}
        cmd = namespace[classes[name]](args, protocol)
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
        self.assertIn(f'users:(("sshd",pid={session.tpot_session_pid},fd=4))', row)
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
        for persona_id, ssh_version, comm, title in (
                ("openwrt", None, "dropbear", "dropbear"),
                ("ubuntu-noble", "OpenSSH_9.6p1 Ubuntu-3ubuntu13.14, OpenSSL 3.0.13 30 Jan 2024", "sshd",
                 "sshd: root@pts/0"),
                ("debian-trixie", "OpenSSH_10.0p2 Debian-7, OpenSSL 3.5.1 1 Jul 2025", "sshd-session",
                 "sshd-session: root@pts/0")):
            data, session, keys, processes = self.session_persona(persona_id, ssh_version=ssh_version)
            with self.subTest(persona=persona_id):
                row = self.estab(data, session, keys)
                self.assertEqual(len(row), 1, row)
                self.assertIn(f'users:(("{comm}",pid={session.tpot_session_pid},fd=4))', row[0])
                shell = [p["PID"] for p in processes if str(p["TTY"]).startswith("pts/")]
                if shell:
                    self.assertEqual(session.tpot_session_pid, shell[0] - 1)
                data["tools"]["netstat"] = {"style": "net-tools", "version": "2.10", "basename": False}
                out = self.run_cmd(data, "netstat", ["-tnp"], keys, protocol=session)[0]
                # net-tools 2.10: the title, PID/title cut to 19 characters
                self.assertIn(f"ESTABLISHED {session.tpot_session_pid}/{title}"[:31], out)
                self.assertNotIn("/sshd", out) if comm == "dropbear" else None

    def test_a_telnet_session_needs_a_telnet_daemon(self):
        """A telnet session is a connection to :23 only where the persona has a telnet daemon (then
        BusyBox telnetd owns it); elsewhere nothing listens on 23 and ss shows no connection."""
        data, session, keys, _ = self.session_persona("ubuntu-noble", telnet=True)
        self.assertEqual(self.estab(data, session, keys), [])
        self.assertNotIn(":23 ", self.run_cmd(data, "ss", ["-tlnp"], keys, protocol=session)[0])
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


if __name__ == "__main__":
    unittest.main()
