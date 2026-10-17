"""The ewsposter entrypoint (docker/ewsposter/dist/entrypoint.sh): its loop of ews.py passes and
the hold that T-Pot's checks ask for, sourced with a stand-in for ews.py; the end of the hold in
tpotinit (clear_ews_hold of docker/tpotinit/dist/entrypoint.sh); and the log paths of ews.cfg
against those Logstash reads for the same honeypot.

The image itself runs under busybox ash; here the functions run under every POSIX shell of the
host that is close to it (sh, dash). The smoke test docker/_tests/tests/ewsposter.sh runs the
real loop in the image.
"""

import configparser
import fnmatch
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
LOGROTATE = ROOT / "docker" / "tpotinit" / "dist" / "etc" / "logrotate" / "logrotate.template"

# The ewsposter commit of docker/ewsposter/Dockerfile and the two lines of it whose output the
# rounds read: modules/ealert.py:437 (EAlert.buildAlert) and :43 (EAlert.__init__)
PINNED = "c6b44c7544e71a637d1bcc019abf4a248afb05d4"
SENDLIMIT_SOURCE = """print(f'    -> Sendlimit ({self.ECFG["sendlimit"]}) for Honeypot {self.MODUL} reached. Skip.')"""
STARTING_SOURCE = "print(f' => Starting {self.MODUL.title()} Honeypot Modul.')"

