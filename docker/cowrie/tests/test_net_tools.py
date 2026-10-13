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


def container_network(image):
    """The network of the reference container: eth0 with its address, sshd listening on 22."""
    cases = REFS["images"][image]["cases"]
    pid, fd = (int(x) for x in re.search(r'pid=(\d+),fd=(\d+)', cases["ss -tlnp"][0]).groups())
    address = re.search(r"src (\S+)", cases["ip r"][0]).group(1)
    version, narrow, basename, si = NET_TOOLS_STYLE[image]
    net = ipaddress.IPv4Interface(f"{address}/16").network
    proc = "/usr/sbin/sshd -D" if image == "centos_7" else "sshd: /usr/sbin/sshd -D [listener] 0 of 10-100 startups"
    sshd = {"pid": pid, "comm": "sshd", "cmdline": proc, "backlog": 128, "proto": "tcp", "port": 22}
    libbpf = (re.findall(r"libbpf (\S+)", cases["ip -V"][0]) or [""])[0]
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
                if case == "netstat -V":
                    # the name list without the name the persona's texts leave out (test_patch)
                    out = out.replace("Phil Blundell, ", "")
                with self.subTest(image=image, case=case):
                    self.assertEqual(self.run_cmd(data, words[0], words[1:]), (out, err, code))

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
        self.assertEqual(out.splitlines()[1].split(), ["ESTAB", "0", "36", "172.17.0.3:22", "203.0.113.7:51234"])
        out = self.run_cmd(data, "netstat", ["-antp"], protocol=Session())[0]
        self.assertIn("tcp        0     36 172.17.0.3:22           203.0.113.7:51234       ESTABLISHED ", out)
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
