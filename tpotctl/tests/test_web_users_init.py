"""The passwd files tpotinit writes from WEB_USER / LS_WEB_USER (create_web_users of
docker/tpotinit/dist/entrypoint.sh): a line whose user the .env no longer names is kept in
<file>.lost, never twice, and the file is written in place. The functions run with the bash of
this host on a temporary data folder; base64 is a stub on PATH (the -w0 of GNU and busybox is
not there on macOS). The smoke test docker/_tests/tests/tpotinit_users.sh runs them in the image."""
import base64
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = ROOT / "docker" / "tpotinit" / "dist" / "entrypoint.sh"
FUNCTIONS = ("write_passwd_file", "create_web_users", "web_users_hint")

ALICE = "alice:$2y$05$aliceshashaliceshashaliceshashaliceshashaliceshash"
BOB = "bob:$apr1$bobsalt$bobshashbobshash"
OTTER = "sensor-otter:$2y$05$otterhashotterhashotterhashotterhashotterhash"
LYNX = "sensor-lynx:$apr1$lynxsalt$lynxhashlynxhash"


def entry(line):
    return base64.b64encode(line.encode()).decode()


def functions():
    text = ENTRYPOINT.read_text()
    found = []
    for name in FUNCTIONS:
        match = re.search(rf"^{name}\(\) \{{.*?^\}}", text, re.M | re.S)
        assert match, name
        found.append(match.group())
    return "\n".join(found)


