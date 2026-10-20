"""What the package state, the units and the processes of a persona have to agree on (round 4, Task 12):
the accounts of the processes, their parents, the owners of their programs, one CPU time per process, the
package <-> service link of apt, dpkg, yum, dnf and rpm (refs t12: Debian 12, Ubuntu 24.04 and AlmaLinux 9
with systemd, CentOS 7)."""
import json
import re
import tempfile
import unittest
from pathlib import Path

from tests.helpers import DIST  # noqa: F401 - puts the persona package on the path
from tests.test_packages import SessionCase
from tpot_personas import catalog, system, validate
from tpot_personas.identity import draw

SERVERS = ("ubuntu-noble", "ubuntu-jammy", "debian-trixie", "debian-bookworm", "rhel-9", "centos-7", "raspios",
           "gpu-ubuntu")


def built(persona_id, seed=0):
    persona = catalog.by_id(persona_id)
    ident = draw(persona, seed)
    return persona, ident, persona.builder(persona, ident)


def table(text):
    return {line.split(":")[0]: line.split(":") for line in text.splitlines() if line}


class AccountTest(unittest.TestCase):
    """Every user of ps has its account (passwd, group, shadow), so ps prints its uid and -u finds it
    (refs t12 *-own-accounts: the accounts systemd-timesyncd and rsyslog make)."""

    def test_every_process_has_an_account(self):
        for persona in catalog.PERSONAS:
            persona, ident, content = built(persona.id)
            passwd = table(content.files["etc/passwd"])
            with self.subTest(persona=persona.id):
                for entry in content.processes:
                    self.assertIn(entry["USERNAME"], passwd, entry["COMMAND"])
                if persona.kind != "server":
                    continue
                groups, shadow = table(content.files["etc/group"]), table(content.files["etc/shadow"])
                self.assertEqual(sorted(passwd), sorted(shadow))
                uids = [fields[2] for fields in passwd.values()]
                self.assertEqual(len(uids), len(set(uids)), "a uid twice")
                gids = [fields[2] for fields in groups.values()]
                self.assertEqual(len(gids), len(set(gids)), "a gid twice")

    def test_the_accounts_of_the_time_client_and_the_logger(self):
        # (name, uid, gid, gecos, home, shadow password and the fields after the change day)
        recorded = {
            "ubuntu-noble": [("systemd-timesync", "996", "996", "systemd Time Synchronization", "/", "!*:", "::::::"),
                             ("syslog", "102", "104", "", "/nonexistent", "!:", "::::::")],
            "ubuntu-jammy": [("systemd-timesync", "104", "106", "systemd Time Synchronization,,,", "/run/systemd",
                              "*:", ":0:99999:7:::"), ("syslog", "105", "111", "", "/home/syslog", "*:", ":0:99999:7:::")],
            "debian-trixie": [("systemd-timesync", "996", "996", "systemd Time Synchronization", "/", "!*:", ":::::1:")],
            "debian-bookworm": [("systemd-timesync", "996", "996", "systemd Time Synchronization", "/", "!*:",
                                 "::::::")],
            # (below pi-gen's groups 997 to 999 and systemd-network 994)
            "raspios": [("systemd-timesync", "995", "995", "systemd Time Synchronization", "/", "!*:", "::::::")],
        }
        recorded["gpu-ubuntu"] = recorded["ubuntu-noble"]
        for persona_id, accounts in recorded.items():
            persona, ident, content = built(persona_id)
            passwd, groups = table(content.files["etc/passwd"]), table(content.files["etc/group"])
            shadow = {line.split(":", 1)[0]: line for line in content.files["etc/shadow"].splitlines()}
            for name, uid, gid, gecos, home, password, rest in accounts:
                with self.subTest(persona=persona_id, account=name):
                    self.assertEqual(passwd[name], [name, "x", uid, gid, gecos, home, "/usr/sbin/nologin"])
                    self.assertEqual(groups[name][:3], [name, "x", gid])
                    self.assertRegex(shadow[name], rf"^{name}:{password.replace('*', '[*]')}\d+{rest}$")
        # Ubuntu's rsyslog runs as syslog, Debian's systems have no rsyslogd and no syslog account
        self.assertNotIn("syslog", table(built("debian-bookworm")[2].files["etc/passwd"]))

    def test_the_validator_names_a_process_without_an_account(self):
        persona, ident, content = built("ubuntu-noble")
        with tempfile.TemporaryDirectory() as handle:
            out_dir = Path(handle)
            (out_dir / "honeyfs/etc").mkdir(parents=True)
            (out_dir / "cmdoutput.json").write_text(json.dumps({"command": {"ps": content.processes}}))
            passwd = "".join(line + "\n" for line in content.files["etc/passwd"].splitlines()
                             if not line.startswith("syslog:"))
            (out_dir / "honeyfs/etc/passwd").write_text(passwd)
            found = []
            validate._check_accounts(persona, out_dir, found)
            self.assertEqual(found, ["accounts: syslog of '/usr/sbin/rsyslogd -n -iNONE' has no line in /etc/passwd"])
            (out_dir / "honeyfs/etc/passwd").write_text(content.files["etc/passwd"])
            found = []
            validate._check_accounts(persona, out_dir, found)
            self.assertEqual(found, [])


# the daemons that fork their workers (child command -> parent command), as the recordings show them: postfix's
#   master with pickup and qmgr, NetworkManager's dhclient (CentOS 7), dbus-broker-launch with dbus-broker (ref
#   almalinux-9-t12-enable-now-mariadb-server), avahi-daemon with its chroot helper, OpenWrt's netifd with udhcpc
FORKED = {"centos-7": {"/sbin/dhclient ": "/usr/sbin/NetworkManager ", "pickup ": "/usr/libexec/postfix/master ",
                       "qmgr ": "/usr/libexec/postfix/master "},
          "rhel-9": {"dbus-broker --log": "/usr/bin/dbus-broker-launch "},
          "raspios": {"avahi-daemon: chroot helper": "avahi-daemon: running"},
          "openwrt": {"udhcpc ": "/sbin/netifd"}}


class ParentTest(unittest.TestCase):
    """The parent of each process: a forked daemon is the child of its daemon, everything else of init."""

    def test_forked_daemons_have_their_parent(self):
        for persona in catalog.PERSONAS:
            persona, ident, content = built(persona.id)
            by_command = {entry["COMMAND"]: entry for entry in content.processes}
            forked = FORKED.get(persona.id, {})
            with self.subTest(persona=persona.id):
                for child, parent in forked.items():
                    entry = next(e for e in content.processes if e["COMMAND"].startswith(child))
                    owner = next(e for e in content.processes if e["COMMAND"].startswith(parent))
                    self.assertEqual(entry["PPID"], owner["PID"], child)
                    self.assertGreater(entry["PID"], owner["PID"])
                others = [e for e in by_command.values() if not any(e["COMMAND"].startswith(c) for c in forked)]
                self.assertEqual({e.get("PPID", 1) for e in others}, {1}, persona.id)
                self.assertNotIn("PARENT", json.dumps(content.processes))
        # postfix's master runs pickup and qmgr, both as postfix
        names = [(e["USERNAME"], e["COMMAND"]) for e in built("centos-7")[2].processes]
        self.assertIn(("postfix", "pickup -l -t unix -u"), names)
        self.assertIn(("postfix", "qmgr -l -t unix -u"), names)

    def test_dbus_broker_names_the_machine_id(self):
        persona, ident, content = built("rhel-9")
        broker = next(e for e in content.processes if e["COMMAND"].startswith("dbus-broker --log"))
        machine = content.files["etc/machine-id"]
        self.assertRegex(machine, r"^[0-9a-f]{32}\n$")
        self.assertEqual(broker["COMMAND"], f"dbus-broker --log 4 --controller 9 --machine-id {machine.strip()} "
                                            "--max-bytes 536870912 --max-fds 4096 --max-matches 16384 --audit")
        self.assertEqual(broker["USERNAME"], "dbus")
        for persona_id in SERVERS:
            self.assertRegex(built(persona_id)[2].files["etc/machine-id"], r"^[0-9a-f]{32}\n$", persona_id)

    def test_the_unit_holds_its_children(self):
        from tpot_personas import servers
        cases = {"centos-7": {"NetworkManager.service": ["/sbin/dhclient "],
                              "postfix.service": ["pickup ", "qmgr "]},
                 "rhel-9": {"dbus-broker.service": ["dbus-broker --log"]},
                 "raspios": {"avahi-daemon.service": ["avahi-daemon: chroot helper"]}}
        for persona_id, units in cases.items():
            persona, ident, content = built(persona_id)
            data = servers.systemd_units(persona, ident, content.processes)
            for name, children in units.items():
                unit = next(u for u in data["units"] if u["name"] == name)
                with self.subTest(persona=persona_id, unit=name):
                    expected = [(e["PID"], e["COMMAND"]) for prefix in children for e in content.processes
                                if e["COMMAND"].startswith(prefix)]
                    self.assertEqual(sorted(tuple(c) for c in unit["children"]), sorted(expected))
                    self.assertGreaterEqual(unit["tasks"], 1 + len(children))

    def test_ps_and_top_show_the_parent(self):
        from tests.test_ps import PS
        from tests.test_top import PatchedTest, Session

        class Patched(PatchedTest):
            def runTest(self):
                pass

        case = Patched()
        Patched.setUpClass()
        self.addCleanup(Patched.tearDownClass) if hasattr(Patched, "tearDownClass") else None
        manager = dict(PS[1], PID=700, COMMAND="/usr/sbin/NetworkManager --no-daemon")
        child = dict(PS[1], PID=702, PPID=700, COMMAND="/sbin/dhclient -d -q eth0")
        session = Session(case, processes=PS[:2] + [manager, child] + PS[2:], target="capture")
        rows = {int(line.split()[1]): int(line.split()[2]) for line in
                session.run("Command_tpot_ps", "-ef").out.splitlines()[1:]}
        self.assertEqual((rows[702], rows[700], rows[1]), (700, 1, 0))
        busybox = Session(case, processes=PS[:2] + [manager, child] + PS[2:], target="capture",
                          config={("shell", "top_style"): "busybox"})
        line = next(line for line in busybox.run("Command_top", "-bn1").out.splitlines() if "dhclient" in line)
        self.assertEqual(line.split()[:2], ["702", "700"])


