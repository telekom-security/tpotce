import configparser
import importlib.util
import json
import os
import random
import stat
import unittest
import unittest.mock
from pathlib import Path

from tests.helpers import DIST, base_tree, tmpdir
from tpot_personas import PersonaError, catalog, tree
from tpot_personas.ssh_profiles import PROFILES

DOCKERFILE = DIST.parent / "Dockerfile"
OLD_IDS = ("debian-bookworm-vuln", "fedora-36-vuln", "rhel-9-vuln", "openwrt-1806", "DEFAULT_BOOTSTRAP_PERSONA")


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, DIST / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_start():
    return load("start_cowrie_persona", "start-cowrie-persona.py")


class StartTest(unittest.TestCase):
    def setUp(self):
        self.start = load_start()
        self.cowrie = tmpdir(self) / "cowrie"
        (self.cowrie / "personas").mkdir(parents=True)
        tree.dump_pickle(base_tree(), self.cowrie / "personas" / "base.pickle")
        (self.cowrie / "etc").mkdir()
        self.old_key = self.cowrie / "etc" / "ssh_host_rsa_key"  # keys of an earlier T-Pot
        self.old_key.write_text("old key\n")
        os.chmod(self.old_key, 0o770)
        self.runtime = tmpdir(self) / "tmp-cowrie"

    def prepare(self, value, rng=None):
        return self.start.prepare({"COWRIE_PERSONA": value}, self.cowrie, self.runtime,
                                  rng or random.Random(4), min_pickle=0)

    def identity(self):
        return json.loads((self.runtime / "identity.json").read_text())

    def test_constants(self):
        self.assertEqual(self.start.TMP_ROOT, Path("/tmp/cowrie"))
        self.assertEqual(self.start.LIB, Path("/usr/local/lib/tpot-cowrie"))
        self.assertEqual(self.start.COWRIE_ROOT, Path("/home/cowrie/cowrie"))

    def test_selected_persona_is_rendered_and_recorded(self):
        pid = self.prepare("ipcam")
        self.assertEqual(pid, "ipcam")
        self.assertEqual((self.runtime / "persona").read_text(), "ipcam\n")
        self.assertEqual(self.identity()["persona"], "ipcam")
        self.assertEqual((self.runtime / "identity.json").read_text(),
                         (self.runtime / "runtime" / "identity.json").read_text())
        self.assertTrue((self.runtime / "runtime" / "cowrie.cfg").is_file())
        self.assertFalse((self.runtime / "runtime.new").exists())
        self.assertTrue((self.cowrie / "etc" / "ipcam").is_dir())
        self.assertTrue((self.runtime / "data").is_dir())

    def test_keys_of_an_earlier_tpot_stay_untouched(self):
        self.prepare("debian-bookworm")
        self.assertEqual(self.old_key.read_text(), "old key\n")
        self.assertEqual(stat.S_IMODE(self.old_key.stat().st_mode), 0o770)
        self.assertEqual(sorted(p.name for p in (self.cowrie / "etc").iterdir()),
                         ["debian-bookworm", "persona-state.json", "ssh_host_rsa_key"])

    def test_cfg_names_the_key_files_of_the_profile_in_the_persona_dir(self):
        for pid, kinds in (("openwrt", ("ed25519", "rsa")), ("ubuntu-noble", ("rsa", "ecdsa", "ed25519")),
                           ("tplink-wr841n", ("rsa",))):
            with self.subTest(pid):
                self.prepare(pid)
                cfg = configparser.ConfigParser(interpolation=None)
                cfg.read(self.runtime / "runtime" / "cowrie.cfg")
                keys_dir = self.cowrie / "etc" / pid
                self.assertEqual(cfg.get("honeypot", "etc_path"), str(keys_dir))
                self.assertEqual(cfg.get("ssh", "public_key_auth"),
                                 ",".join(PROFILES[catalog.by_id(pid).ssh_profile].host_keys))
                named = sorted(v for k, v in cfg.items("ssh") if k.endswith(("_public_key", "_private_key")))
                self.assertEqual(named, sorted(f"{keys_dir}/ssh_host_{kind}_key{ext}"
                                               for kind in kinds for ext in ("", ".pub")))

    def test_rendered_cfg_has_no_share_or_data_path(self):
        self.prepare("ubuntu-jammy")
        cfg = configparser.ConfigParser(interpolation=None)
        cfg.read(self.runtime / "runtime" / "cowrie.cfg")
        self.assertFalse(cfg.has_option("honeypot", "share_path"))
        self.assertFalse(cfg.has_option("honeypot", "data_path"))

    def test_identity_is_new_on_every_start(self):
        # the same selection rng twice: the identity seed must not come from it
        seeds = set()
        for _ in range(3):
            self.prepare("ubuntu-noble", random.Random(4))
            seeds.add(self.identity()["seed"])
        self.assertEqual(len(seeds), 3)

    def test_a_restart_replaces_the_runtime(self):
        self.prepare("ipcam")
        self.prepare("openwrt")
        self.assertEqual((self.runtime / "persona").read_text(), "openwrt\n")
        self.assertEqual(json.loads((self.runtime / "runtime" / "identity.json").read_text())["persona"], "openwrt")

    def test_empty_picks_from_all(self):
        self.assertIn(self.prepare(""), catalog.ids())

    def test_unknown_or_traversal_is_rejected_and_nothing_written(self):
        # "random" is no alias: tpotinit's choice_list rejects it too
        for value in ("nope", "../etc", "ipcam ipcam", "random"):
            with self.assertRaises(PersonaError):
                self.prepare(value)
        self.assertFalse((self.runtime / "runtime").exists())
        self.assertEqual(sorted(p.name for p in (self.cowrie / "etc").iterdir()), ["ssh_host_rsa_key"])

    def test_a_persona_that_fails_validation_is_not_activated(self):
        self.prepare("ipcam")
        with self.assertRaises(PersonaError):
            self.start.prepare({"COWRIE_PERSONA": "openwrt"}, self.cowrie, self.runtime, random.Random(4))
        # the pickle of the test tree is below MIN_PICKLE_SIZE: the previous rendering stays
        self.assertEqual((self.runtime / "persona").read_text(), "ipcam\n")
        self.assertFalse((self.runtime / "runtime.new").exists())
        self.assertFalse((self.cowrie / "etc" / "openwrt").exists())

    def test_main_execs_twistd_with_umask_0027(self):
        # private host keys and logs 0640 (dirs 0750): the log readers run as 2000:2000
        self.start.COWRIE_ROOT, self.start.TMP_ROOT = self.cowrie, self.runtime
        self.start.prepare = lambda *args, **kwargs: "ipcam"
        (self.runtime / "runtime").mkdir(parents=True)
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        calls = []
        with unittest.mock.patch.dict(os.environ, {"COWRIE_PERSONA": "ipcam"}), \
                unittest.mock.patch("sys.stdout"), \
                unittest.mock.patch("os.umask", side_effect=lambda m: calls.append(("umask", m)) or 0o22), \
                unittest.mock.patch("os.execv", side_effect=lambda *a: calls.append(("execv",))) as execv:
            self.assertEqual(self.start.main(), 1)
        # twistd applies --umask only after the plugin opened log/cowrie.json: the process umask is set first
        self.assertEqual(calls, [("umask", 0o027), ("execv",)])
        execv.assert_called_once_with("/usr/bin/twistd",
                                      ["/usr/bin/twistd", "-n", "--umask=0027", "--pidfile=", "cowrie"])
        self.assertEqual(Path(os.getcwd()).resolve(), (self.runtime / "runtime").resolve())

    def test_main_reports_an_unknown_persona(self):
        self.start.COWRIE_ROOT, self.start.TMP_ROOT = self.cowrie, self.runtime
        old = os.environ.get("COWRIE_PERSONA")
        os.environ["COWRIE_PERSONA"] = "nope"
        self.addCleanup(lambda: os.environ.pop("COWRIE_PERSONA") if old is None
                        else os.environ.__setitem__("COWRIE_PERSONA", old))
        with unittest.mock.patch("sys.stderr") as err, unittest.mock.patch("os.execv") as execv:
            self.assertEqual(self.start.main(), 1)
        execv.assert_not_called()
        self.assertIn("Could not activate Cowrie persona", "".join(c.args[0] for c in err.write.call_args_list))


    def main_error(self, error):
        """What main() prints when prepare raises error."""
        self.start.COWRIE_ROOT, self.start.TMP_ROOT = self.cowrie, self.runtime

        def prepare(*args, **kwargs):
            raise error

        self.start.prepare = prepare
        with unittest.mock.patch("sys.stderr") as err, unittest.mock.patch("os.execv") as execv:
            self.assertEqual(self.start.main(), 1)
        execv.assert_not_called()
        return "".join(c.args[0] for c in err.write.call_args_list)

    def test_main_reports_any_error_in_one_line(self):
        # a corrupt base.pickle, a builder that fails: no traceback, the type and the message
        cases = ((__import__("pickle").UnpicklingError("invalid load key, 'x'."),
                  "Could not activate Cowrie persona: UnpicklingError: invalid load key, 'x'."),
                 (EOFError("Ran out of input"), "Could not activate Cowrie persona: EOFError: Ran out of input"),
                 (KeyError("git"), "Could not activate Cowrie persona: KeyError: 'git'"),
                 (ValueError("bad"), "Could not activate Cowrie persona: ValueError: bad"),
                 (PersonaError("unknown"), "Could not activate Cowrie persona: unknown"))
        for error, text in cases:
            with self.subTest(error=type(error).__name__):
                self.assertEqual(self.main_error(error).strip(), text)

    def test_main_reports_a_corrupt_base_pickle(self):
        (self.cowrie / "personas" / "base.pickle").write_bytes(b"x" * 20)
        self.start.COWRIE_ROOT, self.start.TMP_ROOT = self.cowrie, self.runtime
        with unittest.mock.patch.dict(os.environ, {"COWRIE_PERSONA": "ipcam"}), \
                unittest.mock.patch("sys.stderr") as err, unittest.mock.patch("os.execv") as execv:
            self.assertEqual(self.start.main(), 1)
        execv.assert_not_called()
        self.assertTrue("".join(c.args[0] for c in err.write.call_args_list).startswith(
            "Could not activate Cowrie persona: UnpicklingError: "))


