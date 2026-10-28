"""Final wave: the daemons of a device's ps list are programs of its tree (BusyBox applets as links to busybox
where BusyBox installs them), and its telnet session is served by a telnetd of that tree ([shell] telnet_daemon)."""
import json
import unittest

from tests import test_net_tools
from tests.test_render import cfg_of, fs_of, render_one, skipped
from tpot_personas import catalog, devices, firmware, system, tree, validate
from tpot_personas.identity import draw


def node(fs, path):
    return tree.find_node(fs, tree.real_path(fs, path.lstrip("/")) or path.lstrip("/"))


class TreeTest(unittest.TestCase):
    def rendered(self, pid):
        _, out, _ = render_one(self, catalog.by_id(pid), 1)
        return out, fs_of(out)

    def assert_applet(self, fs, path):
        found = node(fs, path)
        self.assertIsNotNone(found, path)
        self.assertEqual(found[tree.A_TYPE], tree.T_LINK, path)
        # a link to busybox: as the daemons place it (/bin/busybox) or as `busybox --install -s` does
        #   (../../bin/busybox, system.applet_links)
        self.assertIn(found[tree.A_TARGET], ("/bin/busybox", "../../bin/busybox", "../bin/busybox", "busybox"), path)
        self.assertIs(tree.resolve(fs, path.lstrip("/")), tree.resolve(fs, "bin/busybox"), path)

    def assert_program(self, fs, path):
        found = tree.resolve(fs, path.lstrip("/"))
        self.assertIsNotNone(found, path)
        self.assertEqual(found[tree.A_TYPE], tree.T_FILE, path)
        self.assertTrue(found[tree.A_MODE] & 0o111, path)

    def test_the_daemons_of_the_dir859_and_the_camera(self):
        out, fs = self.rendered("dlink-dir859")
        # the BusyBox of the DIR-859 1.05B03 has no telnetd applet (its `busybox` list), so the telnetd of
        #   `telnetd -i br0` is a program of its own
        self.assert_program(fs, "/usr/sbin/telnetd")
        self.assert_program(fs, "/usr/sbin/xmldb")
        out, fs = self.rendered("ipcam")
        # /etc/init.d/rcS: /usr/sbin/telnetd, /sbin/dropbear -R
        self.assert_applet(fs, "/usr/sbin/telnetd")
        self.assert_program(fs, "/sbin/dropbear")
        self.assertIn("/sbin/dropbear -R", (out / "honeyfs/etc/init.d/rcS").read_text())

    def test_dlink_has_no_ssh_profile_and_no_dropbear_process(self):
        # the 1.05B03 image ships telnetd only (catalog): no SSH server in the catalog, in ps, in the tree or
        #   in netstat; the SSH side of Cowrie closes the connection without a banner ([ssh] drop_connections)
        persona = catalog.by_id("dlink-dir859")
        self.assertIsNone(persona.ssh_profile)
        for release in persona.releases:
            self.assertEqual((release.ssh_banner, release.ssh_version), ("", ""), release.label)
        out, fs = self.rendered("dlink-dir859")
        processes = json.loads((out / "cmdoutput.json").read_text())["command"]["ps"]
        self.assertEqual([p["COMMAND"] for p in processes if "dropbear" in p["COMMAND"]], [])
        self.assertIn("telnetd -i br0", [p["COMMAND"] for p in processes])
        # no SSH server and no ssh client: the shell says "-sh: ssh: not found"
        for path in ("usr/sbin/dropbear", "usr/sbin/sshd", "etc/ssh", "usr/bin/ssh", "bin/ssh"):
            self.assertIsNone(tree.resolve(fs, path), path)
        self.assertTrue({"ssh", "/bin/ssh", "/usr/bin/ssh"} <= skipped(out))
        # no privilege separation account of an sshd
        for rel in ("etc/passwd", "etc/group", "etc/shadow"):
            self.assertNotIn("sshd:", (out / "honeyfs" / rel).read_text(), rel)
        self.assert_program(fs, "/usr/sbin/telnetd")
        network = json.loads((out / "network.json").read_text())
        self.assertEqual([s for s in network["sockets"] if s["port"] == 22], [])
        cfg = cfg_of(out)
        self.assertEqual(cfg.get("ssh", "drop_connections"), "true")
        self.assertEqual(cfg.get("ssh", "listen_endpoints"), "tcp:22:interface=0.0.0.0")
        # [ssh] names no server: only the listener, the drop and the one key Cowrie's factory starts with
        self.assertEqual(set(cfg.options("ssh")) - set(cfg.defaults()),
                         {"enabled", "drop_connections", "ed25519_public_key", "ed25519_private_key",
                          "public_key_auth", "listen_endpoints"})
        self.assertEqual(cfg.get("shell", "ssh_version"), "")
        self.assertEqual(cfg.get("telnet", "enabled"), "true")
        found = []
        validate._check_device_programs(persona, draw(persona, 1), out, fs, found)
        self.assertEqual(found, [])

    def test_a_device_with_an_ssh_server_keeps_its_sshd_account(self):
        out, _ = self.rendered("ipcam")
        for rel in ("etc/passwd", "etc/group", "etc/shadow"):
            self.assertIn("\nsshd:", (out / "honeyfs" / rel).read_text(), rel)
        self.assertNotIn("ssh", skipped(out))

    def test_every_device_has_the_programs_of_its_list(self):
        for persona in catalog.PERSONAS:
            if persona.kind != "device":
                continue
            with self.subTest(persona=persona.id):
                out, fs = self.rendered(persona.id)
                processes = json.loads((out / "cmdoutput.json").read_text())["command"]["ps"]
                found = []
                validate._check_device_programs(persona, draw(persona, 1), out, fs, found)
                self.assertEqual(found, [])
                self.assertGreater(len(processes), 2)

    def test_the_telnetd_of_a_session(self):
        # the NAS326's BusyBox has the telnetd applet (in /sbin of its initramfs), QNAP's busybox-1.24.1 has not
        #   (their `busybox --list`)
        for pid, applet, path in (("zyxel-nas326", True, "/sbin/telnetd"), ("qnap-qts", False, "/usr/sbin/telnetd"),
                                  ("openwrt", False, "/usr/sbin/telnetd"), ("tplink-archer-c7", False, "/usr/sbin/telnetd"),
                                  ("synology-dsm", False, "/usr/sbin/telnetd")):
            with self.subTest(persona=pid):
                out, fs = self.rendered(pid)
                self.assertEqual(cfg_of(out).get("shell", "telnet_daemon"), path)
                if applet:
                    self.assert_applet(fs, path)
                else:
                    self.assert_program(fs, path)
        out, _ = self.rendered("debian-bookworm")
        self.assertFalse(cfg_of(out).has_option("shell", "telnet_daemon"))

    def test_every_ps_program_of_a_device_is_a_node_of_its_tree(self):
        # every program of a device's ps list and the telnetd of its telnet sessions resolve at exactly their path
        #   (a bare name: the first folder of PATH that has it); a link to busybox only for an applet of its BusyBox
        folders = ("sbin", "bin", "usr/sbin", "usr/bin")
        for persona in catalog.PERSONAS:
            if persona.kind != "device":
                continue
            for seed in range(2):
                with self.subTest(persona=persona.id, seed=seed):
                    _, out, _ = render_one(self, persona, seed)
                    fs = fs_of(out)
                    ident = draw(persona, seed)
                    applets = set(system.busybox_applets(persona, ident)) | {"busybox", "init"}
                    busybox = tree.resolve(fs, "bin/busybox")
                    processes = json.loads((out / "cmdoutput.json").read_text())["command"]["ps"]
                    programs = [devices.ps_program(p["COMMAND"]) for p in processes]
                    programs.append(devices.telnet_daemon(persona, ident))
                    for program in filter(None, programs):
                        paths = [program.lstrip("/")] if program.startswith("/") else \
                            [f"{folder}/{program}" for folder in folders]
                        path = next((rel for rel in paths if tree.resolve(fs, rel) is not None), None)
                        self.assertIsNotNone(path, program)
                        found = tree.resolve(fs, path)
                        self.assertEqual(found[tree.A_TYPE], tree.T_FILE, program)
                        self.assertTrue(found[tree.A_MODE] & 0o111, program)
                        if busybox is not None and found is busybox:
                            self.assertIn(path.rsplit("/", 1)[-1], applets, program)

    def test_the_daemons_of_the_nas326_are_programs_of_its_firmware(self):
        # its ps list names the programs where its firmware has them (sshd of the system image in /sbin, smbd,
        #   nmbd, httpd and syslog-ng in /usr/sbin), and nothing is added to its folders beside them
        persona = catalog.by_id("zyxel-nas326")
        ident = draw(persona, 1)
        out, fs = self.rendered("zyxel-nas326")
        table = firmware.programs(persona, ident)
        processes = json.loads((out / "cmdoutput.json").read_text())["command"]["ps"]
        programs = {devices.ps_program(p["COMMAND"]) for p in processes} - {None, "init"}
        self.assertEqual(programs, {"/usr/sbin/syslog-ng", "/sbin/sshd", "/usr/sbin/httpd", "/usr/sbin/smbd",
                                    "/usr/sbin/nmbd"})
        for program in programs:
            self.assertIn(program.lstrip("/"), table, program)
            self.assert_program(fs, program)
        self.assertEqual(devices.telnet_daemon(persona, ident), "/sbin/telnetd")
        for path in devices.daemon_programs(persona, ident, processes):
            self.assertIn(path.lstrip("/"), table, path)

    def test_the_homes_of_the_applets_are_those_of_its_busybox(self):
        # only the applets of the persona's own BusyBox have a home: the Archer C7 and OpenWrt have no telnetd
        #   applet, so their telnet daemon is the standalone SESSION_TELNETD, not an applet's folder
        for persona in catalog.PERSONAS:
            if persona.kind != "device":
                continue
            ident = draw(persona, 1)
            applets = set(system.busybox_applets(persona, ident)) if ident.release.extra.get("busybox") else set()
            with self.subTest(persona=persona.id):
                self.assertLessEqual(set(devices._applet_homes(persona, applets)), applets)
        for pid in ("tplink-archer-c7", "openwrt"):
            persona = catalog.by_id(pid)
            ident = draw(persona, 1)
            self.assertNotIn("telnetd", devices._applet_homes(persona, set(system.busybox_applets(persona, ident))))
            self.assertEqual(devices.telnet_daemon(persona, ident), devices.SESSION_TELNETD)

    def test_the_telnetd_of_the_c7_and_openwrt_is_a_program_of_their_own(self):
        # their BusyBox has no telnetd applet and the images no telnet daemon: T-Pot's Telnet is served by a
        #   standalone program of the persona at /usr/sbin/telnetd (a deviation on purpose, catalog)
        for pid in ("tplink-archer-c7", "openwrt"):
            with self.subTest(persona=pid):
                out, fs = self.rendered(pid)
                self.assertEqual(cfg_of(out).get("shell", "telnet_daemon"), "/usr/sbin/telnetd")
                found = tree.find_node(fs, tree.real_path(fs, "usr/sbin/telnetd"))
                self.assertEqual(found[tree.A_TYPE], tree.T_FILE)
                self.assertNotIn("telnetd", system.busybox_applets(catalog.by_id(pid), draw(catalog.by_id(pid), 1)))

    def test_the_validator_finds_an_applet_link_busybox_has_not(self):
        persona = catalog.by_id("openwrt")
        out, fs = self.rendered("openwrt")
        tree.remove_node(fs, tree.real_path(fs, "usr/sbin/telnetd"))
        tree.ensure_link(fs, tree.real_path(fs, "usr/sbin/telnetd"), "/bin/busybox", ctime=1)
        found = []
        validate._check_device_programs(persona, draw(persona, 1), out, fs, found)
        self.assertEqual(found, ["programs: /usr/sbin/telnetd is a link to busybox, which has no telnetd applet"])

    def test_the_validator_finds_a_missing_daemon(self):
        persona = catalog.by_id("dlink-dir859")
        out, fs = self.rendered("dlink-dir859")
        tree.remove_node(fs, "usr/sbin/xmldb")
        found = []
        validate._check_device_programs(persona, draw(persona, 1), out, fs, found)
        self.assertEqual(found, ["programs: xmldb of 'xmldb -n wrgac37_dlink.2013gui_dir859 -t' is not in the tree"])
        cfg = (out / "cowrie.cfg").read_text().replace("telnet_daemon = /usr/sbin/telnetd\n",
                                                       "telnet_daemon = /usr/sbin/utelnetd\n")
        (out / "cowrie.cfg").write_text(cfg)
        found = []
        validate._check_device_programs(persona, draw(persona, 1), out, fs_of(out), found)
        self.assertEqual(found, ["cfg telnet_daemon: '/usr/sbin/utelnetd' is not the telnetd of dlink-dir859"])


class SessionTest(unittest.TestCase):
    session_persona = test_net_tools.NetToolsTest.session_persona
    run_cmd = test_net_tools.NetToolsTest.run_cmd
    estab = test_net_tools.NetToolsTest.estab

    def test_a_device_without_a_listed_telnetd_takes_its_telnet_daemon(self):
        data, session, keys, _ = self.session_persona("synology-dsm", telnet=True)
        keys.pop("top_style")   # DSM's top is procps
        keys["telnet_daemon"] = "/usr/sbin/telnetd"
        row = self.estab(data, session, keys)
        self.assertEqual(len(row), 1, row)
        pid = session.tpot_session_pid
        self.assertIn(f'users:(("telnetd",pid={pid},', row[0])
        self.assertNotIn("in.telnetd", row[0])


if __name__ == "__main__":
    unittest.main()