class CpuTest(unittest.TestCase):
    """One CPU time per process: systemctl's CPU of a running unit is that of its CGroup, its main process and
    the processes it forked as ps and top count them, and the helpers it ran (never below its main process)."""

    def test_systemctl_counts_the_cpu_ps_shows(self):
        import re
        import time
        from tests.test_patch import FakeCommand, module_all
        from tests.test_systemctl import Session, patched_service
        run = Session("rhel-9")
        now = time.time()
        base = module_all(patched_service(), {"HoneyPotCommand": FakeCommand})
        units = {u["name"]: u for u in json.loads((run.out / "systemd-units.json").read_text())["units"]}
        by_pid = {p["PID"]: p for p in run.processes}
        for name in ("systemd-journald.service", "dbus-broker.service", "NetworkManager.service", "sshd.service",
                     "tuned.service"):
            unit = units[name]
            out, _, code = run("status", name, "--no-pager")
            with self.subTest(unit=name):
                self.assertEqual(code, 0, out)
                text = re.search(r"\n +CPU: (.+)\n", out).group(1)
                parts = re.fullmatch(r"(?:(\d+)h ?)?(?:(\d+)min ?)?(?:([\d.]+)s)?|(\d+)ms", text)
                self.assertIsNotNone(parts, text)
                shown = (int(parts.group(4)) / 1000 if parts.group(4) else
                         int(parts.group(1) or 0) * 3600 + int(parts.group(2) or 0) * 60 + float(parts.group(3) or 0))
                pids = [unit["pid"]] + [child[0] for child in unit.get("children", [])]
                counted = sum(base["tpot_process_seconds"](run.ident.hostname, pid, int(by_pid[pid]["RSS"]),
                                                          max(1.0, now - run.boot), False) for pid in pids)
                main = base["tpot_process_seconds"](run.ident.hostname, unit["pid"], int(by_pid[unit["pid"]]["RSS"]),
                                                    max(1.0, now - run.boot), False)
                self.assertGreaterEqual(shown + 0.002, main)
                self.assertAlmostEqual(shown, counted + unit["cpu_ms"] / 1000, delta=0.05)
        # ps and top take the function of the same text (the module code of base.py and service.py)
        from tests.test_top import PatchedTest
        PatchedTest.setUpClass()
        source = (PatchedTest.root / "src/cowrie/commands/base.py").read_text()
        start = source.index("def tpot_process_seconds(")
        self.assertIn(source[start:source.index("\n\n\n", start)], patched_service())


class TelnetUnitTest(unittest.TestCase):
    """The telnet daemon stays in the CGroup of inetd.service (telnet@.service on Red Hat); login and the shell
    move to the session's scope (refs systemd-telnet *-session-status-listener, *-session-status-user-slice);
    the journal of inetutils 2.6 names the connection at the session's start."""

    def status(self, persona_id, unit, now=None):
        import time
        from tests.test_patch import FakeCommand, FakeConfig, module_all
        from tests.test_systemctl import Session, patched_service
        from tpot_personas import network
        run = Session(persona_id)
        persona = catalog.by_id(persona_id)
        namespace = module_all(patched_service(), {"HoneyPotCommand": FakeCommand, "CowrieConfig": FakeConfig(
            {("shell", "systemctl_units"): str(run.out / "systemd-units.json"),
             ("shell", "network"): str(run.out / "network.json"),
             ("shell", "telnet_server"): network.telnet_server(persona, run.ident)})})
        cmd = FakeCommand([])
        events = cmd.protocol.events

        class HoneyPotInteractiveTelnetProtocol:
            terminal = object()
            clientIP = "203.0.113.7"  # noqa: N815 - Cowrie's name
            realClientPort = 51234  # noqa: N815 - Cowrie's name
            logintime = 1_790_000_000.0

        protocol = HoneyPotInteractiveTelnetProtocol()
        protocol.events, protocol.hostname, protocol.user, protocol.cmdstack = events, run.ident.hostname, run.user, []
        protocol.boot_time = lambda: run.boot
        cmd.protocol, cmd.fs = protocol, run.fs
        cmd.user = {"uid": 0, "gid": 0, "username": "root"}
        if now is not None:
            namespace["time"] = type("Clock", (), {"time": staticmethod(lambda: now), "gmtime": time.gmtime,
                                                   "strftime": time.strftime})
        code = namespace["tpot_systemctl"](cmd, ["status", unit, "--no-pager"])
        return bytes(cmd.out).decode(), code, namespace["tpot_session"](protocol, "root")

    def test_the_daemon_stays_in_its_service(self):
        for persona_id in ("debian-bookworm", "debian-trixie", "ubuntu-noble", "rhel-9"):
            out, code, session = self.status(persona_id, "user.slice")
            daemon, login = session["chain"]
            with self.subTest(persona=persona_id):
                self.assertEqual(code, 0)
                tree = out.split("\n     CGroup: ", 1)[1].splitlines()
                self.assertIn(f"{login['pid']} {login['command']}", "\n".join(tree))
                self.assertNotIn(f"{daemon['pid']} ", "\n".join(tree))
                self.assertRegex(tree[-1], r"\u2514\u2500\d+ -bash$")
                if persona_id != "rhel-9":
                    out, _, _ = self.status(persona_id, "inetd.service")
                    self.assertRegex(out, rf"\n +\u2514\u2500{daemon['pid']} telnetd\n")

    def test_the_journal_names_the_connection_at_the_login(self):
        import time
        first = self.status("debian-trixie", "inetd.service", now=1_790_000_100.0)[0]
        later = self.status("debian-trixie", "inetd.service", now=1_790_003_600.0)[0]
        stamp = time.strftime("%b %d %H:%M:%S", time.gmtime(1_790_000_000.0))
        lines = [line for line in first.splitlines() if "telnetd[" in line or "login[" in line]
        self.assertEqual(len(lines), 2, first)
        for line in lines:
            self.assertTrue(line.startswith(stamp + " "), line)
        self.assertEqual(lines, [line for line in later.splitlines() if "telnetd[" in line or "login[" in line])