DAY = 86400
NOW = 1_790_000_000


class Pick:
    """A selection rng that takes the entry at index."""

    def __init__(self, index):
        self.index = index

    def choice(self, seq):
        return seq[self.index]


class RotationTest(unittest.TestCase):
    """COWRIE_PERSONA_ROTATION: a start within the days keeps persona and identity."""

    setUp = StartTest.setUp

    def prepare(self, value, rotation, now, pick=0):
        self.lines = []
        with unittest.mock.patch.object(self.start, "log", side_effect=self.lines.append):
            return self.start.prepare({"COWRIE_PERSONA": value, "COWRIE_PERSONA_ROTATION": rotation}, self.cowrie,
                                      self.runtime, Pick(pick), min_pickle=0, now=now)

    def identity(self):
        return json.loads((self.runtime / "identity.json").read_text())

    @property
    def state_path(self):
        return self.cowrie / "etc" / "persona-state.json"

    def state(self):
        return json.loads(self.state_path.read_text())

    def kept(self, first, second):
        for name in ("persona", "seed", "hostname", "release", "hardware", "installed_at", "booted_at", "tokens",
                     "pid_seed", "chosen_at"):
            self.assertEqual(second[name], first[name], name)

    def test_a_start_within_the_days_keeps_the_identity_and_the_uptime_grows(self):
        self.assertEqual(self.prepare("ubuntu-noble ipcam", "7", NOW), "ubuntu-noble")
        first = self.identity()
        self.assertEqual(self.lines, [])
        # the selection rng would take ipcam now
        self.assertEqual(self.prepare("ubuntu-noble ipcam", "7", NOW + 7 * DAY - 1, pick=-1), "ubuntu-noble")
        second = self.identity()
        self.kept(first, second)
        self.assertEqual(second["boot_offset"], first["boot_offset"] + 7 * DAY - 1)
        self.assertEqual(second["now"], NOW + 7 * DAY - 1)
        self.assertEqual(len(self.lines), 1)
        self.assertTrue(self.lines[0].startswith("Keeping Cowrie persona ubuntu-noble drawn "), self.lines)
        cfg = configparser.ConfigParser(interpolation=None)
        cfg.read(self.runtime / "runtime" / "cowrie.cfg")
        self.assertEqual(cfg.get("honeypot", "boot_offset"), str(second["boot_offset"]))
        self.assertEqual(self.state()["chosen_at"], NOW)

    def test_the_state_file_is_private_and_whole(self):
        self.prepare("ipcam", "7", NOW)
        self.assertEqual(stat.S_IMODE(self.state_path.stat().st_mode), 0o640)
        state = self.state()
        self.assertEqual(state["persona"], "ipcam")
        self.assertEqual(state["seed"], self.identity()["seed"])
        self.assertFalse((self.cowrie / "etc" / "persona-state.json.new").exists())

    def test_after_the_days_a_new_persona_is_drawn(self):
        self.prepare("ubuntu-noble ipcam", "7", NOW)
        first = self.identity()
        self.assertEqual(self.prepare("ubuntu-noble ipcam", "7", NOW + 7 * DAY, pick=-1), "ipcam")
        second = self.identity()
        self.assertNotEqual(second["seed"], first["seed"])
        self.assertEqual(second["chosen_at"], NOW + 7 * DAY)
        self.assertEqual(self.lines, ["Cowrie persona ubuntu-noble was kept for 7 days, drawing a new persona"])
        self.assertEqual(self.state()["persona"], "ipcam")
        self.assertEqual(self.state()["chosen_at"], NOW + 7 * DAY)

    def test_a_persona_no_longer_selected_is_drawn_anew(self):
        self.prepare("ubuntu-noble ipcam", "30", NOW)
        self.assertEqual(self.prepare("ipcam debian-trixie", "30", NOW + DAY, pick=-1), "debian-trixie")
        self.assertEqual(self.lines, ["Cowrie persona ubuntu-noble is no longer in COWRIE_PERSONA, drawing a new persona"])

    def test_a_state_that_cannot_be_used_draws_anew_with_one_line(self):
        cases = {
            "broken JSON": b"{\"persona\": ",
            "no UTF-8": b"\xff\xfe{}",
            "no object": b"[1, 2]",
            "no persona": b"{}",
            "unknown persona": b'{"persona": "no-such-persona", "version": 1}',
            "path as persona": b'{"persona": "../etc", "version": 1}',
            "old version": None,
            "another image": None,
            "too large": b" " * (64 * 1024 + 1),
        }
        for name, data in cases.items():
            with self.subTest(name):
                if data is None:
                    self.prepare("ipcam", "7", NOW)
                    state = self.state()
                    if name == "old version":
                        state.pop("version")
                    else:
                        state["release_label"] = "an older release"
                    data = json.dumps(state).encode()
                self.state_path.write_bytes(data)
                self.assertEqual(self.prepare("ipcam", "7", NOW + DAY), "ipcam")
                self.assertEqual(len(self.lines), 1, self.lines)
                self.assertTrue(self.lines[0].startswith("Cowrie persona state persona-state.json not used ("),
                                self.lines)
                self.assertTrue(self.lines[0].endswith("), drawing a new persona"), self.lines)
                self.assertEqual(self.identity()["chosen_at"], NOW + DAY)
                self.assertEqual(self.state()["chosen_at"], NOW + DAY)

    def test_a_state_that_is_a_folder_does_not_stop_the_start(self):
        self.state_path.mkdir()
        self.assertEqual(self.prepare("ipcam", "7", NOW), "ipcam")
        self.assertEqual(len(self.lines), 2, self.lines)
        self.assertIn("not used (unreadable", self.lines[0])
        self.assertTrue(self.lines[1].startswith("Could not save the Cowrie persona state persona-state.json ("))
        self.assertTrue((self.runtime / "runtime" / "cowrie.cfg").is_file())

    @unittest.skipIf(os.geteuid() == 0, "root writes into a read-only folder")
    def test_an_unwritable_state_file_logs_one_line_and_starts(self):
        self.prepare("ipcam", "7", NOW)
        first = self.identity()
        etc = self.cowrie / "etc"
        os.chmod(etc, 0o550)
        self.addCleanup(os.chmod, etc, 0o750)
        self.assertEqual(self.prepare("ipcam", "7", NOW + DAY), "ipcam")
        self.kept(first, self.identity())
        self.assertEqual(len(self.lines), 2, self.lines)
        self.assertEqual(self.lines[1], "Could not save the Cowrie persona state persona-state.json "
                                        "(Permission denied), the next start draws a new persona")
        self.assertFalse((etc / "persona-state.json.new").exists())
        self.assertEqual(self.state()["chosen_at"], NOW)

    def test_a_failed_rename_leaves_no_staged_file(self):
        with unittest.mock.patch.object(self.start.os, "replace", side_effect=PermissionError(13, "Permission denied")):
            self.assertEqual(self.prepare("ipcam", "7", NOW), "ipcam")
        self.assertEqual(len(self.lines), 1, self.lines)
        self.assertFalse(self.state_path.exists())
        self.assertFalse((self.cowrie / "etc" / "persona-state.json.new").exists())

    def test_rotation_0_draws_anew_on_every_start_and_records_the_persona(self):
        seeds = set()
        for day in range(3):
            self.prepare("ubuntu-noble", "0", NOW + day * DAY)
            seeds.add(self.identity()["seed"])
            self.assertEqual(self.state()["seed"], self.identity()["seed"])
            self.assertEqual(self.lines, [])
        self.assertEqual(len(seeds), 3)
        # unset is 0 too
        self.prepare("ubuntu-noble", None, NOW + 3 * DAY)
        self.assertNotIn(self.identity()["seed"], seeds)

    def test_a_change_from_0_to_days_keeps_the_running_persona(self):
        self.prepare("ubuntu-noble ipcam", "0", NOW)
        first = self.identity()
        self.prepare("ubuntu-noble ipcam", "7", NOW + DAY, pick=-1)
        self.kept(first, self.identity())

    def test_a_restart_of_the_same_persona_keeps_its_host_keys(self):
        # Cowrie writes the keys of the profile into etc/<persona>/ on its first start
        self.prepare("ubuntu-noble", "0", NOW)
        keys = self.cowrie / "etc" / "ubuntu-noble"
        written = {}
        for kind in ("rsa", "ecdsa", "ed25519"):
            for ext in ("", ".pub"):
                path = keys / f"ssh_host_{kind}_key{ext}"
                path.write_bytes(os.urandom(64))
                written[path] = path.read_bytes()
        for rotation, day in (("0", 1), ("7", 2), ("7", 3)):
            self.prepare("ubuntu-noble", rotation, NOW + day * DAY)
            for path, data in written.items():
                self.assertEqual(path.read_bytes(), data, path.name)
            cfg = configparser.ConfigParser(interpolation=None)
            cfg.read(self.runtime / "runtime" / "cowrie.cfg")
            self.assertEqual(cfg.get("ssh", "ed25519_private_key"), str(keys / "ssh_host_ed25519_key"))

    def test_a_kept_device_renders_a_year_on(self):
        # a kept uptime grows beyond the 400 days of a fresh draw
        self.prepare("ipcam", "365", NOW)
        first = self.identity()
        self.prepare("ipcam", "365", NOW + 364 * DAY)
        self.kept(first, self.identity())
        self.assertGreater(self.identity()["boot_offset"], 364 * DAY)

    def test_rotation_takes_what_tpotinit_takes(self):
        for value, days in (("", 0), (None, 0), ("0", 0), ("365", 365), ("007", 7)):
            self.assertEqual(self.start.rotation_days({"COWRIE_PERSONA_ROTATION": value}), days, value)
        for value in ("366", "-1", "7d", " 7", "1.5", "seven"):
            with self.subTest(value), self.assertRaises(PersonaError):
                self.start.rotation_days({"COWRIE_PERSONA_ROTATION": value})
        with self.assertRaises(PersonaError):
            self.prepare("ipcam", "400", NOW)
        self.assertFalse(self.state_path.exists())


