"""Final wave: the daemons of a device's ps list are programs of its tree (BusyBox applets as links to busybox
where BusyBox installs them), and its telnet session is served by a telnetd of that tree ([shell] telnet_daemon)."""
import json
import unittest

from tests import test_net_tools
from tests.test_render import cfg_of, fs_of, render_one
from tpot_personas import catalog, tree, validate
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
        self.assertIs(tree.resolve(fs, path.lstrip("/")), tree.find_node(fs, "usr/bin/busybox"), path)

    def assert_program(self, fs, path):
        found = tree.resolve(fs, path.lstrip("/"))
        self.assertIsNotNone(found, path)
        self.assertEqual(found[tree.A_TYPE], tree.T_FILE, path)
        self.assertTrue(found[tree.A_MODE] & 0o111, path)

    def test_the_daemons_of_the_dir859_and_the_camera(self):
        out, fs = self.rendered("dlink-dir859")
        # dropbear of the persona (a deviation of the catalog); the BusyBox of the DIR-859 1.05B03 has no telnetd
        #   applet (its `busybox` list), so the telnetd of `telnetd -i br0` is a program of its own
        self.assert_program(fs, "/usr/sbin/dropbear")
        self.assert_program(fs, "/usr/sbin/telnetd")
        self.assert_program(fs, "/usr/sbin/xmldb")
        out, fs = self.rendered("ipcam")
        # /etc/init.d/rcS: /usr/sbin/telnetd, /sbin/dropbear -R
        self.assert_applet(fs, "/usr/sbin/telnetd")
        self.assert_program(fs, "/sbin/dropbear")
        self.assertIn("/sbin/dropbear -R", (out / "honeyfs/etc/init.d/rcS").read_text())

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
        # the NAS326's BusyBox has the telnetd applet, QNAP's busybox-1.24.1 has not (their `busybox --list`)
        for pid, applet in (("zyxel-nas326", True), ("qnap-qts", False), ("openwrt", False),
                            ("tplink-archer-c7", False), ("synology-dsm", False)):
            with self.subTest(persona=pid):
                out, fs = self.rendered(pid)
                self.assertEqual(cfg_of(out).get("shell", "telnet_daemon"), "/usr/sbin/telnetd")
                if applet:
                    self.assert_applet(fs, "/usr/sbin/telnetd")
                else:
                    self.assert_program(fs, "/usr/sbin/telnetd")
        out, _ = self.rendered("debian-bookworm")
        self.assertFalse(cfg_of(out).has_option("shell", "telnet_daemon"))

    def test_the_validator_finds_a_missing_daemon(self):
        persona = catalog.by_id("dlink-dir859")
        out, fs = self.rendered("dlink-dir859")
        tree.remove_node(fs, "usr/sbin/dropbear")
        found = []
        validate._check_device_programs(persona, draw(persona, 1), out, fs, found)
        self.assertEqual(found, ["programs: /usr/sbin/dropbear of '/usr/sbin/dropbear -p 22' is not in the tree"])
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