class ShellKeyTest(unittest.TestCase):
    """The validator checks the [shell] keys of the patched commands: allowed values, their files, the persona."""

    def check(self, persona_id, changes=None):
        import configparser
        from tests.test_systemctl import rendered
        from tpot_personas import tree
        out_dir, ident = rendered(persona_id)
        cfg = configparser.ConfigParser(interpolation=None)
        cfg.read(out_dir / "cowrie.cfg", encoding="utf-8")
        for key, value in (changes or {}).items():
            if value is None:
                cfg.remove_option("shell", key)
            else:
                cfg.set("shell", key, value)
        found = []
        validate._check_shell_keys(catalog.by_id(persona_id), ident, cfg, out_dir, out_dir,
                                   tree.load_pickle(out_dir / "fs.pickle"), found)
        return found

    def test_the_rendered_keys_pass(self):
        for persona_id in ("tplink-archer-c7", "openwrt", "debian-bookworm", "debian-trixie", "ubuntu-noble", "rhel-9",
                           "dlink-dir859", "ubiquiti-edgerouter-x"):
            with self.subTest(persona=persona_id):
                self.assertEqual(self.check(persona_id), [])

    def test_wrong_values_are_found(self):
        cases = {
            "tplink-archer-c7": {"wget_style": "gnu", "wget_timeout": "maybe", "dd_status_line": "maybe",
                                 "busybox_usage": "/nonexistent/usage.json", "login_shell": "/bin/zsh",
                                 "login_path": "/bin:/opt/nothing", "ls_style": "gnu"},
            # (OpenWrt's wget is uclient-fetch, not BusyBox's)
            "openwrt": {"ls_total": "yes", "which_dirs": "/tmp/elsewhere.txt", "wget_style": "busybox"},
            "dlink-dir859": {"wget_style": None},
            "debian-bookworm": {"telnet_server": "netkit", "history_control_root": "ignoreall",
                                "history_control": "ignoredups", "history_export": "HISTSIZE", "ls_style": "busybox"},
            "debian-trixie": {"telnet_server": "inetutils"},
            "ubuntu-noble": {"command_not_found_sudo": "nobody", "command_not_found": "/nonexistent/cnf.txt"},
            "rhel-9": {"telnet_server": None, "history_export": "HISTSIZE,HISTFILE", "command_not_found": "/x"},
        }
        for persona_id, changes in cases.items():
            for key, value in changes.items():
                with self.subTest(persona=persona_id, key=key):
                    found = self.check(persona_id, {key: value})
                    self.assertTrue(any(line.startswith(f"cfg {key}:") for line in found), found)


class OwnerTest(unittest.TestCase):
    """dpkg -S / rpm -qf name the package of every program ps shows (refs t12 *-own-dpkg-S, almalinux-9-t12-owners,
    centos-7-t12-owners); a program below /usr/local, /opt or /home is nobody's."""

    def test_every_program_of_ps_has_its_package(self):
        from tests.test_systemctl import rendered
        from tpot_personas import tree
        for persona_id in SERVERS:
            out_dir, ident = rendered(persona_id)
            found = []
            validate._check_program_owners(catalog.by_id(persona_id), ident, out_dir,
                                           tree.load_pickle(out_dir / "fs.pickle"), found)
            with self.subTest(persona=persona_id):
                self.assertEqual(found, [])

    def test_the_recorded_owners(self):
        from tpot_personas import system
        recorded = {
            "ubuntu-noble": {"/usr/lib/systemd/systemd-timesyncd": "systemd-timesyncd", "/usr/sbin/cron": "cron",
                             "/usr/lib/snapd/snapd": "snapd", "/usr/sbin/irqbalance": "irqbalance",
                             "/usr/bin/dbus-daemon": "dbus-daemon", "/usr/sbin/init": "systemd-sysv",
                             "/usr/share/unattended-upgrades/unattended-upgrade-shutdown": "unattended-upgrades",
                             "/usr/lib/systemd/systemd-udevd": "udev", "/usr/bin/bash": "bash"},
            "ubuntu-jammy": {"/lib/systemd/systemd-timesyncd": "systemd-timesyncd", "/usr/bin/dbus-daemon": "dbus",
                             "/lib/systemd/systemd-resolved": "systemd", "/sbin/init": "systemd-sysv",
                             "/sbin/agetty": "util-linux", "/bin/bash": "bash"},
            "debian-trixie": {"/usr/lib/openssh/sshd-session": "openssh-server", "/usr/bin/dbus-daemon": "dbus-daemon",
                              "/usr/lib/systemd/systemd-udevd": "udev"},
            "debian-bookworm": {"/lib/systemd/systemd-networkd": "systemd", "/usr/bin/dbus-daemon": "dbus-daemon"},
            "raspios": {"/usr/sbin/NetworkManager": "network-manager", "/usr/sbin/avahi-daemon": "avahi-daemon",
                        "/sbin/wpa_supplicant": "wpasupplicant", "/usr/sbin/ModemManager": "modemmanager",
                        "/usr/libexec/bluetooth/bluetoothd": "bluez", "/usr/sbin/thd": "triggerhappy"},
            "rhel-9": {"/usr/bin/dbus-broker": "dbus-broker", "/usr/sbin/agetty": "util-linux-core",
                       "/usr/lib/systemd/systemd-journald": "systemd", "/usr/libexec/nm-dhcp-helper": "NetworkManager"},
            "centos-7": {"/usr/libexec/nm-dhcp-helper": "NetworkManager", "/usr/sbin/postfix": "postfix",
                         "/usr/libexec/postfix/qmgr": "postfix", "/usr/libexec/postfix/pickup": "postfix",
                         "/usr/lib/polkit-1/polkitd": "polkit", "/sbin/dhclient": "dhclient", "/sbin/agetty": "util-linux"},
        }
        for persona_id, owners in recorded.items():
            persona, ident, _ = built(persona_id)
            for path, package in owners.items():
                with self.subTest(persona=persona_id, path=path):
                    self.assertEqual(system.program_owner(persona, ident, path), package)
        # dpkg knows the spelling of its own records only (noble: /usr/sbin/init, not /sbin/init)
        noble = built("ubuntu-noble")
        self.assertIsNone(system.program_owner(noble[0], noble[1], "/sbin/init", alias=False))
        # the versions of the packages that own the daemons (refs t12 *-own-*, apt-cache policy of the images)
        installed = system.apt_packages(noble[0], noble[1])["installed"]
        self.assertEqual(installed["systemd-timesyncd"], installed["systemd"])
        self.assertEqual(installed["irqbalance"], "1.9.3-2ubuntu5")
        self.assertIn("NetworkManager", system.centos_installed(built("centos-7")[1]))

    def test_the_validator_names_a_program_without_a_package(self):
        from tests.test_systemctl import rendered
        from tpot_personas import tree
        out_dir, ident = rendered("ubuntu-noble")
        processes = json.loads((out_dir / "cmdoutput.json").read_text())
        with tempfile.TemporaryDirectory() as handle:
            copy = Path(handle)
            for name in ("apt-packages.json", "systemd-units.json"):
                (copy / name).write_text((out_dir / name).read_text())
            processes["command"]["ps"].append(dict(processes["command"]["ps"][1], PID=4242,
                                                   COMMAND="/usr/sbin/nosuchd --foreground"))
            (copy / "cmdoutput.json").write_text(json.dumps(processes))
            found = []
            validate._check_program_owners(catalog.by_id("ubuntu-noble"), ident, copy,
                                           tree.load_pickle(out_dir / "fs.pickle"), found)
        self.assertEqual(found, ["owners: /usr/sbin/nosuchd of '/usr/sbin/nosuchd --foreground' is not in the tree",
                                 "owners: no package owns /usr/sbin/nosuchd of '/usr/sbin/nosuchd --foreground'"])


class LiveFs:
    """Cowrie's HoneyPotFilesystem on a rendered tree as far as systemctl and the package managers use it: links
    of folders followed, files written into their nodes, links made and removed."""

    def __init__(self, root):
        import copy
        self.fs = copy.deepcopy(root)
        self.newcount = 0

    def resolve_path(self, path, cwd):
        import posixpath
        return posixpath.normpath(posixpath.join(cwd, path))

    def getfile(self, path, follow_symlinks=True):
        import posixpath
        from tpot_personas import tree
        node, parts = self.fs, [p for p in path.split("/") if p]
        here = ""
        for index, part in enumerate(parts):
            if not isinstance(node, list) or not isinstance(node[tree.A_CONTENTS], list):
                return None
            child = next((c for c in node[tree.A_CONTENTS] if c[tree.A_NAME] == part), None)
            if child is None:
                return None
            last = index == len(parts) - 1
            if child[tree.A_TYPE] == tree.T_LINK and (follow_symlinks or not last):
                target = child[tree.A_TARGET] or ""
                target = target if target.startswith("/") else posixpath.normpath(posixpath.join("/" + here, target))
                child = self.getfile(target, True) if target.count("/") < 64 else None
                if child is None:
                    return None
            node = child
            here = posixpath.join(here, part)
        return node

    def exists(self, path):
        return self.getfile(path) is not None

    def lexists(self, path):
        return self.getfile(path, follow_symlinks=False) is not None

    def isdir(self, path):
        from tpot_personas import tree
        node = self.getfile(path)
        return node is not None and node[tree.A_TYPE] == tree.T_DIR

    def file_contents(self, path):
        from tpot_personas import tree
        node = self.getfile(path)
        if node is None or node[tree.A_TYPE] != tree.T_FILE:
            raise FileNotFoundError(path)
        data = node[tree.A_CONTENTS]
        return data if isinstance(data, bytes) else b""

    def mkdir(self, path, uid, gid, size, mode):
        parent, name = path.rsplit("/", 1)
        self.getfile(parent or "/")[7].append([name, 1, uid, gid, size, mode, 0, [], None, None])

    def mkfile(self, path, uid, gid, size, mode):
        parent, name = path.rsplit("/", 1)
        self.getfile(parent or "/")[7].append([name, 2, uid, gid, size, mode, 0, None, None, None])
        return True

    def link_entry(self, entry, dirpath):
        self.getfile(dirpath)[7].append(entry)

    def remove(self, path):
        parent, name = path.rsplit("/", 1)
        folder = self.getfile(parent or "/")
        before = len(folder[7])
        folder[7][:] = [c for c in folder[7] if c[0] != name]
        if len(folder[7]) == before:
            raise OSError(2, "No such file or directory")


