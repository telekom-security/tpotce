"""The modes tpotinit gives the data folder (docker/tpotinit/dist/bin/data_modes.sh): folders 770,
files 660, nginx conf and cert 774 / 664, links neither followed nor changed. The scripts run
busybox chmod in the tpotinit image; the smoke test docker/_tests/tests/tpotinit_modes.sh checks
them there, these tests run them with the chmod of this host where it reads X the same way."""
import re
import sys
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "docker" / "tpotinit" / "dist" / "bin"
HELPER = BIN / "data_modes.sh"
CLEAN = BIN / "clean.sh"
ENTRYPOINT = ROOT / "docker" / "tpotinit" / "dist" / "entrypoint.sh"
UPDATEIP = BIN / "updateip.sh"


def chmod_takes_x_from_the_new_mode():
    """GNU and busybox chmod: u=rw,g=rw,o=,ug+X leaves a file without x. BSD chmod (macOS)
    takes X from the old mode, so the behaviour tests only run where it matches tpotinit."""
    with tempfile.TemporaryDirectory() as folder:
        probe = Path(folder) / "probe"
        probe.write_text("inert")
        probe.chmod(0o770)
        result = subprocess.run(["chmod", "u=rw,g=rw,o=,ug+X", str(probe)], capture_output=True)
        return result.returncode == 0 and probe.stat().st_mode & 0o777 == 0o660


def mode(path):
    return path.lstat().st_mode & 0o777