# A stand-in for python3: records its arguments; prints " => Starting ..." with $STUB_STARTED;
# says the limit was reached for the first $STUB_LIMITED calls (the line ews.py prints); ends
# with $STUB_RC, with 1 for the calls in $STUB_FAIL_CALLS and, with $STUB_CRASH, for a run of all
# modules and for "-m $STUB_CRASH"; ends itself by TERM on the calls in $STUB_KILL_CALLS
STUB = """#!/bin/sh
echo "$*" >> "$STUB_DIR/calls"
n=$(wc -l < "$STUB_DIR/calls")
n=$((n + 0))
module=""
previous=""
for argument in "$@"; do
  [ "$previous" = "-m" ] && module="$argument"
  previous="$argument"
done
[ -n "${STUB_STARTED:-}" ] && echo " => Starting Cowrie Honeypot Modul."
if [ "$n" -le "${STUB_LIMITED:-0}" ]; then
  echo "    -> Sendlimit (5000) for Honeypot COWRIE reached. Skip."
fi
case " ${STUB_KILL_CALLS:-} " in *" $n "*) kill -TERM $$ ;; esac
rc="${STUB_RC:-0}"
case " ${STUB_FAIL_CALLS:-} " in *" $n "*) rc=1 ;; esac
if [ -n "${STUB_CRASH:-}" ] && { [ -z "$module" ] || [ "$module" = "$STUB_CRASH" ]; }; then
  rc=1
fi
echo " => EWSrun finish."
exit "$rc"
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
        # the index of ews.py (homedir): a container that has run before
        (self.ews / "ews.idx").write_text("")
        self.stub.write_text(STUB)
        self.stub.chmod(0o755)

    @property
    def hold(self):
        return self.data / "hold"

    @property
    def ack(self):
        return self.data / "hold.ack"

    @property
    def quarantine(self):
        return self.data / "quarantine"

    def quarantined(self):
        return self.quarantine.read_text().split()[1:] if self.quarantine.exists() else []

    @property
    def state(self):
        return self.data / "hold.state"

    def state_word(self):
        return self.state.read_text().split()[0] if self.state.exists() else None

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
            f'myHOLD_STATE="{self.state}"\n'
            f'myIDLE_CFG="{self.idle}"\n'
            f'myQUARANTINE_FILE="{self.quarantine}"\n'
            f'myCATCHUP="{self.data / "catchup"}"\n'
            f'myNORMAL_CFG="{self.base / "normal"}"\n'
            'mySLEEP=42\n'
            'myHELD=""\n'
            'myIDLE_RETRY_PAUSE=0\n'
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
            for limited, passes, rc in ((0, 1, 0), (3, 4, 0), (19, 20, 0), (100, 20, 2)):
                with self.subTest(shell=shell, limited=limited):
                    box = self.sandbox(f"{Path(shell).name}-{limited}")
                    result = box.run(shell, "fuIDLE", STUB_LIMITED=limited)
                    # 2: every pass ended with rc 0, but a module is still behind at the cap
                    self.assertEqual(result.returncode, rc, result.stderr)
                    calls = box.calls()
                    self.assertEqual(len(calls), passes)
                    self.assertEqual(set(calls),
                                     {f"-u {box.ews}/ews.py -c {box.idle}/ -l 0 -E -L 5000"})
                    self.assertIn("EWSrun finish", result.stdout)

    def test_failed_pass_is_tried_again(self):
        """A failed pass is tried again up to 3 times; one that works on the second try is clean."""
        for shell in SHELLS:
            with self.subTest(shell=shell):
                box = self.sandbox(Path(shell).name)
                result = box.run(shell, "fuIDLE", STUB_FAIL_CALLS="1 2")
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertEqual(len(box.calls()), 3)
                self.assertIn("try 1 of 3", result.stdout)

    def test_killed_pass_is_tried_again(self):
        """ews.py ended by a signal (rc 143) counts as a failed pass."""
        for shell in SHELLS:
            with self.subTest(shell=shell):
                box = self.sandbox(Path(shell).name)
                result = box.run(shell, "fuIDLE", STUB_KILL_CALLS="1")
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertEqual(len(box.calls()), 2)
                self.assertIn("rc=143", result.stdout)

    def test_start_failure_fails_the_round_without_module_passes(self):
        """ews.py fails before any module (as with an error of api.ipify.org): 1 + 3 tries, no
        pass per module, the round failed."""
        for shell in SHELLS:
            with self.subTest(shell=shell):
                box = self.sandbox(Path(shell).name)
                result = box.run(shell, "fuIDLE", STUB_RC=1)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(len(box.calls()), 4)
                self.assertFalse(any(" -m " in call for call in box.calls()))

    def test_module_crash_falls_back_to_one_pass_per_module(self):
        """A module that keeps failing: after the tries, every module of the idle config has its
        own pass (-m), so the others move on; the round is clean for them (3) and names the one
        that failed (myIDLE_FAILED)."""
        config = configparser.RawConfigParser()
        config.read(EWS_CFG)
        modules = sorted(section.lower() for section in config.sections()
                         if config.has_option(section, section.lower()) and section not in OFF
                         and config.get(section, section.lower()) != "")
        self.assertIn("cowrie", modules)
        for shell in SHELLS:
            with self.subTest(shell=shell):
                box = self.sandbox(Path(shell).name)
                result = box.run(shell, 'fuIDLE; rc=$?; echo "failed=[$myIDLE_FAILED]"; exit $rc',
                                 STUB_STARTED=1, STUB_CRASH="cowrie")
                self.assertEqual(result.returncode, 3)
                self.assertIn("failed=[ cowrie]", result.stdout)
                calls = box.calls()
                self.assertEqual(len([call for call in calls if " -m " not in call]), 4)
                per_module = [call.rsplit(" -m ", 1)[1] for call in calls if " -m " in call]
                self.assertEqual(sorted(set(per_module)), modules)
                self.assertEqual(per_module.count("cowrie"), 4)
                self.assertTrue(all(per_module.count(name) == 1 for name in modules if name != "cowrie"))
                self.assertIn("Idle passes failed for: cowrie.", result.stdout)

    def test_every_module_failing_fails_the_round(self):
        """No module can be held (every pass of every module fails): the round failed (1); the
        modules are named (a check that holds already learns them from hold.ack)."""
        for shell in SHELLS:
            with self.subTest(shell=shell):
                box = self.sandbox(Path(shell).name)
                result = box.run(shell, 'fuIDLE; rc=$?; echo "failed=[$myIDLE_FAILED]"; exit $rc',
                                 STUB_STARTED=1, STUB_RC=1)
                self.assertEqual(result.returncode, 1)
                self.assertIn("failed=[ adbhoney beelzebub ", result.stdout)
                self.assertTrue(any(" -m " in call for call in box.calls()))

    def test_no_module_to_try_one_by_one_fails_closed(self):
        """The full pass failed after a module started, but the config names none to try alone:
        the round is not clean (1)."""
        for shell in SHELLS:
            with self.subTest(shell=shell):
                box = self.sandbox(Path(shell).name)
                result = box.run(shell, 'fuIDLE_MODULES () { :; }; fuIDLE; echo "rc=$?"',
                                 STUB_STARTED=1, STUB_RC=1)
                self.assertIn("rc=1", result.stdout)
                self.assertIn("names no module to try one by one", result.stdout)

    def test_failed_module_and_a_module_behind_is_behind(self):
        """One module fails, another is still behind: no ack until it caught up (2)."""
        for shell in SHELLS:
            with self.subTest(shell=shell):
                box = self.sandbox(Path(shell).name)
                # the four tries of the full pass fail (cowrie), then every pass says the limit
                result = box.run(shell, "myIDLE_PASSES=2; fuIDLE", STUB_STARTED=1, STUB_CRASH="cowrie", STUB_LIMITED=10000)
                self.assertEqual(result.returncode, 2)

    def test_unwritable_config_folder_fails_the_round(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                box = self.sandbox(Path(shell).name)
                locked = box.base / "locked"
                locked.mkdir()
                locked.chmod(0o555)
                self.addCleanup(locked.chmod, 0o755)
                if os.access(locked, os.W_OK):
                    self.skipTest("the folder stays writable (root)")
                result = box.run(shell, f'myIDLE_CFG="{locked}/hold"; fuIDLE')
                self.assertEqual(result.returncode, 1)
                self.assertEqual(box.calls(), [])
                self.assertIn("could not be written", result.stdout)

    def test_module_list_of_the_idle_config(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                box = self.sandbox(Path(shell).name)
                box.idle.mkdir()
                (box.idle / "ews.cfg").write_text(
                    "[MAIN]\nhomedir = /opt/ewsposter/\n[EWS]\news = false\n[HPFEED]\nhpfeed = %(X)s\n"
                    "[EWSJSON]\njson = false\n[INFLUXDB]\ninfluxdb = false\n"
                    "[COWRIE]\ncowrie = true\nlogfile = x\n[WORDPOT]\nwordpot= TRUE\n"
                    "[GLUTTON]\nglutton = false\n# glutton = true\n[GOPOT]\ngopot = %(GOPOT)s\n"
                    "[HELLPOT]\nhellpot =\n")
                result = box.run(shell, "fuIDLE_MODULES")
                # as readMYcfg of ews.py: a key of the section's name that is not empty; the
                # module itself skips on "false"
                self.assertEqual(result.stdout.split(), ["cowrie", "wordpot", "glutton", "gopot"])

    def test_texts_of_the_pinned_ewsposter(self):
        """The texts the rounds read from ews.py are those of the pinned commit; a bump of
        EWSPOSTER_COMMIT means checking them again (and this test)."""
        dockerfile = (ROOT / "docker" / "ewsposter" / "Dockerfile").read_text()
        self.assertIn(f"ARG EWSPOSTER_COMMIT={PINNED}", dockerfile)
        text = ENTRYPOINT.read_text()
        for source in (SENDLIMIT_SOURCE, STARTING_SOURCE):
            self.assertIn(source, text)
        result = Sandbox(Path(self.tmp.name) / "texts").run(
            SHELLS[0], 'printf "%s\\n%s\\n" "$myIDLE_LIMIT_TEXT" "$myIDLE_MODULE_TEXT"')
        limit_text, module_text = result.stdout.split("\n")[:2]
        # the f-strings of the source, filled in as ews.py does for -L 5000 and the cowrie module
        printed_limit = "    -> Sendlimit (5000) for Honeypot COWRIE reached. Skip."
        printed_start = " => Starting Cowrie Honeypot Modul."
        self.assertEqual(SENDLIMIT_SOURCE.replace('{self.ECFG["sendlimit"]}', "5000")
                         .replace("{self.MODUL}", "COWRIE"), f"print(f'{printed_limit}')")
        self.assertEqual(STARTING_SOURCE.replace("{self.MODUL.title()}", "Cowrie"),
                         f"print(f'{printed_start}')")
        self.assertIn(limit_text, printed_limit)
        self.assertTrue(printed_start.startswith(module_text))


class RoundTest(EntrypointCase):
    """The rounds of the loop: idle while the hold is on, one idle pass more at its end, then normal."""

    @staticmethod
    def kinds(box):
        return ["idle" if " -E " in call else "normal" for call in box.calls()]

    def test_first_round_of_a_hold_sends_before_the_ack(self):
        """The normal pass of the first hold round runs before the idle passes and before hold.ack."""
        for shell in SHELLS:
            with self.subTest(shell=shell):
                box = self.sandbox(Path(shell).name)
                box.hold.write_text("")
                # the stand-in notes whether hold.ack was there when it ran
                stub = box.stub.read_text().replace(
                    'echo "$*" >> "$STUB_DIR/calls"',
                    'echo "$*" >> "$STUB_DIR/calls"; [ -e "$STUB_ACK" ] && echo ack >> "$STUB_DIR/acks"')
                box.stub.write_text(stub)
                result = box.run(shell, "fuROUND", STUB_ACK=box.ack)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.kinds(box), ["normal", "idle"])
                self.assertFalse((box.base / "acks").exists())
                self.assertTrue(box.ack.is_file())

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
                # the first round of the hold starts with a normal pass
                self.assertEqual(self.kinds(box), ["normal", "idle", "idle", "idle", "normal", "normal"])

    def test_cap_during_the_hold_writes_no_ack_until_caught_up(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                box = self.sandbox(Path(shell).name)
                box.hold.write_text("")
                script = (
                    f'myHELD=yes; fuROUND; [ -e "{box.ack}" ] || echo no-ack-after-cap\n'
                    'fuROUND\n'
                )
                result = box.run(shell, script, STUB_LIMITED=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("no-ack-after-cap", result.stdout)
                self.assertIn("still behind", result.stdout)
                self.assertEqual(self.kinds(box), ["idle"] * 21)
                self.assertTrue(box.ack.is_file())
                self.assertEqual(box.state_word(), "held")

    def test_cap_at_the_end_of_a_hold_quarantines_the_module_behind(self):
        """A module still behind when the marker is gone: quarantined (off in the normal passes),
        the others send; the next round its own idle pass catches up and it sends again."""
        for shell in SHELLS:
            with self.subTest(shell=shell):
                box = self.sandbox(Path(shell).name)
                script = (
                    'myHELD=yes; fuROUND; echo "interval=$myINTERVAL held=$myHELD q=[$myQUARANTINE]"\n'
                    f'echo "file=$(cut -d" " -f2- "{box.quarantine}")"\n'
                    'fuROUND; echo "interval=$myINTERVAL q=[$myQUARANTINE]"\n'
                )
                result = box.run(shell, script, STUB_LIMITED=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("interval=42 held= q=[ cowrie]", result.stdout)
                self.assertIn("file=cowrie", result.stdout)
                self.assertIn("interval=42 q=[]", result.stdout)
                calls = box.calls()
                normal = [call for call in calls if " -E " not in call]
                # the first normal pass with the copy that has cowrie off, the second as usual
                self.assertIn(f"-c {box.base / 'normal'}/", normal[0])
                self.assertEqual(normal[1], f"-u {box.ews}/ews.py -l 0")
                self.assertIn("cowrie = false", (box.base / "normal" / "ews.cfg").read_text())
                self.assertFalse(box.quarantine.exists())
                self.assertEqual(box.state_word(), "normal")

    def test_stuck_says_so_as_error_every_ten_minutes(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                box = self.sandbox(Path(shell).name)
                result = box.run(shell, "myHELD=yes; fuROUND; fuROUND; fuROUND", STUB_RC=1)
                self.assertEqual(result.stdout.count("[ERROR]"), 1)
                self.assertEqual(box.state_word(), "stuck")
                # once the report time has passed it says so again
                result = box.run(shell, "myHELD=yes; mySTUCK_REPORT=0; fuROUND; fuROUND", STUB_RC=1)
                self.assertEqual(result.stdout.count("[ERROR]"), 2)
                self.assertRegex(result.stdout, re.compile(r"^ => \[ERROR\] The hold is over since ", re.M))

    def test_state_file(self):
        """hold.state is one line "<word> <epoch>", a regular file; a link of that name is
        replaced, its target stays."""
        for shell in SHELLS:
            with self.subTest(shell=shell):
                box = self.sandbox(Path(shell).name)
                target = box.base / "elsewhere"
                target.write_text("keep")
                box.state.symlink_to(target)
                before = int(time.time())
                result = box.run(shell, "fuSTART; fuROUND")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(box.state.is_symlink())
                self.assertEqual(target.read_text(), "keep")
                word, stamp = box.state.read_text().split()
                self.assertEqual(word, "normal")
                self.assertRegex(box.state.read_text(), r"^normal \d+\n$")
                self.assertGreaterEqual(int(stamp), before)
                self.assertEqual([path.name for path in box.data.iterdir() if path.name.startswith("hold.state.")], [])
                box.hold.write_text("")
                box.run(shell, "fuSTART")
                self.assertEqual(box.state_word(), "held")

    def test_module_that_fails_is_named_in_the_ack(self):
        """A module whose idle passes keep failing: hold.ack is written all the same, with one
        line naming it; hold.state stays held, not stuck."""
        for shell in SHELLS:
            with self.subTest(shell=shell):
                box = self.sandbox(Path(shell).name)
                box.hold.write_text("")
                result = box.run(shell, 'myHELD=yes; fuROUND; echo "interval=$myINTERVAL"',
                                 STUB_STARTED=1, STUB_CRASH="redishoneypot")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("interval=5", result.stdout)
                self.assertTrue(box.ack.is_file())
                self.assertEqual(box.ack.read_text(), "failed: redishoneypot\n")
                self.assertEqual(box.state_word(), "held")
                self.assertIn("leaves their honeypots alone", result.stdout)
                # the other modules had their idle pass, nothing was sent
                self.assertTrue(all(" -E " in call for call in box.calls()))
                self.assertIn("-m wordpot", " ".join(box.calls()))
                self.assertEqual([path.name for path in box.data.iterdir() if path.name.startswith("hold.ack.")], [])

    def test_clean_round_writes_an_empty_ack(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                box = self.sandbox(Path(shell).name)
                box.hold.write_text("")
                box.ack.write_text("failed: cowrie\n")
                result = box.run(shell, "myHELD=yes; fuROUND")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(box.ack.read_text(), "")

    def test_end_of_hold_with_a_module_that_fails_goes_normal(self):
        """After the marker is gone, a module that fails keeps nobody idle: it is quarantined and the
        normal pass of the others follows the idle round."""
        for shell in SHELLS:
            with self.subTest(shell=shell):
                box = self.sandbox(Path(shell).name)
                box.ack.write_text("failed: redishoneypot\n")
                result = box.run(shell, 'myHELD=yes; myHOLD_ACKED=yes; fuROUND; '
                                        'echo "interval=$myINTERVAL held=$myHELD"',
                                 STUB_STARTED=1, STUB_CRASH="redishoneypot")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("interval=42 held=\n", result.stdout)
                self.assertFalse(os.path.lexists(box.ack))
                self.assertEqual(box.state_word(), "normal")
                self.assertEqual(self.kinds(box)[-1], "normal")

    HOLD_ROUNDS = ('myHELD=yes\n'
                   'export STUB_CRASH="$CRASH1"; fuROUND\n'
                   'export STUB_CRASH="$CRASH2"; fuROUND\n'
                   'echo "ack2=[$(cat "$ACK")]"\n'
                   'rm -f "$MARKER"\n'
                   'export STUB_CRASH="$CRASH2"; fuROUND; echo "interval=$myINTERVAL held=$myHELD q=[$myQUARANTINE]"\n')

    def hold_rounds(self, box, shell, crash1, crash2, start=""):
        box.hold.write_text("")
        return box.run(shell, start + self.HOLD_ROUNDS, STUB_STARTED=1, CRASH1=crash1, CRASH2=crash2,
                       ACK=box.ack, MARKER=box.hold)

    @staticmethod
    def normal_calls(box):
        return [call for call in box.calls() if " -E " not in call]

    def assert_quarantined(self, box, result, module):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"interval=42 held= q=[ {module}]", result.stdout)
        self.assertEqual(box.quarantined(), [module])
        self.assertEqual(box.state_word(), "normal")
        # the normal pass after the hold runs with the module off
        last = self.normal_calls(box)[-1]
        self.assertIn(f"-c {box.base / 'normal'}/", last)
        self.assertIn(f"\n{module} = false\n", (box.base / "normal" / "ews.cfg").read_text())
        self.assertIn(f"except {module}: it sends only once", result.stdout)

    def test_a_module_that_failed_in_the_last_round_is_quarantined(self):
        """redishoneypot fails in every round (named in the first ack): after the marker it stays
        idle-only, the others send."""
        for shell in SHELLS:
            with self.subTest(shell=shell):
                box = self.sandbox(Path(shell).name)
                result = self.hold_rounds(box, shell, "redishoneypot", "redishoneypot")
                self.assertIn("ack2=[failed: redishoneypot]", result.stdout)
                self.assert_quarantined(box, result, "redishoneypot")

    def test_a_module_held_at_the_first_ack_that_fails_later_is_quarantined(self):
        """cowrie was held at the first ack (its honeypot was probed), then fails: the ack names it
        for the check, and after the marker it sends nothing until it caught up."""
        for shell in SHELLS:
            with self.subTest(shell=shell):
                box = self.sandbox(Path(shell).name)
                result = self.hold_rounds(box, shell, "", "cowrie")
                self.assertIn("ack2=[failed: cowrie]", result.stdout)
                self.assert_quarantined(box, result, "cowrie")

    def test_a_hold_that_began_without_an_ack_quarantines_a_failing_module(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                box = self.sandbox(Path(shell).name)
                result = self.hold_rounds(box, shell, "redishoneypot", "redishoneypot", start="fuSTART\n")
                self.assert_quarantined(box, result, "redishoneypot")

    def test_a_start_after_a_check_without_ewsposter_catches_up_first(self):
        """The catch-up flag of a check (ewsposter did not run at its end), or hold.state "held"
        without a marker (the container stopped during a hold): the start runs the idle round of
        the end of a hold first, quarantines what fails, removes the flag, then sends."""
        for shell in SHELLS:
            for case in ("catchup", "held", "stuck"):
                with self.subTest(shell=shell, case=case):
                    box = self.sandbox(f"{Path(shell).name}-{case}")
                    if case == "catchup":
                        (box.data / "catchup").write_text("1792233828 tpot check\n")
                    else:
                        box.state.write_text(f"{case} 1792233828\n")
                    result = box.run(shell, 'fuSTART; echo "held=$myHELD"; fuROUND; echo "q=[$myQUARANTINE]"',
                                     STUB_STARTED=1, STUB_CRASH="cowrie")
                    self.assertIn("held=yes", result.stdout, result.stdout + result.stderr)
                    self.assertIn("q=[ cowrie]", result.stdout)
                    kinds = RoundTest.kinds(box)
                    self.assertEqual(kinds[0], "idle")       # nothing sent before the idle round
                    self.assertFalse((box.data / "catchup").exists())
                    self.assertEqual(box.state_word(), "normal")
                    self.assertIn(f"-c {box.base / 'normal'}/", [c for c in box.calls() if " -E " not in c][0])

    def test_a_new_container_reads_the_logs_idle_first(self):
        """No ews.idx (a recreated container, its indexes start at line 1): the start runs the idle
        round of the end of a hold first and says so; nothing is sent before it."""
        for shell in SHELLS:
            with self.subTest(shell=shell):
                box = self.sandbox(Path(shell).name)
                (box.ews / "ews.idx").unlink()
                result = box.run(shell, 'fuSTART; echo "held=$myHELD"; fuROUND; echo "q=[$myQUARANTINE]"')
                self.assertIn("held=yes", result.stdout)
                self.assertIn("No ews.idx (a new container): one idle round first", result.stdout)
                self.assertEqual(RoundTest.kinds(box), ["idle", "normal"])
                self.assertEqual(box.state_word(), "normal")

    def test_the_texts_of_one_module(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                box = self.sandbox(Path(shell).name)
                result = box.run(shell, 'myHELD=yes; fuROUND', STUB_STARTED=1, STUB_CRASH="redishoneypot")
                self.assertIn("except redishoneypot: it sends only once an idle pass of its own", result.stdout)
                result = box.run(shell, 'myQUARANTINE=" cowrie"; fuROUND', STUB_STARTED=1, STUB_CRASH="cowrie")
                self.assertIn("The idle pass of cowrie ended with rc=1.", result.stdout)
                self.assertNotIn("after 0 more tries", result.stdout)

    def test_a_normal_start_sends_at_once(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                box = self.sandbox(Path(shell).name)
                box.state.write_text("normal 1792233828\n")
                result = box.run(shell, 'fuSTART; echo "held=[$myHELD]"')
                self.assertIn("held=[]", result.stdout)

    def test_a_failed_and_a_behind_module_name_the_failed_one_at_once(self):
        """After the ack, a round where one module fails and another is behind rewrites hold.ack
        with the failed module (the check reads it again), no ack of a clean round."""
        for shell in SHELLS:
            with self.subTest(shell=shell):
                box = self.sandbox(Path(shell).name)
                box.hold.write_text("")
                result = box.run(shell, "myHELD=yes; myHOLD_ACKED=yes; myIDLE_PASSES=2; fuROUND; echo rc=$?",
                                 STUB_STARTED=1, STUB_CRASH="cowrie", STUB_LIMITED=10000)
                self.assertIn("rc=0", result.stdout, result.stderr)
                self.assertEqual(box.ack.read_text(), "failed: cowrie\n")

    def test_a_quarantined_module_sends_only_after_its_own_clean_pass(self):
        """Each normal round gives it one idle pass of its own (no tries again); while that fails it
        is in no normal pass; once it is clean and caught up it sends again and the file goes."""
        for shell in SHELLS:
            with self.subTest(shell=shell):
                box = self.sandbox(Path(shell).name)
                script = ('myQUARANTINE=" cowrie"\n'
                          'export STUB_CRASH=cowrie; fuROUND; echo "q1=[$myQUARANTINE]"\n'
                          'fuROUND; echo "q2=[$myQUARANTINE]"\n'
                          'export STUB_CRASH=; fuROUND; echo "q3=[$myQUARANTINE]"\n')
                result = box.run(shell, script, STUB_STARTED=1)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("q1=[ cowrie]\n", result.stdout)
                self.assertIn("q2=[ cowrie]\n", result.stdout)
                self.assertIn("q3=[]\n", result.stdout)
                self.assertIn("cowrie caught up after the hold, it sends again.", result.stdout)
                calls = box.calls()
                idle_cowrie = [call for call in calls if " -E " in call and call.endswith("-m cowrie")]
                self.assertEqual(len(idle_cowrie), 3)          # one per round, no tries again
                normal = self.normal_calls(box)
                self.assertTrue(all(f"-c {box.base / 'normal'}/" in call for call in normal[:2]), normal)
                self.assertEqual(normal[2], f"-u {box.ews}/ews.py -l 0")
                self.assertFalse(box.quarantine.exists())

    def test_a_module_that_never_catches_up_stays_quarantined(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                box = self.sandbox(Path(shell).name)
                script = 'myQUARANTINE=" cowrie"\n' + 'fuROUND\n' * 4 + 'echo "q=[$myQUARANTINE]"\n'
                result = box.run(shell, script, STUB_STARTED=1, STUB_CRASH="cowrie")
                self.assertIn("q=[ cowrie]", result.stdout)
                self.assertEqual(box.quarantined(), ["cowrie"])
                self.assertTrue(all(f"-c {box.base / 'normal'}/" in call for call in self.normal_calls(box)))

    def test_an_expired_marker_quarantines_too(self):
        """A marker older than 30 minutes (a check that was cut off) ends the hold the same way."""
        for shell in SHELLS:
            with self.subTest(shell=shell):
                box = self.sandbox(Path(shell).name)
                box.hold.write_text("")
                old = time.time() - 31 * 60
                os.utime(box.hold, (old, old))
                result = box.run(shell, 'myHELD=yes; fuROUND; echo "interval=$myINTERVAL held=$myHELD q=[$myQUARANTINE]"',
                                 STUB_STARTED=1, STUB_CRASH="redishoneypot")
                self.assert_quarantined(box, result, "redishoneypot")

    def test_the_quarantine_survives_a_start_of_the_container(self):
        """ewsposter started again without tpotinit (it clears the file with the hold): the modules
        stay quarantined; a link in place of the file is never followed."""
        for shell in SHELLS:
            with self.subTest(shell=shell):
                box = self.sandbox(Path(shell).name)
                box.quarantine.write_text("1792233828 cowrie wordpot\n")
                result = box.run(shell, 'fuSTART; echo "q=[$myQUARANTINE]"')
                self.assertIn("q=[ cowrie wordpot]", result.stdout)
                target = box.base / "elsewhere"
                target.write_text("1 keep\n")
                box.quarantine.unlink()
                box.quarantine.symlink_to(target)
                result = box.run(shell, 'fuSTART; echo "q=[$myQUARANTINE]"; myQUARANTINE=" cowrie"; fuQUARANTINE_SAVE')
                self.assertIn("q=[]", result.stdout)
                self.assertFalse(box.quarantine.is_symlink())
                self.assertEqual(target.read_text(), "1 keep\n")
                self.assertEqual(box.quarantined(), ["cowrie"])

    def test_failed_round_writes_no_ack(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                box = self.sandbox(Path(shell).name)
                box.hold.write_text("")
                result = box.run(shell, 'myHELD=yes; fuROUND; echo "interval=$myINTERVAL"', STUB_RC=1)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("interval=5", result.stdout)
                self.assertFalse(os.path.lexists(box.ack))
                self.assertIn("No hold.ack", result.stdout)
                self.assertEqual(self.kinds(box), ["idle"] * 4)

    def test_end_of_hold_waits_for_a_clean_round(self):
        """After the marker is gone a failed idle round keeps the rounds idle; the normal pass
        comes only after a clean one."""
        for shell in SHELLS:
            with self.subTest(shell=shell):
                box = self.sandbox(Path(shell).name)
                box.ack.write_text("")
                script = (
                    'myHELD=yes\n'
                    'export STUB_RC=1\n'
                    'fuROUND; echo "interval=$myINTERVAL held=$myHELD"\n'
                    'fuROUND; echo "interval=$myINTERVAL held=$myHELD"\n'
                    'export STUB_RC=0\n'
                    'fuROUND; echo "interval=$myINTERVAL held=$myHELD"\n'
                )
                result = box.run(shell, script)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(re.findall(r"^interval=(\d+) held=(\w*)$", result.stdout, re.M),
                                 [("5", "yes"), ("5", "yes"), ("42", "")])
                self.assertEqual(self.kinds(box), ["idle"] * 9 + ["normal"])
                self.assertFalse(os.path.lexists(box.ack))

    def test_hold_on_at_the_start_goes_on_idle(self):
        """A restart while the marker is there: no normal pass first, the stale ack is gone."""
        for shell in SHELLS:
            with self.subTest(shell=shell):
                box = self.sandbox(Path(shell).name)
                box.hold.write_text("")
                box.ack.write_text("")
                result = box.run(shell, f'fuSTART; [ -e "{box.ack}" ] || echo no-ack; fuROUND')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("no-ack", result.stdout)
                self.assertEqual(self.kinds(box), ["idle"])
                self.assertTrue(box.ack.is_file())

    def test_no_hold_at_the_start(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                box = self.sandbox(Path(shell).name)
                box.ack.write_text("")
                result = box.run(shell, 'fuSTART; echo "held=$myHELD"; fuROUND')
                self.assertIn("held=\n", result.stdout)
                self.assertFalse(os.path.lexists(box.ack))
                self.assertEqual(self.kinds(box), ["normal"])

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
        self.assertEqual(len(re.findall(r'fuRUN "\$\{myPYTHON\}" -u "\$\{myEWS\}/ews.py"', text)), 3)
        self.assertIn('fuRUN sleep "${myINTERVAL}"', text)
        self.assertIn('fuRUN sleep "${myIDLE_RETRY_PAUSE}"', text)
        self.assertNotIn("exec ", text)
        self.assertRegex(text, r'myINTERVAL=\$\{mySLEEP\}\n  fuSTART\n  while :;')
        self.assertRegex(text, r'case "\$\{0##\*/\}" in\n  entrypoint\.sh\) fuMAIN ;;\nesac\n$')


class TpotinitClearTest(unittest.TestCase):
    """tpotinit removes hold, hold.ack, check.lock, quarantine and catchup in <data>/ews on every start (the logs
    are rotated and ewsposter starts with new indexes then), never through a link."""

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

    def test_removes_the_five_names_only(self):
        for name in ("hold", "hold.ack", "check.lock", "quarantine", "catchup", "hold.old", "conf"):
            (self.ews / name).write_text("")
        self.clear()
        self.assertEqual(sorted(p.name for p in self.ews.iterdir()), ["conf", "hold.old"])

    def test_a_lock_held_by_a_running_check_stays(self):
        import fcntl
        lock = self.ews / "check.lock"
        lock.write_text("")
        (self.ews / "hold").write_text("")
        handle = os.open(lock, os.O_RDWR)
        self.addCleanup(os.close, handle)
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        output = self.clear()
        self.assertIn("check.lock is held by a running check, it stays.", output)
        self.assertTrue(lock.exists())
        self.assertFalse((self.ews / "hold").exists())
        fcntl.flock(handle, fcntl.LOCK_UN)
        self.clear()
        self.assertFalse(lock.exists())

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
        self.assertIn("is a link", self.clear())
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
    # ewsposter reads a log of an older release of the honeypot, on purpose
    OLDER_LOG = {
        ("MAILONEY", "logfile"): "mailoney 3 writes log/log.json; ewsposter reads it after its PR #17",
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

    def test_module_keys_are_true_or_false(self):
        """ews.py runs a module whose key is not empty, the fallback per module lists it the
        same way; both agree only when the key says true or false."""
        for section in self.modules():
            with self.subTest(section=section):
                self.assertIn(self.config.get(section, section.lower()), ("true", "false"))

    def test_older_logs_say_why(self):
        text = EWS_CFG.read_text()
        for (section, key), reason in self.OLDER_LOG.items():
            with self.subTest(section=section):
                self.assertIn(f"[{section}]\n# {reason}\n", text)
                self.assertNotIn(self.config.get(section, key), self.paths[section.lower()])

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
                    if (section, key) in self.OLDER_LOG:
                        continue
                    if key == "logdir":
                        self.assertIn(value, [os.path.dirname(path) for path in logstash if "*" in path])
                    elif (section, key) in self.SAME_FOLDER:
                        self.assertIn(os.path.dirname(value), [os.path.dirname(path) for path in logstash])
                    else:
                        self.assertIn(value, logstash)

    def test_logs_are_rotated(self):
        """ewsposter starts its indexes anew with every container, the logs it reads must be
        rotated (emptied) on every start of T-Pot, or it reads them from line 1 again; the same
        for the logs of Logstash."""
        globs = [line.strip() for line in LOGROTATE.read_text().splitlines() if line.startswith("/data/")]
        self.assertIn("/data/citrixhoneypot/log/server.log", globs)

        def rotated(path):
            if "*" in path:      # a glob of Logstash: a glob of logrotate in the same folder
                return any(os.path.dirname(glob) == os.path.dirname(path) for glob in globs)
            return any(fnmatch.fnmatchcase(path, glob) for glob in globs)

        reads = []
        for section in self.modules():
            for key in ("logfile", "attackerfile", "sqlitedb", "logdir"):
                if self.config.has_option(section, key) and (section, key) not in self.OLDER_LOG:
                    value = self.config.get(section, key).rstrip("/")
                    reads.append(value + "/*" if key == "logdir" else value)
        reads += [path for paths in self.paths.values() for path in paths]
        for path in reads:
            with self.subTest(path=path):
                self.assertTrue(rotated(path), f"{path} is not in {LOGROTATE.name}")

    def test_payload_folders_are_made_by_tpotinit(self):
        folders = {path.rstrip("/") for path in re.findall(r'"(/data/[^"$]+)"', CLEAN.read_text())}
        for section in self.modules():
            for key in ("payloaddir", "malwaredir"):
                if self.config.has_option(section, key):
                    with self.subTest(section=section, key=key):
                        self.assertIn(self.config.get(section, key).rstrip("/"), folders)


if __name__ == "__main__":
    unittest.main()