class PackageUnitSession:
    """systemctl, ss and the package hook of one session on a rendered persona (refs t12)."""

    def __init__(self, persona_id, telnet=False):
        import time
        from tests.test_net_tools import Base, Log, patched_source
        from tests.test_patch import FakeCommand, FakeConfig, module_all
        from tests.test_systemctl import patched_service, rendered
        from tests.test_telnet_network import session_for
        from tpot_personas import tree
        self.out, self.ident = rendered(persona_id)
        self.persona = catalog.by_id(persona_id)
        keys = {("shell", "systemctl_units"): str(self.out / "systemd-units.json"),
                ("shell", "network"): str(self.out / "network.json"),
                ("shell", "package_units"): str(self.out / "package-units.json"),
                ("shell", "ssh_version"): self.ident.release.ssh_version}
        from tpot_personas import network
        if network.telnet_server(self.persona, self.ident):
            keys[("shell", "telnet_server")] = network.telnet_server(self.persona, self.ident)
        self.service = module_all(patched_service(), {"HoneyPotCommand": FakeCommand, "CowrieConfig": FakeConfig(keys)})
        self.net = module_all(patched_source(), {"HoneyPotCommand": Base, "CowrieConfig": FakeConfig(keys), "log": Log,
                                                 "commands": {}})
        self.processes = json.loads((self.out / "cmdoutput.json").read_text())["command"]["ps"]
        self.fs = LiveFs(tree.load_pickle(self.out / "fs.pickle"))
        self.protocol = session_for(self.processes, telnet=telnet)
        self.protocol.hostname, self.protocol.fs, self.protocol.cmdstack = self.ident.hostname, self.fs, []
        self.protocol.boot_time = lambda: time.time() - 3 * 86400
        self.protocol.events = type("Events", (), {"dispatch": lambda self, *a, **k: None})()
        self.protocol.terminal = object()
        self.data = json.loads((self.out / "network.json").read_text())

    def command(self, args):
        from tests.test_patch import FakeCommand
        cmd = FakeCommand(args)
        cmd.fs, cmd.protocol = self.fs, self.protocol
        cmd.user = {"uid": 0, "gid": 0, "username": "root"}
        return cmd

    def systemctl(self, *args):
        cmd = self.command(args)
        code = self.service["tpot_systemctl"](cmd, list(args))
        return bytes(cmd.out).decode(), cmd.err, code

    def change(self, package, change, nl="\n"):
        return self.service["tpot_package_change"](self.command([]), package, change, nl)

    def ss(self, *args):
        cmd = self.net["Command_tpot_ss"](list(args), self.protocol, {"uid": 0, "username": "root"})
        cmd.start()
        return cmd.out

    def listening(self, port):
        return [line for line in self.ss("-tlnp").splitlines() if f":{port} " in line]

    def ps(self, text):
        return [p for p in self.processes if text in str(p["COMMAND"])]


class PackageUnitTest(unittest.TestCase):
    """The package <-> service link (refs t12 debian-12-t12-*, ubuntu-24-04-t12-*, almalinux-9-t12-*,
    centos-7-t12-scripts)."""

    def test_debian_remove_stops_the_daemon_and_takes_its_unit_purge_its_links(self):
        run = PackageUnitSession("debian-bookworm")
        self.assertEqual(run.systemctl("is-active", "ssh")[0], "active\n")
        self.assertTrue(any('"sshd"' in line for line in run.listening(22)))
        listener = run.ps("[listener]")[0]["PID"]
        self.assertEqual(run.change("openssh-server", "remove"), "")
        # ref debian-12-t12-after-remove-openssh-server: the unit is gone, its links stay, the listener went
        out, err, code = run.systemctl("status", "ssh", "--no-pager")
        self.assertEqual((out, code), ("", 4))
        self.assertIn("Unit ssh.service could not be found.", err)
        self.assertEqual(run.listening(22), [])
        self.assertNotIn(listener, [p["PID"] for p in run.processes])
        self.assertFalse(run.fs.exists("/lib/systemd/system/ssh.service"))
        for link in ("/etc/systemd/system/multi-user.target.wants/ssh.service", "/etc/systemd/system/sshd.service"):
            self.assertTrue(run.fs.lexists(link), link)
        self.assertNotIn("ssh.service", run.systemctl("list-units", "--all", "--no-legend")[0])
        # purge: deb-systemd-helper purge takes the links (ref debian-12-t12-after-purge-openssh-server)
        self.assertEqual(run.change("openssh-server", "purge"), "")
        self.assertFalse(run.fs.lexists("/etc/systemd/system/multi-user.target.wants/ssh.service"))
        self.assertFalse(run.fs.lexists("/etc/systemd/system/sshd.service"))
        # installed again: its unit, enabled and started (the postinst), with a new listener
        out = run.change("openssh-server", "install")
        self.assertIn("Created symlink /etc/systemd/system/multi-user.target.wants/ssh.service \u2192 "
                      "/lib/systemd/system/ssh.service.\n\n", out)
        self.assertEqual(run.systemctl("is-active", "ssh")[0], "active\n")
        self.assertTrue(run.listening(22))

    def test_the_session_survives_the_removal_of_its_server(self):
        # ssh.service has KillMode=process (ref debian-12-t12-files-openssh-server): only the listener goes
        run = PackageUnitSession("debian-bookworm")
        before = [line for line in run.ss("-tnp").splitlines() if line.startswith("ESTAB")]
        run.change("openssh-server", "remove")
        self.assertEqual([line for line in run.ss("-tnp").splitlines() if line.startswith("ESTAB")], before)
        self.assertTrue(before)

    def test_telnetd_leaves_inetd_without_its_line(self):
        # ref debian-12-t12-after-remove-inetutils-telnetd: inetd runs on, 23 is no longer served
        run = PackageUnitSession("debian-bookworm")
        self.assertTrue(run.listening(23))
        run.change("inetutils-telnetd", "remove")
        self.assertEqual(run.listening(23), [])
        self.assertEqual(run.systemctl("is-active", "inetd")[0], "active\n")
        self.assertNotIn("\ntelnet\t", run.fs.file_contents("/etc/inetd.conf").decode())
        # removing openbsd-inetd stops inetd and takes its unit (ref debian-12-t12-after-remove-openbsd-inetd)
        run.change("openbsd-inetd", "remove")
        self.assertEqual(run.ps("/usr/sbin/inetd"), [])
        self.assertIn("could not be found", run.systemctl("status", "inetd")[1])

    def test_red_hat_removes_disabled(self):
        run = PackageUnitSession("rhel-9")
        # telnet-server: telnet.socket goes, systemd no longer listens on 23 (ref almalinux-9-t12-after-remove-telnet-server)
        self.assertTrue(run.listening(23))
        run.change("telnet-server", "remove")
        self.assertEqual(run.listening(23), [])
        self.assertIn("could not be found", run.systemctl("status", "telnet.socket")[1])
        # openssh-server comes back enabled (its preset), not started
        run.change("openssh-server", "remove")
        self.assertEqual(run.listening(22), [])
        self.assertIn("Created symlink /etc/systemd/system/multi-user.target.wants/sshd.service",
                      run.change("openssh-server", "install"))
        self.assertEqual(run.systemctl("is-active", "sshd")[0], "inactive\n")

    def test_centos_scriptlets_write_nothing(self):
        # ref centos-7-t12-scripts: systemctl --no-reload disable / stop > /dev/null 2>&1
        run = PackageUnitSession("centos-7")
        self.assertEqual(run.systemctl("is-active", "tuned")[0], "active\n")
        self.assertEqual(run.change("tuned", "remove"), "")
        self.assertEqual(run.ps("/usr/sbin/tuned"), [])
        self.assertFalse(run.fs.lexists("/etc/systemd/system/multi-user.target.wants/tuned.service"))
        # dhclient has no scriptlets: NetworkManager's dhclient runs on
        run.change("dhclient", "remove")
        self.assertTrue(run.ps("/sbin/dhclient"))
        # postfix: master and its children stop
        run.change("postfix", "remove")
        self.assertEqual(run.ps("postfix/master") + run.ps("qmgr") + run.ps("pickup"), [])


