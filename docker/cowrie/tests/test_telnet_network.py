"""The telnet daemon, the DHCP client and the SSH server of each server persona as packages, ps,
systemctl, ss and netstat show them (refs systemd-telnet and network-dhcp of round 4: openbsd-inetd and
inetutils-telnetd on ubuntu:22.04/24.04, debian:12/13, telnet-server with telnet.socket on almalinux:9,
dhclient on centos:7, the internal client of NetworkManager, sshd and dropbear sessions)."""
import json
import re
import tempfile
import unittest
from pathlib import Path

from tests.test_net_tools import Base, Log, Protocol, patched_source
from tests.test_patch import FakeConfig, module_all
from tpot_personas import catalog, network, servers, system
from tpot_personas.identity import draw

DEBIAN_FAMILY = ("ubuntu-noble", "ubuntu-jammy", "debian-trixie", "debian-bookworm", "raspios", "gpu-ubuntu")


def persona_parts(pid, seed=5):
    persona = catalog.by_id(pid)
    ident = draw(persona, seed)
    content = persona.builder(persona, ident)
    return persona, ident, content


def session_for(processes, telnet, user="root", uid=0):
    name = "HoneyPotInteractiveTelnetProtocol" if telnet else "HoneyPotInteractiveProtocol"

    class Server:
        process = processes

    session = type(name, (Protocol,), {"clientIP": "203.0.113.7", "realClientPort": 51234})()
    session.user = type("user", (), {"uid": uid, "server": Server})
    return session