class GenerateTest(unittest.TestCase):
    def test_build_driver_cleans_the_pickle_and_lists_every_persona(self):
        gen = load("generate_cowrie_fs", "generate_cowrie_fs.py")
        root = tmpdir(self) / "cowrie"
        (root / "src/cowrie/data").mkdir(parents=True)
        base = base_tree()
        tree.ensure_file(base, "usr/lib/padding.bin", os.urandom(1_100_000))  # the real MIN_PICKLE_SIZE holds
        tree.ensure_file(base, "home/ubuntu/.bashrc", b"upstream\n")
        tree.dump_pickle(base, root / "src/cowrie/data/fs.pickle")
        work = tmpdir(self) / "work"
        self.assertEqual(gen.main(["--cowrie-root", str(root), "--work-dir", str(work), "--seeds", "1"]), 0)
        cleaned = tree.load_pickle(root / "personas" / "base.pickle")
        self.assertIsNone(tree.find_node(cleaned, "home/phil"))
        self.assertIsNone(tree.find_node(cleaned, "home/ubuntu"))
        listed = json.loads((root / "personas" / "personas.json").read_text())
        self.assertEqual([p["id"] for p in listed], catalog.ids())
        self.assertEqual(len(listed), 16)
        self.assertEqual(set(listed[0]), {"id", "family", "kind", "user", "arch", "ssh_banners",
                                          "package_managers", "summary"})
        noble = next(p for p in listed if p["id"] == "ubuntu-noble")
        self.assertTrue(all(b.startswith("SSH-2.0-OpenSSH_") for b in noble["ssh_banners"]))

    def test_build_driver_fails_on_a_persona_that_does_not_validate(self):
        gen = load("generate_cowrie_fs", "generate_cowrie_fs.py")
        root = tmpdir(self) / "cowrie"
        (root / "src/cowrie/data").mkdir(parents=True)
        tree.dump_pickle(base_tree(), root / "src/cowrie/data/fs.pickle")  # below MIN_PICKLE_SIZE
        with self.assertRaises(PersonaError):
            gen.main(["--cowrie-root", str(root), "--work-dir", str(tmpdir(self)), "--seeds", "1"])