class SocketListenerTest(unittest.TestCase):
    """A socket unit without its service: systemd itself holds the port (ref ubuntu-24-04-t12r2-state-openssh-server)."""

    def test_noble_socket_listens_after_the_reinstall(self):
        run = PackageUnitSession("ubuntu-noble")
        run.change("openssh-server", "purge")
        self.assertEqual(run.listening(22), [])
        run.change("openssh-server", "install")
        self.assertEqual(run.systemctl("is-active", "ssh.socket")[0], "active\n")
        lines = run.listening(22)
        self.assertEqual(len(lines), 2, lines)
        for line in lines:
            self.assertRegex(line, r'users:\(\("systemd",pid=1,fd=\d+\)\)\s*$')
            self.assertNotIn("sshd", line)


class PackageSession(SessionCase):
    """A session on the patched package managers and systemctl of a rendered persona, its hook wired."""

    def session(self, persona_id):
        from tpot_personas import tree
        run = super().session(persona_id)
        run.protocol.fs = LiveFs(tree.load_pickle(run.out / "fs.pickle"))
        run.protocol.process = json.loads((run.out / "cmdoutput.json").read_text())["command"]["ps"]
        service = next(c for c in run.commands.values() if c.__name__ == "Command_systemctl").call.__globals__
        for command in run.commands.values():
            namespace = getattr(getattr(command, "call", None), "__globals__", {})
            if "tpot_package_units" in namespace:
                namespace["TPOT_SERVICE"] = service
        return run


class PackageCommandTest(PackageSession):
    """apt-get, dpkg, dnf and rpm run the hook of their package's units (the patched modules of one session)."""

    def test_apt_get_installs_and_removes_a_service(self):
        run = self.session("debian-bookworm")
        out, err, code = run("apt-get install -y nginx")
        self.assertEqual(code, 0, err)
        # ref debian-12-t12-install-nginx
        self.assertIn("The following additional packages will be installed:\n  nginx-common\n", out)
        self.assertIn("The following NEW packages will be installed:\n  nginx nginx-common\n", out)
        self.assertIn("Get:1 http://deb.debian.org/debian-security bookworm-security/main amd64 nginx-common all "
                      "1.22.1-9+deb12u10 [115 kB]\n", out)
        self.assertRegex(out, r"Setting up nginx-common \(1\.22\.1-[^)]+\) \.\.\.\r\nCreated symlink "
                              r"/etc/systemd/system/multi-user\.target\.wants/nginx\.service \u2192 "
                              r"/lib/systemd/system/nginx\.service\.\r\n\r\nSetting up nginx \(")
        self.assertIn("Upgrading binary: nginx.\r\n", out)
        self.assertEqual(run("systemctl is-active nginx")[0], "active\n")
        self.assertIn("nginx: master process", "".join(p["COMMAND"] for p in run.protocol.process))
        run("apt-get remove -y nginx nginx-common")
        self.assertIn("could not be found", run("systemctl status nginx")[1])
        self.assertNotIn("nginx:", "".join(p["COMMAND"] for p in run.protocol.process))
        # the persona's own daemon: dpkg -r stops it and takes its unit
        run("dpkg -r openssh-server")
        self.assertIn("could not be found", run("systemctl status ssh")[1])
        self.assertNotIn("[listener]", "".join(p["COMMAND"] for p in run.protocol.process))

    def test_dpkg_names_the_unit_file_of_an_installed_service(self):
        run = self.session("debian-bookworm")
        run("apt-get install -y nginx")
        # ref debian-12-t12-files-nginx: the unit is nginx-common's, a file without a manual page of its own
        out = run("dpkg -S nginx.service")[0]
        self.assertEqual(out, "nginx-common: /lib/systemd/system/nginx.service\n")
        # nginx-common ships no program (ref debian-12-t12-files-nginx): no /usr/bin/nginx-common, no manual page
        files = run("dpkg -L nginx-common")[0]
        self.assertNotIn("/usr/share/man/man1/", files)
        self.assertNotIn("/usr/bin/nginx-common", files)
        self.assertIn("/lib/systemd/system/nginx.service\n", files)
        self.assertEqual(run("dpkg -S /usr/sbin/nginx")[0], "nginx: /usr/sbin/nginx\n")

    def test_dnf_and_rpm_run_the_scriptlets(self):
        run = self.session("rhel-9")
        run("dnf install -y nginx")
        self.assertEqual(run("systemctl is-enabled nginx")[0], "disabled\n")
        out, err, code = run("dnf remove -y tuned")
        self.assertEqual(code, 0, err)
        # ref almalinux-9-t12-remove-tuned: the systemctl line of the preun before the package is erased
        self.assertRegex(out, r"  Running scriptlet: tuned-[^ ]+ +1/1\nRemoved \"/etc/systemd/system/"
                              r"multi-user\.target\.wants/tuned\.service\"\.\n\n  Erasing +: tuned-")
        self.assertNotIn("tuned -l -P", "".join(p["COMMAND"] for p in run.protocol.process))
        # ref almalinux-9-t12-remove-openssh-server: rpm -e writes it on stderr
        out, err, code = run("rpm -e openssh-server")
        self.assertEqual((out, err, code), ("", 'Removed "/etc/systemd/system/multi-user.target.wants/sshd.service".\n', 0))
        self.assertIn("could not be found", run("systemctl status sshd")[1])


class LinkTest(unittest.TestCase):
    """Links the evidence named (round 4): openbsd-inetd.service is a link to inetd.service (ref
    debian-12-ls-unit-inetd), ls -l and ls -ld show /proc/self as the link to the shell's folder."""

    def test_openbsd_inetd_service_is_a_link(self):
        from tests.test_systemctl import rendered
        from tpot_personas import tree
        for persona_id in ("debian-bookworm", "debian-trixie", "ubuntu-jammy", "ubuntu-noble", "raspios"):
            out_dir, _ = rendered(persona_id)
            fs = tree.load_pickle(out_dir / "fs.pickle")
            with self.subTest(persona=persona_id):
                node = tree.find_node(fs, "usr/lib/systemd/system/openbsd-inetd.service")
                self.assertEqual((node[tree.A_TYPE], node[tree.A_TARGET], node[tree.A_SIZE]),
                                 (tree.T_LINK, "inetd.service", 13))

    def test_ls_shows_proc_self_as_its_link(self):
        from unittest import mock
        from tests import test_ls

        class Fs(test_ls.Fs):
            def __init__(self):
                super().__init__()
                shell = test_ls.node("4219", test_ls.T_DIR, 0, children=[])
                self.root[test_ls.A_CONTENTS].append(test_ls.node("proc", test_ls.T_DIR, 0, children=[
                    shell, test_ls.node("self", test_ls.T_LINK, 4, "4219", mode=0o777)]))

        test_ls.LsTest.setUpClass()
        case = test_ls.LsTest("test_upstream_followed_the_link")
        with mock.patch.object(test_ls, "Fs", Fs):
            for args in (["-ld", "/proc/self"], ["-l", "/proc/self"]):
                with self.subTest(args=args):
                    out, _ = case.ls(args)
                    self.assertTrue(out.startswith("lrwxrwxrwx 1 root root 4 ") and out.endswith(" /proc/self -> 4219\n"),
                                    out)