class ScriptsTest(unittest.TestCase):
    """No script of tpotinit gives the data folder an execute bit on files any more."""

    def test_no_recursive_numeric_mode(self):
        for path in (CLEAN, ENTRYPOINT, UPDATEIP):
            text = path.read_text()
            with self.subTest(path=path.name):
                self.assertNotRegex(text, r"chmod\s+(-R\s+)?7[0-7]{2}\s+\S+\s+-R")
                self.assertNotRegex(text, r"chmod\s+-R\s+7[0-7]{2}\b")
                self.assertNotRegex(text, r"chmod\s+7[0-7]{2}\s+-R\b")
                self.assertNotIn("-exec chmod", text)

    def test_the_scripts_use_the_helper(self):
        self.assertIn('. "$(dirname "${BASH_SOURCE[0]}")/data_modes.sh"', CLEAN.read_text())
        self.assertIn(". /opt/tpot/bin/data_modes.sh", ENTRYPOINT.read_text())
        function = re.search(r"^update_permissions\(\) \{.*?^\}", ENTRYPOINT.read_text(), re.M | re.S).group()
        self.assertIn("fuDATA_MODES /data\n", function)
        self.assertIn("fuDATA_WEB_MODES /data/nginx/conf /data/nginx/cert", function)

    def test_every_service_folder_gets_the_modes(self):
        """Every fuSERVICE of clean.sh that makes folders under /data also sets their modes."""
        text = CLEAN.read_text()
        for name, body in re.findall(r"^(fu[A-Z0-9]+) \(\) \{(.*?)^\}", text, re.M | re.S):
            if "mkdir -vp /data/" not in body:
                continue
            with self.subTest(function=name):
                self.assertRegex(body, r"fuDATA_(WEB_)?MODES /data/")

    def test_archives_and_ews_ip_are_plain_files(self):
        text = CLEAN.read_text()
        call = re.search(r'^python3 -c "\$\{myARCHIVE_MODES\}" tpot:tpot (.*)$', text, re.M)
        self.assertIsNotNone(call)
        archives = re.findall(r"^  local (my[A-Z0-9]+TGZ)=", text, re.M)
        self.assertEqual(call.group(1).split(), [f"${name}" for name in archives])
        self.assertNotIn("chmod 660 $myADBHONEYTGZ", text)
        self.assertNotIn("chown tpot:tpot $myADBHONEYTGZ", text)
        updateip = UPDATEIP.read_text()
        # through a descriptor, no link followed
        self.assertIn("os.O_NOFOLLOW", updateip)
        self.assertIn("os.fchmod(handle, 0o660)", updateip)
        self.assertNotIn("\nchmod 660 /data/ews/conf/ews.ip", updateip)
        self.assertNotIn("\nchown tpot:tpot /data/ews/conf/ews.ip", updateip)

    def test_updateip_writes_in_place_and_follows_no_link(self):
        """ews.ip and elk_environment are written in place (ews.ip is bind mounted) through an
        O_NOFOLLOW descriptor: the inode stays, a planted link is refused and its target unchanged."""
        text = UPDATEIP.read_text()
        code = re.search(r"^myWRITE_IN_PLACE='(.*?)'$", text, re.M | re.S).group(1)
        self.assertNotIn("\ntee /data/", text)
        with tempfile.TemporaryDirectory() as folder:
            path, target, link = (os.path.join(folder, name) for name in ("ews.ip", "target", "elk_environment"))
            with open(path, "w", encoding="utf-8") as out:
                out.write("[MAIN]\nip = 192.0.2.1 and more text than the new one\n")
            inode = os.stat(path).st_ino
            with open(target, "w", encoding="utf-8") as out:
                out.write("keep\n")
            os.symlink(target, link)
            result = subprocess.run([sys.executable, "-c", code, path], input=b"[MAIN]\nip = 198.51.100.7\n",
                                    capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "[MAIN]\nip = 198.51.100.7\n")
            self.assertEqual(os.stat(path).st_ino, inode)
            result = subprocess.run([sys.executable, "-c", code, link], input=b"MY_EXTIP=x\n", capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            with open(target, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "keep\n")
            new = os.path.join(folder, "new")
            subprocess.run([sys.executable, "-c", code, new], input=b"x\n", check=True)
            self.assertEqual(oct(os.stat(new).st_mode & 0o777), oct(0o660 & ~self.umask()))

    def test_updateip_names_the_running_version(self):
        """The login banner takes TPOT_VERSION of the environment (the image tag of .env), no number
        of its own; only the characters of a tag go in, without one it says T-Pot alone."""
        text = UPDATEIP.read_text()
        self.assertIsNone(re.search(r"T-Pot \d", text))
        version = re.search(r'^myVERSION=.*$', text, re.M).group(0)
        title = re.search(r'^echo "T-Pot.*" >> /tmp/etc/issue$', text, re.M).group(0)
        script = version + "\n" + title.replace(" >> /tmp/etc/issue", "")
        for value, shown in (("24.04.2", "T-Pot 24.04.2"), ("", "T-Pot"), ("1.2\\n$(x)`y`", "T-Pot 1.2nxy")):
            with self.subTest(value=value):
                environ = {key: item for key, item in os.environ.items() if key != "TPOT_VERSION"}
                if value:
                    environ["TPOT_VERSION"] = value
                done = subprocess.run(["bash", "-c", script], env=environ, capture_output=True, text=True)
                self.assertEqual((done.returncode, done.stdout), (0, shown + "\n"), done.stderr)

    @staticmethod
    def umask():
        mask = os.umask(0)
        os.umask(mask)
        return mask

    def test_the_archive_modes_follow_no_link(self):
        """The archives that are there get 660 and the owner; a missing one says nothing, a link in
        place of one is left alone and so is its target."""
        import getpass
        code = re.search(r"^myARCHIVE_MODES='(.*?)'$", CLEAN.read_text(), re.M | re.S).group(1)
        with tempfile.TemporaryDirectory() as folder:
            archive, target, link = (os.path.join(folder, name) for name in ("a.tgz", "target", "b.tgz"))
            for path in (archive, target):
                with open(path, "w", encoding="utf-8") as out:
                    out.write("x")
                os.chmod(path, 0o644)
            os.symlink(target, link)
            import grp
            owner = f"{getpass.getuser()}:{grp.getgrgid(os.getgid()).gr_name}"
            result = subprocess.run([sys.executable, "-c", code, owner, archive, link,
                                     os.path.join(folder, "missing.tgz")], capture_output=True, text=True)
            self.assertEqual((result.returncode, result.stderr), (0, ""))
            self.assertEqual(oct(os.stat(archive).st_mode & 0o777), "0o660")
            self.assertEqual(oct(os.stat(target).st_mode & 0o777), "0o644")
            self.assertTrue(os.path.islink(link))


@unittest.skipUnless(chmod_takes_x_from_the_new_mode(),
                     "the chmod of this host takes X from the old mode (BSD); "
                     "./docker/_tests/run.sh tpotinit_modes checks it in the tpotinit image")
class HelperTest(unittest.TestCase):

    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.base)])
        self.outside = self.base / "outside"
        (self.outside / "folder").mkdir(parents=True)
        (self.outside / "file").write_text("inert")
        (self.outside / "folder" / "file").write_text("inert")
        (self.outside / "file").chmod(0o705)
        (self.outside / "folder" / "file").chmod(0o705)
        (self.outside / "folder").chmod(0o751)

    def tree(self, root):
        (root / "sub" / "deeper").mkdir(parents=True)
        for name, bits in (("run", 0o777), ("sub/plain", 0o755), ("sub/deeper/data", 0o644)):
            (root / name).write_text("inert")
            (root / name).chmod(bits)
        (root / "sub").chmod(0o755)
        (root / "sub" / "deeper").chmod(0o700)
        (root / "sub" / "to-file").symlink_to(self.outside / "file")
        (root / "sub" / "to-folder").symlink_to(self.outside / "folder")
        return root

    def run_helper(self, command):
        return subprocess.run(["bash", "-c", f'. "{HELPER}"\n{command}'], capture_output=True, text=True, check=True)

    def assert_outside_unchanged(self, root):
        self.assertEqual(mode(self.outside / "file"), 0o705)
        self.assertEqual(mode(self.outside / "folder"), 0o751)
        self.assertEqual(mode(self.outside / "folder" / "file"), 0o705)
        self.assertTrue((root / "sub" / "to-file").is_symlink())
        self.assertTrue((root / "sub" / "to-folder").is_symlink())

    def test_files_660_folders_770(self):
        root = self.tree(self.base / "data")
        self.run_helper(f'fuDATA_MODES "{root}"')
        for name in ("run", "sub/plain", "sub/deeper/data"):
            self.assertEqual(mode(root / name), 0o660, name)
        for folder in (root, root / "sub", root / "sub" / "deeper"):
            self.assertEqual(mode(folder), 0o770, folder)
        self.assert_outside_unchanged(root)

    def test_web_files_664_folders_774(self):
        root = self.tree(self.base / "conf")
        self.run_helper(f'fuDATA_WEB_MODES "{root}/"')
        for name in ("run", "sub/plain", "sub/deeper/data"):
            self.assertEqual(mode(root / name), 0o664, name)
        for folder in (root, root / "sub", root / "sub" / "deeper"):
            self.assertEqual(mode(folder), 0o774, folder)
        self.assert_outside_unchanged(root)

    def test_a_link_given_as_path_is_not_followed(self):
        link = self.base / "top"
        link.symlink_to(self.outside / "folder")
        result = self.run_helper(f'fuDATA_MODES "{link}/"')
        self.assertIn(f"# Warning: {link} is a link, its modes stay as they are.", result.stdout)
        self.assertEqual(mode(self.outside / "folder"), 0o751)
        self.assertEqual(mode(self.outside / "folder" / "file"), 0o705)


