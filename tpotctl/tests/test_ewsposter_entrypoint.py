"""The ewsposter entrypoint (docker/ewsposter/dist/entrypoint.sh): its loop of ews.py passes and
the hold that T-Pot's checks ask for, sourced with a stand-in for ews.py; the end of the hold in
tpotinit (clear_ews_hold of docker/tpotinit/dist/entrypoint.sh); and the log paths of ews.cfg
against those Logstash reads for the same honeypot.

The image itself runs under busybox ash; here the functions run under every POSIX shell of the
host that is close to it (sh, dash). The smoke test docker/_tests/tests/ewsposter.sh runs the
real loop in the image.
"""

import configparser
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = ROOT / "docker" / "ewsposter" / "dist" / "entrypoint.sh"
EWS_CFG = ROOT / "docker" / "ewsposter" / "dist" / "ews.cfg"
TPOTINIT = ROOT / "docker" / "tpotinit" / "dist" / "entrypoint.sh"
LOGSTASH = ROOT / "docker" / "elk" / "logstash" / "dist" / "logstash.conf"
CLEAN = ROOT / "docker" / "tpotinit" / "dist" / "bin" / "clean.sh"

SHELLS = [shell for shell in ("/bin/sh", "/bin/dash", shutil.which("busybox") and "busybox-ash")
          if shell and (shell == "busybox-ash" or os.access(shell, os.X_OK))]
OFF = {"EWS": "ews", "HPFEED": "hpfeed", "EWSJSON": "json", "INFLUXDB": "influxdb"}

# A stand-in for python3: records its arguments, says the limit was reached for the first
# $STUB_LIMITED calls (the line ews.py prints), ends with $STUB_RC
STUB = """#!/bin/sh
echo "$*" >> "$STUB_DIR/calls"
n=$(wc -l < "$STUB_DIR/calls")
if [ "$n" -le "${STUB_LIMITED:-0}" ]; then
  echo "    -> Sendlimit (5000) for Honeypot COWRIE reached. Skip."
fi
echo " => EWSrun finish."
exit "${STUB_RC:-0}"
"""


def shell_argv(shell):
    return ["busybox", "ash"] if shell == "busybox-ash" else [shell]


class Sandbox:
    """A folder with the paths of the entrypoint: ews (ewsposter, with ews.cfg), data/ews (the
    marker), hold (the idle config) and the stand-in for python3."""

    def __init__(self, base):
        self.base = Path(base)
        self.ews = self.base / "ews"
        self.data = self.base / "data" / "ews"
        self.idle = self.base / "hold"
        self.stub = self.base / "python3"
        for folder in (self.ews, self.data):
            folder.mkdir(parents=True)
        shutil.copy(EWS_CFG, self.ews / "ews.cfg")
        self.stub.write_text(STUB)
        self.stub.chmod(0o755)

    @property
    def hold(self):
        return self.data / "hold"

    @property
    def ack(self):
        return self.data / "hold.ack"

    def calls(self):
        path = self.base / "calls"
        return path.read_text().splitlines() if path.exists() else []

    def run(self, shell, script, **env):
        """`script` after the entrypoint is sourced and its paths point into the sandbox."""
        prelude = (
            '. "$ENTRY"\n'
            f'myPYTHON="{self.stub}"\n'
            f'myEWS="{self.ews}"\n'
            f'myHOLD="{self.hold}"\n'
            f'myHOLD_ACK="{self.ack}"\n'
            f'myIDLE_CFG="{self.idle}"\n'
            'mySLEEP=42\n'
            'myHELD=""\n'
        )
        environ = dict(os.environ, ENTRY=str(ENTRYPOINT), STUB_DIR=str(self.base),
                       **{key: str(value) for key, value in env.items()})
        return subprocess.run([*shell_argv(shell), "-c", prelude + script], env=environ,
                              capture_output=True, text=True, timeout=60)