class FixRoundTwoTest(unittest.TestCase):
    """The recorded install data and the texts systemd prints (task 12, fix round 2)."""

    def test_postinst_blocks_hold_no_unpack_lines_or_architecture(self):
        import re
        from tpot_personas import package_units
        unpack = re.compile(r"^(Selecting previously unselected package |Preparing to unpack |Unpacking |"
                            r"\(Reading database )")
        arch = re.compile(r"\b(arm64|amd64|armhf|i386|aarch64|x86_64)\b")
        for release, data in package_units.installs()["deb"].items():
            for name, lines in data["blocks"].items():
                for line in lines:
                    with self.subTest(release=release, package=name, line=line):
                        self.assertIsNone(unpack.match(line))
                        self.assertIsNone(arch.search(line))
                        self.assertIsNone(arch.search(line))

    def test_recorded_rows_leave_the_installed_packages_alone(self):
        # the recorded installs name the candidates of their day; the packages a persona has keep the rows of its
        #   index, so apt list --upgradable stays as it was (udev with systemd, trixie's logrotate, openbsd-inetd)
        from unittest import mock
        from tpot_personas import package_units, system

        def upgradable(data):
            return sorted(name for name, version in data["installed"].items()
                          if name in data["meta"] and data["meta"][name][0] != version)

        for persona_id in ("debian-bookworm", "debian-trixie", "ubuntu-jammy", "ubuntu-noble", "raspios"):
            persona, ident, _ = built(persona_id)
            data = system.apt_packages(persona, ident)
            with mock.patch.object(package_units, "deb_release", lambda *a: {}):
                plain = system.apt_packages(persona, ident)
            with self.subTest(persona=persona_id):
                self.assertEqual(upgradable(data), upgradable(plain))
                self.assertEqual(data["installed"], plain["installed"])


class FixRoundOneTest(unittest.TestCase):
    """Fix round 1 of Task 12: one table per source package, the helpers' CPU, the Pi's groups, the inetd line of a
    new telnetd, postfix's tree, spawned PIDs, the spellings of a path, no package hook on a device."""

    def test_the_binaries_of_one_source_come_from_one_table(self):
        from tpot_personas import package_units, system
        for persona_id in ("debian-bookworm", "debian-trixie", "ubuntu-jammy", "ubuntu-noble", "raspios"):
            persona, ident, _ = built(persona_id)
            meta = system.apt_packages(persona, ident)["meta"]
            table = package_units.SERVICE_META["bookworm" if persona_id == "raspios" else system.codename(persona, ident)]
            with self.subTest(persona=persona_id):
                for name, row in table.items():
                    self.assertEqual(meta[name][:3], row[:3], name)

    def test_the_helpers_cpu_belongs_to_units_that_run_helpers(self):
        from tpot_personas import servers
        for persona_id in ("debian-bookworm", "rhel-9", "ubuntu-noble"):
            persona, ident, content = built(persona_id)
            texts = servers.unit_texts(persona, ident)
            for unit in servers.systemd_units(persona, ident, content.processes)["units"]:
                if unit.get("sub") != "running" or not unit["name"].endswith(".service"):
                    continue
                keys = servers.unit_keys(texts.get(unit["path"].rsplit("/", 1)[-1], ""))
                helpers = any(keys.get(k) for k in ("ExecStartPre", "ExecStartPost", "ExecCondition"))
                with self.subTest(persona=persona_id, unit=unit["name"]):
                    self.assertEqual(unit["cpu_ms"] > 0, helpers)

    def test_the_groups_of_the_pi_have_their_own_numbers(self):
        persona, ident, content = built("raspios")
        gids = [line.split(":")[2] for line in content.files["etc/group"].splitlines() if line]
        self.assertEqual(len(gids), len(set(gids)))
        uids = [line.split(":")[2] for line in content.files["etc/passwd"].splitlines() if line]
        self.assertEqual(len(uids), len(set(uids)))
        groups = table(content.files["etc/group"])
        self.assertEqual([groups[name][2] for name in ("gpio", "i2c", "spi")], ["997", "998", "999"])
        self.assertNotIn("systemd-resolve", groups)

    def test_a_new_telnetd_comes_with_its_line_off(self):
        # refs systemd-telnet: inetutils-telnetd adds its line as "#<off># telnet ...", inetd does not serve 23
        run = PackageUnitSession("debian-bookworm")
        run.change("inetutils-telnetd", "remove")
        run.change("inetutils-telnetd", "install")
        conf = run.fs.file_contents("/etc/inetd.conf").decode()
        self.assertIn("\n#<off># telnet\tstream\ttcp\tnowait\troot\t/usr/sbin/tcpd\t/usr/sbin/telnetd\n", conf)
        self.assertNotIn("\ntelnet\t", conf)
        self.assertEqual(run.listening(23), [])

    def test_spawned_pids_never_come_back(self):
        run = PackageUnitSession("rhel-9")
        used = set()
        for _ in range(3):
            run.systemctl("restart", "crond")
            pid = run.ps("/usr/sbin/crond")[0]["PID"]
            self.assertNotIn(pid, used)
            self.assertGreater(pid, max(used, default=0))
            used.add(pid)

    def test_the_spellings_of_a_path(self):
        from tpot_personas import system
        self.assertEqual(system.path_spellings("/usr/libexec/nm-dhcp-helper"), ["/usr/libexec/nm-dhcp-helper"])
        self.assertEqual(system.path_spellings("/sbin/init"), ["/sbin/init", "/usr/sbin/init"])
        self.assertEqual(system.path_spellings("/usr/lib/systemd/systemd"), ["/usr/lib/systemd/systemd",
                                                                             "/lib/systemd/systemd"])

    def test_no_device_gets_the_package_hook(self):
        from pathlib import Path
        from tpot_personas import render, system
        for persona in catalog.PERSONAS:
            persona, ident, content = built(persona.id)
            keys = system.shell_keys(persona, ident, content.files, Path("/r"))
            with self.subTest(persona=persona.id):
                self.assertEqual("package_units" in keys, persona.kind == "server")
                if persona.kind == "server" or system.has_systemd(persona):
                    self.assertEqual("package-units.json" in render.runtime_json(persona, ident, content.processes),
                                     persona.kind == "server")


class PostfixTreeTest(SessionCase):
    def test_yum_remove_postfix_takes_its_programs(self):
        run = self.session("centos-7")
        run("yum remove -y postfix")
        for path in ("/usr/libexec/postfix/master", "/usr/libexec/postfix/qmgr", "/usr/sbin/postfix"):
            self.assertIn(path, run.removed)