class TelnetNetworkTest(unittest.TestCase):
    def run_cmd(self, data, name, args, keys=None, protocol=None, user=None):
        path = Path(tempfile.mkdtemp()) / "network.json"
        path.write_text(json.dumps(data))
        values = {("shell", "network"): str(path), **{("shell", k): v for k, v in (keys or {}).items()}}
        namespace = module_all(patched_source(), {"HoneyPotCommand": Base, "CowrieConfig": FakeConfig(values),
                                                  "log": Log, "commands": {}})
        classes = {"ss": "Command_tpot_ss", "netstat": "Command_tpot_netstat", "ip": "Command_tpot_ip"}
        cmd = namespace[classes[name]](args, protocol, user)
        cmd.start()
        return cmd.out, cmd.err, cmd.exit_code

    def keys(self, persona, ident):
        keys = {"ssh_version": ident.release.ssh_version}
        if network.telnet_server(persona, ident):
            keys["telnet_server"] = network.telnet_server(persona, ident)
        return keys

    def test_debian_and_ubuntu_run_inetd_with_telnetd(self):
        for pid in DEBIAN_FAMILY:
            persona, ident, content = persona_parts(pid)
            with self.subTest(pid):
                installed = system.apt_packages(persona, ident)["installed"]
                for package in ("inetutils-telnetd", "openbsd-inetd", "tcpd", "update-inetd"):
                    self.assertIn(package, installed)
                inetd = [p for p in content.processes if p["COMMAND"] == "/usr/sbin/inetd"]
                self.assertEqual(len(inetd), 1)
                self.assertIn("telnet\tstream\ttcp\tnowait\troot\t/usr/sbin/tcpd\t/usr/sbin/telnetd\n",
                              content.files["etc/inetd.conf"])
                units = {u["name"]: u for u in servers.systemd_units(persona, ident, content.processes)["units"]}
                self.assertEqual((units["inetd.service"]["sub"], units["inetd.service"]["pid"]),
                                 ("running", inetd[0]["PID"]))
                self.assertEqual(units["inetd.service"]["docs"], ["man:inetd(8)"])
                data = network.network(persona, ident, content.processes)
                listen = [(s["address"], s["fd"]) for s in data["sockets"] if s["port"] == 23]
                # 0.20160825 of 22.04 listens on IPv4 only (ref ubuntu-22-04-ss-tlnp-enabled)
                self.assertEqual(listen, [("0.0.0.0", 7)] if pid == "ubuntu-jammy" else [("0.0.0.0", 7), ("::", 8)])
                # a program of its package (TOOL_PROGRAMS: dpkg -L and -S of track A's package DB)
                self.assertIn("usr/sbin/telnetd", system.tool_programs(persona, ident)[0])
        self.assertEqual(len(servers.inetd_conf(draw(catalog.by_id("ubuntu-jammy"), 1)).encode()), 1119)
        self.assertEqual(len(servers.inetd_conf(draw(catalog.by_id("debian-bookworm"), 1)).encode()), 1200)

    def test_the_packages_of_the_daemons(self):
        # track A's package DB reads TOOL_PROGRAMS, APT_INSTALLED, CENTOS_BASE and RPM_OWNED
        for package, program in (("inetutils-telnetd", "usr/sbin/telnetd"), ("openbsd-inetd", "usr/sbin/inetd"),
                                 ("tcpd", "usr/sbin/tcpd"), ("update-inetd", "usr/sbin/update-inetd")):
            self.assertIn(program, system.TOOL_PROGRAMS[package].values())
        for pid in ("debian-bookworm", "debian-trixie", "raspios", "ubuntu-noble", "ubuntu-jammy"):
            persona, ident, content = persona_parts(pid)
            installed = system.apt_packages(persona, ident)["installed"]
            self.assertIn("systemd-timesyncd", installed, pid)
            self.assertIn("usr/sbin/telnetd", system.tool_programs(persona, ident)[0], pid)
        self.assertEqual(system.CENTOS_BASE["dhclient"], "12:4.2.5-83.el7.centos.2")
        self.assertIn("dhclient", system.RPM_OWNED["dhclient"][1].split())
        persona, ident, content = persona_parts("debian-bookworm")
        # the init script of openbsd-inetd and service --status-all
        self.assertEqual(len(content.files["etc/init.d/openbsd-inetd"].encode()), 2444)
        self.assertIn(" [ + ]  openbsd-inetd\n", system.service_status(persona))
        self.assertIn(("etc/rc2.d/S01openbsd-inetd", "etc/init.d/openbsd-inetd"), system.init_links(persona))

    def test_the_dhcp_server_is_the_gateway(self):
        persona, ident, content = persona_parts("rhel-9")
        data = network.network(persona, ident, content.processes)
        row = next(s for s in data["sockets"] if s["port"] == 68)
        self.assertEqual(row["peer"], data["gateway"])

    def test_red_hat_listens_with_telnet_socket(self):
        for pid in ("rhel-9", "centos-7"):
            persona, ident, content = persona_parts(pid)
            with self.subTest(pid):
                units = {u["name"]: u for u in servers.systemd_units(persona, ident, content.processes)["units"]}
                socket = units["telnet.socket"]
                self.assertEqual((socket["state"], socket["preset"], socket["listen"], socket["accept"]),
                                 ("enabled", "disabled", ["[::]:23 (Stream)"], True))
                self.assertEqual(socket["docs"], ["man:telnetd(8)"])
                data = network.network(persona, ident, content.processes)
                row = next(s for s in data["sockets"] if s["port"] == 23)
                self.assertEqual((row["address"], row["pid"], row["comm"]), ("*", 1, "systemd"))
                self.assertIn("usr/sbin/in.telnetd", system.programs(persona, ident))
        self.assertIn("telnet-server", system.RPM_INSTALLED)
        self.assertEqual(system.CENTOS_BASE["telnet-server"], "1:0.17-66.el7")
        persona, ident, content = persona_parts("rhel-9")
        data = network.network(persona, ident, content.processes)
        out = self.run_cmd(data, "ss", ["-tlnp"])[0]
        # ref almalinux-9-ss-tlnp
        self.assertRegex(out, r'\nLISTEN 0      4096 +\*:23 +\*:\*    users:\(\("systemd",pid=1,fd=32\)\) *\n')

    def test_the_session_of_a_telnet_login(self):
        cases = {"debian-bookworm": ("telnetd", "/bin/login -p -h 203.0.113.7", "172", False),
                 "debian-trixie": ("telnetd", "login -- root", "", False),
                 "rhel-9": ("in.telnetd: ::ffff:203.0.113.7", "login -- root", "", True)}
        for pid, (first, login, _, systemd) in cases.items():
            persona, ident, content = persona_parts(pid)
            data = network.network(persona, ident, content.processes)
            session = session_for(content.processes, telnet=True)
            keys = self.keys(persona, ident)
            with self.subTest(pid):
                out = self.run_cmd(data, "ss", ["-tanp"], keys, protocol=session)[0]
                row = next(line for line in out.splitlines() if line.startswith("ESTAB"))
                spid = session.tpot_session_pid
                name = first.split(":")[0]
                owners = f'("{name}",pid={spid},fd=2),("{name}",pid={spid},fd=1),("{name}",pid={spid},fd=0)'
                self.assertIn(f"users:({owners}" + (',("systemd",pid=1,fd=23))' if systemd else ")"), row)
                if systemd:
                    # ref almalinux-9-session-ss-tnp: both ends as IPv4 in IPv6
                    self.assertRegex(row, r"\[::ffff:[\d.]+\]:23 +\[::ffff:203\.0\.113\.7\]:51234 ")
                else:
                    self.assertIn(" users:((", row)
                    self.assertTrue(any(":23 " in line and line.startswith("LISTEN") and '"inetd"' in line
                                        for line in out.splitlines()), out)
                namespace = module_all(patched_source(), {"HoneyPotCommand": Base, "CowrieConfig": FakeConfig(
                    {("shell", k): v for k, v in keys.items()}), "log": Log, "commands": {}})
                chain = namespace["tpot_session"](session_for(content.processes, telnet=True))["chain"]
                self.assertEqual([entry["command"] for entry in chain], [first, login])

    def test_dhcp_clients(self):
        # dhclient of CentOS 7 listens on *:68 (ref dhclient-c7-ifup-ss-ulnp)
        persona, ident, content = persona_parts("centos-7")
        dhclient = [p for p in content.processes if p["COMMAND"].startswith("/sbin/dhclient ")]
        self.assertEqual(len(dhclient), 1)
        data = network.network(persona, ident, content.processes)
        out = self.run_cmd(data, "ss", ["-ulnp"])[0]
        self.assertRegex(out, rf'\nUNCONN +0 +0 +\*:68 +\*:\* +users:\(\("dhclient",pid={dhclient[0]["PID"]},fd=5\)\)')
        self.assertIn(f"udp        0      0 0.0.0.0:68              0.0.0.0:*                           "
                      f"{dhclient[0]['PID']}/dhclient", self.run_cmd(data, "netstat", ["-ulnp"])[0])
        # NetworkManager's own client: no listener, a connected socket (refs nm-al9-after11s-ss-ulnp, -ss-uanp)
        persona, ident, content = persona_parts("rhel-9")
        data = network.network(persona, ident, content.processes)
        self.assertNotIn(":68", self.run_cmd(data, "ss", ["-ulnp"])[0])
        main = data["interfaces"][0]
        out = self.run_cmd(data, "ss", ["-uanp"])[0]
        self.assertRegex(out, rf'\nESTAB +0 +0 +{re.escape(main["inet"])}%{main["name"]}:68 +[\d.]+:67 +'
                              r'users:\(\("NetworkManager",pid=\d+,fd=26\)\)')
        self.assertNotIn(":68", self.run_cmd(data, "netstat", ["-ulnp"])[0] if "netstat" in data["tools"] else "")
        # BusyBox udhcpc of OpenWrt has a packet socket only
        persona, ident, content = persona_parts("openwrt")
        self.assertTrue(any(p["COMMAND"].startswith("udhcpc ") for p in content.processes))
        data = network.network(persona, ident, content.processes)
        self.assertFalse([s for s in data["sockets"] if s["port"] == 68])

    def test_socket_activated_ssh_of_noble(self):
        # ref ubuntu-24-04-ss-tlnp-enabled: systemd holds ssh.socket's sockets with sshd
        persona, ident, content = persona_parts("ubuntu-noble")
        data = network.network(persona, ident, content.processes)
        out = self.run_cmd(data, "ss", ["-tlnp"])[0]
        sshd = next(p["PID"] for p in content.processes if p["COMMAND"].startswith("sshd:"))
        self.assertIn(f'0.0.0.0:22        0.0.0.0:*    users:(("sshd",pid={sshd},fd=3),("systemd",pid=1,fd=80))', out)
        self.assertRegex(out, r"\nLISTEN 0      4096 +0\.0\.0\.0:22 ")
        data["tools"]["netstat"] = {"style": "net-tools", "version": "2.10", "si": True}
        # net-tools names the process it finds first: PID 1 (/sbin/init)
        self.assertRegex(self.run_cmd(data, "netstat", ["-tlnp"])[0], r"\ntcp +0 +0 0\.0\.0\.0:22 .* 1/init +\n")

    def test_ssh_session_of_a_user_belongs_to_each_sshd(self):
        # refs network-dhcp sshd-*-session-tester-ss-tanp-grep22: both sshd processes, the later first
        persona, ident, content = persona_parts("debian-bookworm")
        data = network.network(persona, ident, content.processes)
        namespace = module_all(patched_source(), {"HoneyPotCommand": Base, "CowrieConfig": FakeConfig({}),
                                                  "log": Log, "commands": {}})
        session = session_for(content.processes, telnet=False)
        real = namespace["tpot_session"]

        def two(protocol, user="root"):
            # a user other than root: its [priv] monitor below the session's sshd (tpot_session's chain,
            # 4 PIDs apart for an OpenSSH that names no distribution)
            return real(protocol, "tester")
        path = Path(tempfile.mkdtemp()) / "network.json"
        path.write_text(json.dumps(data))
        namespace = module_all(patched_source(), {"HoneyPotCommand": Base, "CowrieConfig": FakeConfig(
            {("shell", "network"): str(path)}), "log": Log, "commands": {}})
        namespace["tpot_session"] = two
        cmd = namespace["Command_tpot_ss"](["-tnp"], session, None)
        cmd.start()
        row = next(line for line in cmd.out.splitlines() if line.startswith("ESTAB"))
        spid = session.tpot_session_pid
        self.assertIn(f'users:(("sshd",pid={spid},fd=4),("sshd",pid={spid - 4},fd=4))', row)


    def test_ip_route_get_of_multicast_and_loopback(self):
        # refs network-dhcp route-*-ip_route_get_224.0.0.1 and _::1 (without the VM's rt_offload_failed)
        persona, ident, content = persona_parts("debian-bookworm")
        data = network.network(persona, ident, content.processes)
        main = data["interfaces"][0]
        self.assertEqual(self.run_cmd(data, "ip", ["route", "get", "224.0.0.1"]),
                         (f"multicast 224.0.0.1 dev {main['name']} src {main['inet']} uid 0 \n    cache <local,mc> \n",
                          "", 0))
        for args in (["route", "get", "::1"], ["-6", "route", "get", "::1"]):
            self.assertEqual(self.run_cmd(data, "ip", args),
                             ("local ::1 from :: dev lo table local proto kernel src ::1 metric 0 pref medium\n", "", 0))
        self.assertEqual(self.run_cmd(data, "ip", ["route", "get", "127.0.0.1"]),
                         ("local 127.0.0.1 dev lo src 127.0.0.1 uid 0 \n    cache <local> \n", "", 0))

    def test_ss_dump_goes_to_a_file(self):
        # refs network-dhcp ssD-*: nothing on the terminal, 40 bytes without a TCP socket
        persona, ident, content = persona_parts("debian-bookworm")
        data = network.network(persona, ident, content.processes)

        class Fs:
            made = {}

            def resolve_path(self, path, cwd):
                return path if path.startswith("/") else f"{cwd}/{path}"

            def isdir(self, path):
                return path in ("/", "/tmp")

            def mkfile(self, path, uid, gid, size, mode):
                self.made[path] = ["x", 2, uid, gid, size, mode, 0, [], None, None]
                return True

            def exists(self, path):
                return path in self.made or self.isdir(path)

            def getfile(self, path):
                dirs = {"/": ["", 1, 0, 0, 4096, 0o40755], "/tmp": ["tmp", 1, 0, 0, 4096, 0o41777]}
                return dirs[path] if path in dirs else self.made[path]

        path = Path(tempfile.mkdtemp()) / "network.json"
        path.write_text(json.dumps(data))
        namespace = module_all(patched_source(), {"HoneyPotCommand": Base, "CowrieConfig": FakeConfig(
            {("shell", "network"): str(path)}), "log": Log, "commands": {}})
        protocol = Protocol()
        protocol.cwd = "/root"
        fs = Fs()
        for args, size in ((["-D", "/tmp/x", "-tln"], 40 + 116 * 4), (["-D", "/tmp/y", "-tn"], 40)):
            cmd = namespace["Command_tpot_ss"](args, protocol, None)
            cmd.fs = fs
            cmd.start()
            self.assertEqual((cmd.out, cmd.err, cmd.exit_code), ("", "", 0))
            self.assertEqual(len(fs.made[args[1]][7]), size)
        # one dump of the family asked for
        cmd = namespace["Command_tpot_ss"](["-D", "/tmp/z", "-4tln"], protocol, None)
        cmd.fs = fs
        cmd.start()
        self.assertEqual(len(fs.made["/tmp/z"][7]), 20 + 116 * 2)
        cmd = namespace["Command_tpot_ss"](["-D", "/nosuch/x"], protocol, None)
        cmd.fs = fs
        cmd.start()
        self.assertEqual((cmd.err, cmd.exit_code), ("fopen dump file: No such file or directory\n", 255))
        # a user writes only where the directory lets them
        user = Base(["-D", "/x", "-tln"], protocol, {"uid": 1000, "gid": 1000, "username": "eve"})
        cmd = namespace["Command_tpot_ss"](["-D", "/x", "-tln"], protocol, {"uid": 1000, "gid": 1000})
        cmd.fs = fs
        cmd.start()
        self.assertEqual((cmd.err, cmd.exit_code), ("fopen dump file: Permission denied\n", 255))
        cmd = namespace["Command_tpot_ss"](["-D", "/tmp/u", "-tln"], protocol, {"uid": 1000, "gid": 1000})
        cmd.fs = fs
        cmd.start()
        self.assertEqual((cmd.err, cmd.exit_code), ("", 0))
        del user
        err, code = self.run_cmd(data, "ss", ["-D"])[1:]
        self.assertTrue(err.startswith("ss: option requires an argument -- 'D'\nUsage: ss [ OPTIONS ]\n"), err)
        self.assertEqual(code, 255)


if __name__ == "__main__":
    unittest.main()