class EntrypointCase(unittest.TestCase):
    def setUp(self):
        if not SHELLS:
            self.skipTest("no POSIX shell on this host")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def sandbox(self, name):
        return Sandbox(Path(self.tmp.name) / name)


class HoldDecisionTest(EntrypointCase):
    """The hold is on while /data/ews/hold is a regular file, no link, younger than 1800 s."""

    def decide(self, shell, box):
        return box.run(shell, "fuHOLD_ON").returncode == 0

    def test_decision(self):
        now = time.time()
        cases = {
            "missing": (None, False),
            "fresh file": (now, True),
            "29 minutes": (now - 29 * 60, True),
            "31 minutes": (now - 31 * 60, False),
            "30 s ahead": (now + 30, True),
            "2 h ahead": (now + 7200, False),
        }
        for shell in SHELLS:
            for name, (mtime, expected) in cases.items():
                with self.subTest(shell=shell, case=name):
                    box = self.sandbox(f"{Path(shell).name}-{name}")
                    if mtime is not None:
                        box.hold.write_text("")
                        os.utime(box.hold, (mtime, mtime))
                    self.assertEqual(self.decide(shell, box), expected)

    def test_link_and_folder_are_no_hold(self):
        for shell in SHELLS:
            with self.subTest(shell=shell, case="link"):
                box = self.sandbox(f"{Path(shell).name}-link")
                target = box.base / "target"
                target.write_text("")
                box.hold.symlink_to(target)
                self.assertFalse(self.decide(shell, box))
            with self.subTest(shell=shell, case="folder"):
                box = self.sandbox(f"{Path(shell).name}-folder")
                box.hold.mkdir()
                self.assertFalse(self.decide(shell, box))