class InstalledFilesTest(PackageSession):
    """Fix round 1: a covered package's programs and units are in the tree and in dpkg / rpm (refs t12
    *-t12r2-files-*), a unit whose program went fails or skips its start as recorded (*-start-without-binary),
    the installs write the texts of the recordings."""

    def test_dpkg_knows_the_files_of_nginx(self):
        run = self.session("debian-bookworm")
        run("apt-get install -y nginx")
        self.assertEqual(run("dpkg -S /lib/systemd/system/nginx.service")[0], "nginx-common: /lib/systemd/system/nginx.service\n")
        self.assertEqual(run("dpkg -S /usr/sbin/nginx")[0], "nginx: /usr/sbin/nginx\n")
        self.assertTrue(run.protocol.fs.exists("/usr/sbin/nginx"))
        self.assertIn("/usr/sbin/nginx\n", run("dpkg -L nginx")[0])

    def test_a_unit_without_its_program_fails_with_203(self):
        # ref debian-12-t12r2-start-without-binary
        run = self.session("debian-bookworm")
        run("apt-get install -y nginx")
        run("apt-get remove -y nginx")
        out, err, code = run("systemctl start nginx")
        self.assertEqual((out, code), ("", 1))
        self.assertEqual(err, "Job for nginx.service failed because the control process exited with error code.\n"
                              'See "systemctl status nginx.service" and "journalctl -xeu nginx.service" for details.\n')
        status = run("systemctl status nginx --no-pager")[0]
        self.assertTrue(status.startswith("\u00d7 nginx.service - A high performance web server"), status)
        self.assertRegex(status, r"\n     Active: failed \(Result: exit-code\) since ")
        self.assertRegex(status, r"\n    Process: \d+ ExecStartPre=/usr/sbin/nginx -t -q -g daemon on; master_process on; "
                                 r"\(code=exited, status=203/EXEC\)\n")
        self.assertIn("nginx.service: Failed to locate executable /usr/sbin/nginx: No such file or directory", status)
        self.assertIn("systemd[1]: Failed to start nginx.service - A high performance web server and a reverse proxy "
                      "server.", status)
        self.assertEqual(run("systemctl is-active nginx")[0], "failed\n")

    def test_a_unit_with_its_condition_skips_its_start(self):
        # ref ubuntu-24-04-t12r2-start-without-binary: ConditionFileIsExecutable of noble's nginx.service
        run = self.session("ubuntu-noble")
        run("apt-get install -y nginx")
        run("apt-get remove -y nginx")
        self.assertEqual(run("systemctl start nginx"), ("", "", 0))
        status = run("systemctl status nginx --no-pager")[0]
        self.assertIn("\n  Condition: start condition unmet at ", status)
        self.assertIn("\n             \u2514\u2500 ConditionFileIsExecutable=/usr/sbin/nginx was not met\n", status)
        self.assertIn("was skipped because of an unmet condition check (ConditionFileIsExecutable=/usr/sbin/nginx).\n",
                      status)
        # jammy: the binary is nginx-core's, removing nginx stops nothing (ref ubuntu-22-04-t12r2-start-without-binary)
        run = self.session("ubuntu-jammy")
        run("apt-get install -y nginx")
        self.assertIn("nginx-core", run("dpkg -S /usr/sbin/nginx")[0])
        run("apt-get remove -y nginx")
        self.assertEqual(run("systemctl is-active nginx")[0], "active\n")

    def test_the_install_texts_of_the_recordings(self):
        run = self.session("debian-bookworm")
        out = run("apt-get install -y mariadb-server")[0]
        # ref debian-12-t12r2-install-mariadb-server: its dependencies the persona has not
        self.assertIn("galera-4", out)
        self.assertIn("Created symlink /etc/systemd/system/multi-user.target.wants/mariadb.service \u2192 "
                      "/lib/systemd/system/mariadb.service.\r\n", out)
        self.assertIn("update-alternatives: using /etc/mysql/mariadb.cnf to provide /etc/mysql/my.cnf (my.cnf) in auto "
                      "mode\r\n", out)
        # openssh-server after a purge: its config and keys again (ref debian-12-t12r2-install-openssh-server)
        run("apt-get purge -y openssh-server")
        out = run("apt-get install -y openssh-server")[0]
        self.assertIn("Creating config file /etc/ssh/sshd_config with new version\r\n", out)
        self.assertRegex(out, r"Creating SSH2 ED25519 key; this may take some time \.\.\.\r\n256 SHA256:[A-Za-z0-9+/]{43} "
                              r"root@\S+ \(ED25519\)\r\n")
        self.assertIn("rescue-ssh.target is a disabled or a static unit, not starting it.\r\n", out)

    def test_noble_takes_ssh_back_through_its_socket(self):
        # refs ubuntu-24-04-t12r2-install-openssh-server, -state-: only the socket's links, ssh.service disabled
        run = self.session("ubuntu-noble")
        run("apt-get purge -y openssh-server")
        out = run("apt-get install -y openssh-server")[0]
        self.assertIn("Created symlink /etc/systemd/system/sockets.target.wants/ssh.socket \u2192 "
                      "/usr/lib/systemd/system/ssh.socket.\r\n\r\nCreated symlink /etc/systemd/system/ssh.service.requires/"
                      "ssh.socket \u2192 /usr/lib/systemd/system/ssh.socket.\r\n", out)
        self.assertNotIn("multi-user.target.wants/ssh.service", out)
        self.assertEqual(run("systemctl is-enabled ssh")[0], "disabled\n")
        self.assertEqual(run("systemctl is-active ssh")[0], "inactive\n")
        self.assertEqual(run("systemctl is-active ssh.socket")[0], "active\n")

    def test_dnf_brings_the_dependencies_and_rpm_knows_the_files(self):
        run = self.session("rhel-9")
        out = run("dnf install -y nginx")[0]
        # ref almalinux-9-t12r2-install-nginx (redhat- for almalinux-logos-httpd)
        self.assertIn("Installing dependencies:\n", out)
        for name in ("nginx-core", "nginx-filesystem", "redhat-logos-httpd"):
            # a name wider than its column has a line of its own (the repository ids of RHEL are long)
            self.assertRegex(out, rf"\n {re.escape(name)}[ \n]")
        self.assertRegex(run("rpm -qf /usr/lib/systemd/system/nginx.service")[0], r"^nginx-\d")
        self.assertRegex(run("rpm -qf /usr/sbin/nginx")[0], r"^nginx-core-\d")
        self.assertIn("/usr/lib/systemd/system/nginx.service\n", run("rpm -ql nginx")[0])

    def test_centos_installs_httpd_and_mariadb_disabled(self):
        # refs centos-7-t12r2-units, -files, -run-httpd
        run = self.session("centos-7")
        run("yum install -y httpd mariadb-server")
        self.assertEqual(run("systemctl is-enabled httpd")[0], "disabled\n")
        self.assertEqual(run("systemctl is-enabled mariadb")[0], "disabled\n")
        self.assertRegex(run("rpm -qf /usr/lib/systemd/system/httpd.service")[0], r"^httpd-2\.4\.6-")
        self.assertIn("/usr/sbin/httpd\n", run("rpm -ql httpd")[0])
        self.assertIn("/usr/libexec/mysqld\n", run("rpm -ql mariadb-server")[0])
        run("systemctl start httpd")
        rows = [p for p in run.protocol.process if p["COMMAND"] == "/usr/sbin/httpd -DFOREGROUND"]
        self.assertEqual([p["USERNAME"] for p in rows], ["root"] + ["apache"] * 5)


class ServiceInstallTest(PackageSession):
    """Installing a covered service through apt-get and dnf (refs t12 *-t12r2-install-*, -state-, -files-)."""

    def test_debian_installs_and_starts_a_service(self):
        run = self.session("ubuntu-noble")
        out = run("apt-get install -y nginx")[0]
        # ref ubuntu-24-04-t12r2-install-nginx: nginx-common's postinst enables, nginx's starts
        self.assertIn("Created symlink /etc/systemd/system/multi-user.target.wants/nginx.service \u2192 "
                      "/usr/lib/systemd/system/nginx.service.\r\n", out)
        self.assertIn(" * Upgrading binary nginx\r\n   ...done.\r\n", out)
        self.assertEqual(run("systemctl is-enabled nginx")[0], "enabled\n")
        self.assertEqual(run("systemctl is-active nginx")[0], "active\n")
        processes = run.protocol.process
        master = [p for p in processes if "nginx: master process" in p["COMMAND"]]
        workers = [p for p in processes if "nginx: worker process" in p["COMMAND"]]
        self.assertEqual(len(master), 1)
        self.assertEqual(len(workers), run.ident.hardware.cpus)
        self.assertEqual({w["PPID"] for w in workers}, {master[0]["PID"]})
        self.assertEqual({w["USERNAME"] for w in workers}, {"www-data"})
        status = run("systemctl status nginx --no-pager")[0]
        self.assertIn(f"Main PID: {master[0]['PID']} (nginx)", status)
        run("apt-get remove -y nginx")
        self.assertEqual(run("systemctl is-active nginx")[0], "inactive\n")
        self.assertFalse([p for p in run.protocol.process if "nginx:" in p["COMMAND"]])
        run("apt-get remove -y nginx-common")
        self.assertIn("could not be found", run("systemctl status nginx")[1])

    def test_a_service_account_comes_with_its_package(self):
        run = self.session("debian-bookworm")
        run("apt-get install -y redis-server")
        passwd = run.protocol.fs.file_contents("/etc/passwd").decode()
        line = next(line for line in passwd.splitlines() if line.startswith("redis:"))
        self.assertRegex(line, r"^redis:x:\d{3}:\d{3}::/var/lib/redis:/usr/sbin/nologin$")
        self.assertIn(f"redis:x:{line.split(':')[3]}:", run.protocol.fs.file_contents("/etc/group").decode())
        self.assertEqual([p["USERNAME"] for p in run.protocol.process if "redis-server" in p["COMMAND"]], ["redis"])
        # (ref debian-12-t12r2-files-redis-server)
        self.assertEqual(run("dpkg -S /usr/bin/redis-server")[0], "redis-server: /usr/bin/redis-server\n")

    def test_red_hat_installs_disabled(self):
        run = self.session("rhel-9")
        # ref almalinux-9-t12r2-state-nginx: disabled and inactive until enable --now
        out = run("dnf install -y nginx")[0]
        self.assertNotIn("Created symlink", out)
        self.assertEqual(run("systemctl is-enabled nginx")[0], "disabled\n")
        self.assertEqual(run("systemctl is-active nginx")[0], "inactive\n")
        out, err, code = run("systemctl enable --now nginx")
        self.assertEqual(err, "Created symlink /etc/systemd/system/multi-user.target.wants/nginx.service \u2192 "
                              "/usr/lib/systemd/system/nginx.service.\n")
        self.assertEqual(run("systemctl is-active nginx")[0], "active\n")
        self.assertEqual({p["USERNAME"] for p in run.protocol.process if "nginx: worker" in p["COMMAND"]}, {"nginx"})
        self.assertTrue(any(line.startswith("nginx:x:") for line in
                            run.protocol.fs.file_contents("/etc/passwd").decode().splitlines()))
        out = run("dnf remove -y nginx")[0]
        self.assertIn('Removed "/etc/systemd/system/multi-user.target.wants/nginx.service".\n', out)
        self.assertIn("could not be found", run("systemctl status nginx")[1])


if __name__ == "__main__":
    unittest.main()