class WebUsersInitTest(unittest.TestCase):

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.data = Path(folder.name) / "data"
        (self.data / "nginx" / "conf").mkdir(parents=True)
        self.nginx = self.data / "nginx" / "conf" / "nginxpasswd"
        self.lsweb = self.data / "nginx" / "conf" / "lswebpasswd"
        for path in (self.nginx, self.lsweb):
            path.write_text("")
        self.bin = Path(folder.name) / "bin"
        self.bin.mkdir()
        stub = self.bin / "base64"
        stub.write_text(f"#!/bin/sh\nexec {sys.executable} -c 'import base64,sys; "
                        f"sys.stdout.buffer.write(base64.b64decode(sys.stdin.buffer.read()))'\n")
        stub.chmod(0o755)

    def run_init(self, web="", lsweb="", kind="HIVE", command=None):
        script = functions() + "\n" + (command or f'create_web_users "{self.data}"') + "\n"
        environ = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", WEB_USER=web, LS_WEB_USER=lsweb,
                       TPOT_TYPE=kind)
        done = subprocess.run(["bash", "-c", script], env=environ, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def lost(self, path):
        lost = Path(f"{path}.lost")
        return lost.read_text() if lost.exists() else None

    def test_the_files_come_from_the_env(self):
        out = self.run_init(web=f"{entry(ALICE)} {entry(BOB + chr(10) + chr(10))}", lsweb=entry(OTTER))
        self.assertEqual(self.nginx.read_text(), f"{ALICE}\n{BOB}\n")
        self.assertEqual(self.lsweb.read_text(), f"{OTTER}\n")
        self.assertIsNone(self.lost(self.nginx))
        self.assertNotIn("Warning", out)

    def test_an_empty_key_keeps_the_lines_and_warns(self):
        self.lsweb.write_text(f"{OTTER}\n{LYNX}\n")
        inode = self.lsweb.stat().st_ino
        out = self.run_init(web=entry(ALICE))
        self.assertEqual(self.lsweb.read_text(), "")
        self.assertEqual(self.lsweb.stat().st_ino, inode, "lswebpasswd is written in place")
        self.assertEqual(self.lost(self.lsweb), f"{OTTER}\n{LYNX}\n")
        self.assertEqual(oct(Path(f"{self.lsweb}.lost").stat().st_mode & 0o777), "0o660")
        self.assertIn("# Warning: 2 sensor user(s) of lswebpasswd are not in LS_WEB_USER and lose access, they "
                      "are kept in lswebpasswd.lost. Take them back with: tpot sensors import", out)
        # the next start adds nothing, it names what is left there
        out = self.run_init(web=entry(ALICE))
        self.assertEqual(self.lost(self.lsweb), f"{OTTER}\n{LYNX}\n")
        self.assertNotIn("Warning", out)
        self.assertIn("# lswebpasswd.lost holds 2 sensor user(s) LS_WEB_USER does not name, take them back with: "
                      "tpot sensors import", out)

    def test_only_the_missing_name_is_kept(self):
        self.nginx.write_text(f"{ALICE}\n{BOB}\n")
        out = self.run_init(web=entry(ALICE))
        self.assertEqual(self.nginx.read_text(), f"{ALICE}\n")
        self.assertEqual(self.lost(self.nginx), f"{BOB}\n")
        self.assertIn("# Warning: 1 web user(s) of nginxpasswd are not in WEB_USER and lose access, they are kept "
                      "in nginxpasswd.lost. Take them back with: tpot users import", out)
        # a changed password of a name the .env still names is no loss
        self.nginx.write_text("alice:$apr1$older$hash\n")
        self.run_init(web=entry(ALICE))
        self.assertEqual(self.lost(self.nginx), f"{BOB}\n")

    def test_the_same_content_keeps_nothing(self):
        self.nginx.write_text(f"{ALICE}\n")
        out = self.run_init(web=entry(ALICE))
        self.assertIsNone(self.lost(self.nginx))
        self.assertEqual(out.count("#"), 1, out)      # only the heading

    def test_a_line_already_kept_is_not_added_again(self):
        Path(f"{self.nginx}.lost").write_text(f"{BOB}\n")
        self.nginx.write_text(f"{ALICE}\n{BOB}\n")
        out = self.run_init(web=entry(ALICE))
        self.assertEqual(self.lost(self.nginx), f"{BOB}\n")
        self.assertIn("nginxpasswd.lost holds 1 web user(s)", out)

    def test_a_sensor_keeps_nothing_and_says_nothing(self):
        self.nginx.write_text(f"{ALICE}\n")
        self.lsweb.write_text(f"{OTTER}\n")
        out = self.run_init(kind="SENSOR")
        self.assertEqual((self.nginx.read_text(), self.lsweb.read_text()), ("", ""))
        self.assertIsNone(self.lost(self.nginx))
        self.assertIsNone(self.lost(self.lsweb))
        self.assertNotIn("Warning", out)

    def test_a_link_in_place_of_the_lost_file_is_not_written(self):
        target = self.data / "target"
        target.write_text("keep\n")
        Path(f"{self.nginx}.lost").symlink_to(target)
        self.nginx.write_text(f"{BOB}\n")
        out = self.run_init(web=entry(ALICE))
        self.assertEqual(target.read_text(), "keep\n")
        self.assertIn("is a link, nothing is kept in it", out)

    def test_the_hint_before_the_abort(self):
        self.nginx.write_text(f"{ALICE}\n{BOB}\n")
        out = self.run_init(command=f'web_users_hint "{self.data}"')
        self.assertEqual(out, f"# {self.nginx} still holds 2 web user(s), take them over with: tpot users import\n")
        self.assertEqual(self.nginx.read_text(), f"{ALICE}\n{BOB}\n")
        self.nginx.write_text("")
        self.assertEqual(self.run_init(command=f'web_users_hint "{self.data}"'), "")

    def test_the_entrypoint_gives_the_hint_on_a_hive_without_web_user(self):
        text = ENTRYPOINT.read_text()
        report = text[text.index("if ! fuENV_REPORT;"):]
        report = report[:report.index('echo "# Aborting"')]
        self.assertIn('[ "${TPOT_TYPE}" != "SENSOR" ] && [ -z "${WEB_USER//[[:space:]]/}" ]', report)
        self.assertIn("web_users_hint /data", report)


if __name__ == "__main__":
    unittest.main()