class IdleConfigTest(EntrypointCase):
    """The idle config: ews, hpfeed, json and influxdb set to false in their sections, nothing else."""

    def rewrite(self, shell, box, text):
        source = box.base / "in.cfg"
        source.write_text(text)
        result = box.run(shell, f'fuIDLE_CONFIG_REWRITE "{source}"')
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_real_config_changes_exactly_the_four_keys(self):
        # ews.cfg of the image, with json and influxdb switched on as a person might
        text = re.sub(r"^(json|influxdb) = false$", r"\1 = true", EWS_CFG.read_text(), flags=re.M)
        original = text.splitlines()
        self.assertEqual(len(re.findall(r"^(ews|json|influxdb) = true$", text, re.M)), 3)
        for shell in SHELLS:
            with self.subTest(shell=shell):
                box = self.sandbox(Path(shell).name)
                rewritten = self.rewrite(shell, box, text).splitlines()
                self.assertEqual(len(rewritten), len(original))
                changed = [(old, new) for old, new in zip(original, rewritten) if old != new]
                self.assertEqual(sorted(new for _, new in changed),
                                 sorted(f"{key} = false" for key in OFF.values()))
                parser = configparser.RawConfigParser()
                parser.read_string("\n".join(rewritten))
                before = configparser.RawConfigParser()
                before.read_string("\n".join(original))
                for section in before.sections():
                    for key, value in before.items(section):
                        if OFF.get(section) == key:
                            self.assertEqual(parser.get(section, key), "false")
                        else:
                            self.assertEqual(parser.get(section, key), value, f"[{section}] {key}")

    def test_other_sections_forms_and_missing_keys(self):
        text = (
            "# a comment\n"
            "[MAIN]\nhomedir = /opt/ewsposter/\news = true\n\n"
            "[EWS]\nEWS = TRUE\njson = true\nusername = u\n\n"
            "[HPFEED]\nhpfeed: %(EWS_HPFEEDS_ENABLE)s\nhost = h\n\n"
            "[INFLUXDB]\nhost = i\n\n"
            "[COWRIE]\ncowrie = true\njson = true\n"
        )
        for shell in SHELLS:
            with self.subTest(shell=shell):
                box = self.sandbox(Path(shell).name)
                out = self.rewrite(shell, box, text)
                parser = configparser.RawConfigParser()
                parser.read_string(out)
                self.assertEqual(parser.get("EWS", "ews"), "false")
                self.assertEqual(parser.get("HPFEED", "hpfeed"), "false")
                self.assertEqual(parser.get("INFLUXDB", "influxdb"), "false")
                self.assertEqual(parser.get("EWSJSON", "json"), "false")
                # the same names elsewhere stay
                self.assertEqual(parser.get("MAIN", "ews"), "true")
                self.assertEqual(parser.get("EWS", "json"), "true")
                self.assertEqual(parser.get("COWRIE", "json"), "true")
                self.assertEqual(parser.get("HPFEED", "host"), "h")
                self.assertIn("# a comment", out)

    def test_config_folder(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                box = self.sandbox(Path(shell).name)
                (box.ews / "ews.ip").write_text("[EWSIP]\nip_int = 192.0.2.1\nip_ext = 192.0.2.1\n")
                result = box.run(shell, "fuIDLE_CONFIG")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(os.readlink(box.idle / "ews.ip"), str(box.ews / "ews.ip"))
                self.assertFalse(os.path.lexists(box.idle / "ews.uuid"))
                parser = configparser.RawConfigParser()
                parser.read(box.idle / "ews.cfg")
                self.assertEqual(parser.get("MAIN", "homedir"), "/opt/ewsposter/")
                self.assertEqual(parser.get("EWS", "ews"), "false")
                (box.ews / "ews.uuid").write_text("uuid")
                box.run(shell, "fuIDLE_CONFIG")
                self.assertEqual(os.readlink(box.idle / "ews.uuid"), str(box.ews / "ews.uuid"))


class PassCountTest(EntrypointCase):
    """Idle passes repeat while ews.py says a module stopped at the limit, 20 at most."""

    def test_passes(self):
        for shell in SHELLS:
            for limited, passes in ((0, 1), (3, 4), (100, 20)):
                with self.subTest(shell=shell, limited=limited):
                    box = self.sandbox(f"{Path(shell).name}-{limited}")
                    result = box.run(shell, "fuIDLE", STUB_LIMITED=limited)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    calls = box.calls()
                    self.assertEqual(len(calls), passes)
                    self.assertEqual(set(calls),
                                     {f"-u {box.ews}/ews.py -c {box.idle}/ -l 0 -E -L 5000"})
                    self.assertIn("EWSrun finish", result.stdout)

    def test_failed_pass_ends_the_round(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                box = self.sandbox(Path(shell).name)
                result = box.run(shell, "fuIDLE", STUB_LIMITED=5, STUB_RC=1)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(len(box.calls()), 1)
                self.assertIn("rc=1", result.stdout)


class RoundTest(EntrypointCase):
    """The rounds of the loop: idle while the hold is on, one idle pass more at its end, then normal."""

    @staticmethod
    def kinds(box):
        return ["idle" if " -E " in call else "normal" for call in box.calls()]

    def test_hold_on_then_over(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                box = self.sandbox(Path(shell).name)
                box.hold.write_text("")
                script = (
                    'fuROUND; echo "interval=$myINTERVAL"\n'
                    f'[ -f "{box.ack}" ] && echo ack-after-1\n'
                    'fuROUND; echo "interval=$myINTERVAL"\n'
                    f'rm -f "{box.hold}"\n'
                    'fuROUND; echo "interval=$myINTERVAL"\n'
                    f'[ -e "{box.ack}" ] || echo no-ack-after-3\n'
                    'fuROUND; echo "interval=$myINTERVAL"\n'
                )
                result = box.run(shell, script)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(re.findall(r"^interval=(\d+)$", result.stdout, re.M),
                                 ["5", "5", "42", "42"])
                self.assertIn("ack-after-1", result.stdout)
                self.assertIn("no-ack-after-3", result.stdout)
                self.assertEqual(self.kinds(box), ["idle", "idle", "idle", "normal", "normal"])

    def test_no_hold_and_stale_hold_send(self):
        for shell in SHELLS:
            for name in ("none", "stale"):
                with self.subTest(shell=shell, case=name):
                    box = self.sandbox(f"{Path(shell).name}-{name}")
                    if name == "stale":
                        box.hold.write_text("")
                        old = time.time() - 31 * 60
                        os.utime(box.hold, (old, old))
                    result = box.run(shell, 'fuROUND; echo "interval=$myINTERVAL"')
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("interval=42", result.stdout)
                    self.assertEqual(self.kinds(box), ["normal"])
                    self.assertFalse(os.path.lexists(box.ack))
                    self.assertEqual(box.calls(), [f"-u {box.ews}/ews.py -l 0"])

    def test_ack_link_is_replaced_not_followed(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                box = self.sandbox(Path(shell).name)
                box.hold.write_text("")
                target = box.base / "elsewhere"
                target.write_text("keep")
                old = time.time() - 3600
                os.utime(target, (old, old))
                box.ack.symlink_to(target)
                result = box.run(shell, "fuROUND")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(box.ack.is_symlink())
                self.assertTrue(box.ack.is_file())
                self.assertEqual(target.read_text(), "keep")
                self.assertAlmostEqual(target.stat().st_mtime, old, delta=1)

    def test_loop_waits_for_each_pass_and_handles_signals(self):
        """One ews.py at a time: every pass and pause runs through fuRUN (background plus wait,
        so INT / TERM end it at once), the stale ack goes at the start, the loop starts only as
        entrypoint.sh."""
        text = ENTRYPOINT.read_text()
        self.assertIn("trap fuSTOP INT TERM", text)
        self.assertEqual(len(re.findall(r'fuRUN "\$\{myPYTHON\}" -u "\$\{myEWS\}/ews.py"', text)), 2)
        self.assertIn('fuRUN sleep "${myINTERVAL}"', text)
        self.assertNotIn("exec ", text)
        self.assertRegex(text, r'rm -f "\$\{myHOLD_ACK\}"\n  while :;')
        self.assertRegex(text, r'case "\$\{0##\*/\}" in\n  entrypoint\.sh\) fuMAIN ;;\nesac\n$')


class TpotinitClearTest(unittest.TestCase):
    """tpotinit removes hold, hold.ack and check.lock in <data>/ews on every start, never through a link."""

    def setUp(self):
        if not shutil.which("bash"):
            self.skipTest("bash is missing")
        text = TPOTINIT.read_text()
        match = re.search(r"^clear_ews_hold\(\) \{\n.*?^\}\n", text, re.M | re.S)
        self.assertIsNotNone(match, "clear_ews_hold() is missing in the tpotinit entrypoint")
        self.function = match.group(0)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name) / "data"
        self.ews = self.data / "ews"
        self.ews.mkdir(parents=True)

    def clear(self):
        result = subprocess.run(["bash", "-c", self.function + 'clear_ews_hold "$1"', "bash", str(self.data)],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_removes_the_three_names_only(self):
        for name in ("hold", "hold.ack", "check.lock", "hold.old", "conf"):
            (self.ews / name).write_text("")
        self.clear()
        self.assertEqual(sorted(p.name for p in self.ews.iterdir()), ["conf", "hold.old"])

    def test_links_are_removed_not_followed(self):
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        (outside / "hold").write_text("keep")
        (self.ews / "hold").symlink_to(outside / "hold")
        (self.ews / "check.lock").symlink_to(outside)
        self.clear()
        self.assertFalse(os.path.lexists(self.ews / "hold"))
        self.assertFalse(os.path.lexists(self.ews / "check.lock"))
        self.assertEqual((outside / "hold").read_text(), "keep")

    def test_ews_folder_as_link_is_left_alone(self):
        shutil.rmtree(self.ews)
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        (outside / "hold").write_text("keep")
        self.ews.symlink_to(outside)
        self.clear()
        self.assertTrue((outside / "hold").exists())

    def test_folder_named_hold_stays_and_missing_folder_is_fine(self):
        (self.ews / "hold.ack").mkdir()
        self.assertIn("is a folder", self.clear())
        self.assertTrue((self.ews / "hold.ack").is_dir())
        shutil.rmtree(self.ews)
        self.clear()

    def test_called_on_every_start_before_ready(self):
        text = TPOTINIT.read_text()
        call = text.index("\nclear_ews_hold /data\n")
        self.assertGreater(call, text.index("# Data folder management"))
        self.assertLess(call, text.index("touch /tmp/success"))
        # on its own line at the top level, not inside the first-run branch
        self.assertGreater(call, text.index("    uuidgen > /data/uuid\nfi\n"))


def logstash_paths():
    """{type in lower case: [paths]} of the file inputs of logstash.conf."""
    text = LOGSTASH.read_text()
    inputs = text[:text.index("\nfilter {")]
    paths = {}
    for block in re.findall(r"file\s*\{(.*?)\n  \}", inputs, re.S):
        found = re.search(r'path => \["([^"]+)"\]', block)
        kind = re.search(r'type => "([^"]+)"', block)
        if found and kind:
            paths.setdefault(kind.group(1).lower(), []).append(found.group(1))
    return paths


class EwsCfgPathsTest(unittest.TestCase):
    """Every module of ews.cfg reads the log Logstash reads for the same honeypot."""

    # ews.cfg section -> Logstash type, where the names differ
    TYPES = {"CITRIX": "citrixhoneypot", "GOPOT": "go-pot"}
    # ewsposter reads another file of the honeypot than Logstash, in the same folder
    SAME_FOLDER = {
        ("HONEYTRAP", "attackerfile"): "the attacker.log of honeytrap, Logstash reads attackers.json",
        ("DIONAEA", "sqlitedb"): "the SQLite log of Dionaea, Logstash reads dionaea.json",
    }

    def setUp(self):
        self.config = configparser.RawConfigParser()
        self.config.read(EWS_CFG)
        self.paths = logstash_paths()

    def modules(self):
        """Every module section, on or off: one that is off today is switched on later as it is."""
        for section in self.config.sections():
            if self.config.has_option(section, section.lower()) and section not in OFF:
                yield section

    def test_logstash_inputs_are_found(self):
        self.assertIn("cowrie", self.paths)
        self.assertIn("/data/conpot/log/*.json", self.paths["conpot"])

    def test_modules_read_the_logstash_logs(self):
        sections = list(self.modules())
        self.assertIn("COWRIE", sections)
        for section in sections:
            kind = self.TYPES.get(section, section.lower())
            with self.subTest(section=section):
                self.assertIn(kind, self.paths, f"no Logstash input of type {kind}")
                logstash = self.paths[kind]
                keys = [key for key in ("logfile", "logdir", "attackerfile", "sqlitedb")
                        if self.config.has_option(section, key)]
                self.assertTrue(keys, f"[{section}] names no log")
                for key in keys:
                    value = self.config.get(section, key).rstrip("/")
                    if key == "logdir":
                        self.assertIn(value, [os.path.dirname(path) for path in logstash if "*" in path])
                    elif (section, key) in self.SAME_FOLDER:
                        self.assertIn(os.path.dirname(value), [os.path.dirname(path) for path in logstash])
                    else:
                        self.assertIn(value, logstash)

    def test_payload_folders_are_made_by_tpotinit(self):
        folders = {path.rstrip("/") for path in re.findall(r'"(/data/[^"$]+)"', CLEAN.read_text())}
        for section in self.modules():
            for key in ("payloaddir", "malwaredir"):
                if self.config.has_option(section, key):
                    with self.subTest(section=section, key=key):
                        self.assertIn(self.config.get(section, key).rstrip("/"), folders)


if __name__ == "__main__":
    unittest.main()