class StopStatusTest(PackageSession):
    """Fix round 2: the status of a stopped daemon names the processes of its last run as systemd does (refs t12
    debian-12-t12-after-remove-nginx, almalinux-9-t12-after-remove-tuned, *-t12r2-start-without-binary)."""

    STOP = ("ExecStop=/sbin/start-stop-daemon --quiet --stop --retry QUIT/5 --pidfile /run/nginx.pid "
            "(code=exited, status=0/SUCCESS)")

    def test_bookworm_names_the_stop_and_the_main_process(self):
        run = self.session("debian-bookworm")
        run("apt-get install -y nginx")
        run("systemctl stop nginx")
        status = run("systemctl status nginx --no-pager -n0")[0]
        self.assertRegex(status, r"\n   Duration: \d+ms\n       Docs: man:nginx\(8\)\n    Process: \d+ "
                         + re.escape(self.STOP) + r"\n   Main PID: \d+ \(code=exited, status=0/SUCCESS\)\n")
        self.assertNotIn("signal=TERM", status)
        self.assertNotIn("ExecStart=", status)

    def test_noble_names_every_process_of_the_run(self):
        run = self.session("ubuntu-noble")
        run("apt-get install -y nginx")
        run("systemctl stop nginx")
        status = run("systemctl status nginx --no-pager -n0")[0]
        lines = [line for line in status.splitlines() if line.startswith(("    Process:", "   Main PID:"))]
        self.assertEqual(len(lines), 4, status)
        self.assertRegex(lines[0], r"^    Process: \d+ ExecStartPre=/usr/sbin/nginx -t -q -g daemon on; master_process on; "
                                   r"\(code=exited, status=0/SUCCESS\)$")
        self.assertRegex(lines[1], r"^    Process: \d+ ExecStart=/usr/sbin/nginx -g daemon on; master_process on; "
                                   r"\(code=exited, status=0/SUCCESS\)$")
        self.assertRegex(lines[2], r"^    Process: \d+ " + re.escape(self.STOP) + "$")
        self.assertRegex(lines[3], r"^   Main PID: \d+ \(code=exited, status=0/SUCCESS\)$")
        pids = [int(line.split()[1 if "Process" in line else 2]) for line in lines]
        self.assertEqual(pids[:2], sorted(pids[:2]))
        self.assertLess(pids[1], pids[3])
        self.assertGreater(pids[2], pids[3])

    def test_a_persona_daemon_exits_cleanly(self):
        run = self.session("rhel-9")
        run("systemctl stop tuned")
        status = run("systemctl status tuned --no-pager -n0")[0]
        hit = re.search(r"\n    Process: (\d+) ExecStart=/usr/sbin/tuned -l -P \(code=exited, status=0/SUCCESS\)\n"
                        r"   Main PID: (\d+) \(code=exited, status=0/SUCCESS\)\n", status)
        self.assertIsNotNone(hit, status)
        self.assertEqual(hit.group(1), hit.group(2))
        # it ran since the boot (ref almalinux-9-t12-after-remove-tuned: Duration is the time of that run)
        self.assertNotRegex(status, r"\n   Duration: \d+(\.\d+)?m?s\n")
        self.assertRegex(status, r"\n   Duration: .*\b(\d+h|\d+ days?|\d+min)\b")
        # and keeps the CPU time its processes had (ps TIME)
        self.assertNotIn("\n        CPU: 0ms\n", status)

    def test_a_failed_start_keeps_the_duration_of_the_last_run(self):
        run = self.session("debian-bookworm")
        run("apt-get install -y nginx")
        run("systemctl stop nginx")
        before = re.search(r"\n   Duration: (.+)\n", run("systemctl status nginx --no-pager -n0")[0]).group(1)
        import time
        from unittest import mock
        later = time.time() + 75
        with mock.patch("time.time", return_value=later):
            run("apt-get remove -y nginx")
            run("systemctl start nginx")
            after = re.search(r"\n   Duration: (.+)\n", run("systemctl status nginx --no-pager -n0")[0])
        self.assertIsNotNone(after)
        self.assertEqual(after.group(1), before)


class CentosMariadbTest(PackageSession):
    """Fix round 2: CentOS 7's mysqld_safe is the main process by its script's name, only mysqld holds 3306."""

    def test_the_main_process_and_the_socket_holder(self):
        run = self.session("centos-7")
        run("yum install -y mariadb-server")
        self.assertEqual(run("systemctl start mariadb")[2], 0)
        status = run("systemctl status mariadb --no-pager -n0")[0]
        self.assertRegex(status, r"\n +Main PID: \d+ \(mysqld_safe\)\n", status)
        mysqld = [p for p in run.protocol.process if str(p["COMMAND"]).startswith("/usr/libexec/mysqld ")]
        self.assertEqual(len(mysqld), 1)
        server = getattr(getattr(run.protocol, "user", None), "server", None)
        holder = getattr(server, "tpot_systemd", None) or getattr(run.protocol, "tpot_systemd")
        sockets = [s for s in holder["sockets"] if s.get("port") == 3306]
        self.assertEqual(len(sockets), 1, sockets)
        self.assertEqual((sockets[0]["comm"], sockets[0]["pid"], sockets[0]["also"]), ("mysqld", mysqld[0]["PID"], []))


class DnfRemoveTest(PackageSession):
    """Fix round 2: dnf removes the packages that require what it removes and the dependencies nothing needs any more
    (ref almalinux-9-t12r3-remove-nginx-core, -after-remove-nginx-core)."""

    def test_removing_nginx_core_takes_nginx_and_its_unit(self):
        run = self.session("rhel-9")
        run("dnf install -y nginx")
        run("systemctl start nginx")
        self.assertEqual(run("systemctl is-active nginx")[0], "active\n")
        out = run("dnf remove -y nginx-core")[0]
        self.assertRegex(out, r"\nRemoving:\n nginx-core +x86_64 ")
        self.assertRegex(out, r"\nRemoving dependent packages:\n nginx +x86_64 ")
        unused = out.split("Removing unused dependencies:\n", 1)[1].split("\n\n", 1)[0]
        self.assertEqual([line.split()[0] for line in unused.splitlines() if re.match(r" \S", line)],
                         ["nginx-filesystem", "redhat-logos-httpd"] + (["logrotate"] if "logrotate" in unused else []))
        self.assertRegex(out, r"\nRemove +[45] Packages\n")
        erasing = [line.split(":", 1)[1].split()[0] for line in out.splitlines() if line.startswith("  Erasing ")]
        self.assertTrue(erasing[0].startswith("nginx-1:"), erasing)
        self.assertEqual(run("rpm -q nginx")[0], "package nginx is not installed\n")
        self.assertEqual(run("systemctl is-active nginx")[0], "inactive\n")
        self.assertFalse(any("nginx" in str(p["COMMAND"]) for p in run.protocol.process))


class InstalledDependencyTest(PackageSession):
    """Final wave: apt adds the packages a service package brings along only for a package it installs; an installed
    package is the newest version and installs nothing (ref d12-08-install-tree-again)."""

    def test_an_installed_service_package_installs_nothing(self):
        for pid, names in (("debian-bookworm", ("openssh-server", "cron")),
                           ("ubuntu-noble", ("openssh-server", "cron", "ca-certificates"))):
            for name in names:
                with self.subTest(pid=pid, name=name):
                    run = self.session(pid)
                    version = system.apt_packages(catalog.by_id(pid), run.ident)["installed"][name]
                    out, err, code = run(f"apt-get install -y {name}")
                    self.assertEqual(code, 0, err)
                    self.assertIn(f"{name} is already the newest version ({version}).\n", out)
                    self.assertRegex(out, r"\n0 upgraded, 0 newly installed, 0 to remove and \d+ not upgraded\.\n$")
                    self.assertNotIn("Get:", out)
                    out = run(f"apt-get install -s {name}")[0]
                    self.assertNotIn("Inst ", out)

    def test_a_reinstall_reinstalls_only_the_package(self):
        run = self.session("ubuntu-noble")
        out, err, code = run("apt-get install -y --reinstall openssh-server")
        self.assertEqual(code, 0, err)
        self.assertIn("0 upgraded, 0 newly installed, 1 reinstalled, 0 to remove and ", out)
        self.assertNotIn("NEW packages", out)
        self.assertEqual(len([line for line in out.splitlines() if line.startswith("Get:")]), 1)
        self.assertNotIn("Creating config file", out)

    def test_a_fresh_install_brings_its_packages_along(self):
        run = self.session("debian-bookworm")
        out = run("apt-get install -y nginx")[0]
        self.assertIn("The following additional packages will be installed:\n  nginx-common\n", out)