class LayoutTest(unittest.TestCase):
    def test_no_old_persona_ids(self):
        for path in (DIST / "generate_cowrie_fs.py", DIST / "start-cowrie-persona.py", DOCKERFILE):
            text = path.read_text()
            for old in OLD_IDS:
                self.assertFalse(old in text, f"{old} in {path.name}")

    def test_dockerfile_pins_v3_1_1_and_boots_a_catalog_persona(self):
        text = DOCKERFILE.read_text()
        for needle in ("ARG COWRIE_VERSION=v3.1.1", "ARG COWRIE_COMMIT=c17c9b73d6af0972334ea1e90b20d974cb24eeca",
                       "COWRIE_PERSONA=debian-bookworm ",
                       "python3 /root/dist/check_ssh_support.py --cowrie-root /home/cowrie/cowrie &&",
                       "python3 /root/dist/patch_cowrie_persona_support.py --cowrie-root /home/cowrie/cowrie --self-test &&"):
            self.assertTrue(needle in text, f"{needle} not in the Dockerfile")
        self.assertIn("debian-bookworm", catalog.ids())

    def test_old_static_files_are_gone(self):
        self.assertFalse((DIST / "cowrie.cfg").exists())
        self.assertFalse((DIST / "requirements.txt").exists())


if __name__ == "__main__":
    unittest.main()