if __name__ == "__main__":
    unittest.main()



class PersistenceTest(unittest.TestCase):
    """TPOT_PERSISTENCE=off deletes what the honeypots collected (logs, downloads, payloads, captures and
    the archives of fuLOGROTATE) at every start, the identity and state of a service stays: host keys,
    TLS certificates, the values of an installation, persona state, the rules cache. Every fuSERVICE of
    clean.sh runs on a temporary tree in place of /data, first with on (it makes its folders, a file goes
    into each), then with off."""

    # per folder of /data: the folders that stay with off (all others are emptied)
    KEPT = {
        "beelzebub": {"key"}, "ciscoasa": {"identity"}, "conpot": {"identity"}, "cowrie": {"keys"},
        "dicompot": {"images"}, "elk": {"data", "listbot"}, "endlessh": {"identity"}, "galah": {"cert"},
        "miniprint": {"data"}, "nginx": {"cert", "conf"}, "rdphoneypot": {"cert"}, "suricata": {"rules"},
    }
    # names of folders that hold an identity or state: one of them that a function makes has to stay
    STATE_NAMES = {"identity", "key", "keys", "cert", "certs", "data", "rules", "images", "conf", "listbot",
                   "state", "seed"}
    NOT_SERVICES = {"fuEMPTY", "fuREMOVE_ALL_BUT", "fuLOGROTATECONF", "fuLOGROTATE", "fuTPOTINIT"}

    def functions(self):
        text = CLEAN.read_text()
        found = {name: body for name, body in re.findall(r"^(fu[A-Z0-9]+) \(\) \{(.*?)^\}", text, re.M | re.S)}
        helper = re.search(r"^fuREMOVE_ALL_BUT \(\) \{.*?^\}", text, re.M | re.S).group()
        return helper, {name: body for name, body in found.items()
                        if name not in self.NOT_SERVICES and "myPERSISTENCE" in body}

    def run_function(self, helper, name, body, root, persistence):
        here = re.sub(r"(?<![\w/])/data(?=/|\s|$)", root, f"{name} () {{{body}}}")
        script = ("fuDATA_MODES () { :; }\nchown () { :; }\n" + helper + "\n" + here
                  + f"\nmyPERSISTENCE={persistence}\n{name}\n")
        result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_off_keeps_identity_and_state_on_keeps_all(self):
        helper, functions = self.functions()
        self.assertGreater(len(functions), 30)
        for name, body in sorted(functions.items()):
            service = re.search(r"mkdir -vp /data/([\w.-]+)", body).group(1)
            with self.subTest(function=name), tempfile.TemporaryDirectory() as root:
                folder = Path(root) / service
                self.run_function(helper, name, body, root, "on")
                made = sorted(path for path in folder.rglob("*") if path.is_dir())
                tops = {path.relative_to(folder).parts[0] for path in made}
                self.assertEqual(sorted(tops & self.STATE_NAMES - self.KEPT.get(service, set())), [],
                                 "a folder of identity or state that KEPT does not name")
                for path in [folder] + made:
                    (path / "sample").write_text("inert")
                (folder / "archive.tgz").write_text("inert")
                every = sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file())
                self.run_function(helper, name, body, root, "on")
                self.assertEqual(sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file()), every)
                self.run_function(helper, name, body, root, "off")
                # what the function makes itself (empty files of nginx and dionaea) does not count
                left = {p.relative_to(folder).parts[0] for p in folder.rglob("*")
                        if p.is_file() and p.name in ("sample", "archive.tgz")}
                kept = self.KEPT.get(service, set())
                if "fuREMOVE_ALL_BUT" in body or f"/data/{service}/*;" in body:
                    self.assertEqual(sorted(left), sorted(kept))
                else:
                    # a function that names what goes: its folders are emptied, the rest stays
                    self.assertEqual(sorted(left & tops), sorted(kept))
                for top in tops:
                    self.assertTrue((folder / top).is_dir(), top)

    def test_cowrie_keeps_the_persona_state(self):
        helper, functions = self.functions()
        with tempfile.TemporaryDirectory() as root:
            keys = Path(root) / "cowrie" / "keys"
            (keys / "ubuntu-noble").mkdir(parents=True)
            for item in ("persona-state.json", "ubuntu-noble/ssh_host_ed25519_key"):
                (keys / item).write_text("inert")
            self.run_function(helper, "fuCOWRIE", functions["fuCOWRIE"], root, "off")
            self.assertEqual(sorted(str(p.relative_to(keys)) for p in keys.rglob("*") if p.is_file()),
                             ["persona-state.json", "ubuntu-noble/ssh_host_ed25519_key"])

    def test_a_link_is_removed_not_followed(self):
        helper, functions = self.functions()
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            (Path(outside) / "file").write_text("inert")
            folder = Path(root) / "conpot"
            folder.mkdir()
            (folder / "log").symlink_to(outside)
            self.run_function(helper, "fuCONPOT", functions["fuCONPOT"], root, "off")
            self.assertTrue((Path(outside) / "file").exists())
            self.assertFalse((folder / "log").is_symlink())
