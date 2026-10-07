"""install.sh, uninstall.sh, genuser.sh and deploy.sh look like the other T-Pot scripts: the T-Pot
logo at a terminal (never in the marks mode), the help in one style, usage errors with a hint,
spinners for the long steps and a summary at the end.

Every run has a temporary HOME with stubs for sudo, docker, systemctl, ansible-playbook, ss, grc and
gum, and the os-release of a Debian 13 (an awk that reads $HOME/os-release instead of the host's):
nothing touches the host, the real checkout, its .env or ~/.config/tpotce, nothing calls docker.
"""

import os
import re
import shutil
import subprocess
import time
import unittest

from tpotctl.tests import test_scripts as base
from tpotctl.tests import test_ui as ui

from tpotctl import installer

REPO = base.REPO
AWK = shutil.which("awk") or "/usr/bin/awk"
GUM_VERSION = re.search(r'myUI_GUM_VERSION="([^"]+)"', base.read("installer/lib/ui.sh")).group(1)
NEW = "99.1.0"

STUBS = {
    # the os-release of the test, not the one of the host
    "awk": "#!/bin/sh\nn=$#\nwhile [ \"$n\" -gt 0 ]; do\n  a=$1; shift; n=$((n - 1))\n"
           "  [ \"$a\" = /etc/os-release ] && a=\"$HOME/os-release\"\n  set -- \"$@\" \"$a\"\ndone\n"
           f"exec {AWK} \"$@\"\n",
    "ss": "#!/bin/sh\nexit 0\n",
    "grc": "#!/bin/sh\necho \"grc $*\" >> \"$HOME/calls\"\n",
    "shuf": "#!/bin/sh\necho honey\n",
    "htpasswd": "#!/bin/sh\nread -r pw\necho \"$4:\\$2y\\$05\\$hash\"\n",
    "ansible-playbook": "#!/bin/sh\necho \"ansible-playbook $*\" >> \"$HOME/calls\"\n"
                        "case \" $* \" in *\" --list-tasks \"*)\n"
                        "  printf '      Task one\\tTAGS: [Debian]\\n      Task two\\tTAGS: [Debian]\\n'; exit 0 ;;\nesac\n"
                        "echo 'PLAY [T-Pot] ****'\necho 'TASK [Task one] ****'\necho 'ok: [127.0.0.1]'\n"
                        "echo 'TASK [Task two] ****'\n"
                        "if [ -n \"$FAKE_PLAYBOOK_RC\" ]; then echo 'fatal: [127.0.0.1]: FAILED! => {}'; "
                        "exit \"$FAKE_PLAYBOOK_RC\"; fi\necho 'changed: [127.0.0.1]'\n",
    "docker": "#!/bin/sh\necho \"docker $*\" >> \"$HOME/calls\"\ncase \"$*\" in\n"
              "  *'config --images'*) printf 'ghcr.io/t/cowrie:1\\nghcr.io/t/nginx:1\\n' ;;\n"
              "  *' pull'*) if [ -n \"$FAKE_PULL_SLOW\" ]; then sleep 2; touch \"$HOME/marker\"; fi\n"
              "             echo ' cowrie Pulled' >&2; echo ' nginx Pulled' >&2; exit \"${FAKE_PULL_RC:-0}\" ;;\n"
              "esac\n",
}
# a gum that confirms with FAKE_GUM_CONFIRM (no by default), styles plain text and spins in the foreground;
# a spin whose title has FAKE_GUM_STOP ends at once with 130, as gum does on Ctrl+C
GUM = ("#!/bin/sh\ncase \"$1\" in\n"
       f"  --version) echo 'gum version v{GUM_VERSION}' ;;\n"
       "  confirm) echo confirm >> \"$HOME/gum.calls\"; exit \"${FAKE_GUM_CONFIRM:-1}\" ;;\n"
       "  style) while [ \"$#\" -gt 0 ] && [ \"$1\" != \"--\" ]; do shift; done; shift\n"
       "         for a in \"$@\"; do echo \"$a\"; done ;;\n"
       "  spin) if [ -n \"$FAKE_GUM_STOP\" ]; then case \"$*\" in *\"$FAKE_GUM_STOP\"*) exit 130 ;; esac; fi\n"
       "        while [ \"$#\" -gt 0 ] && [ \"$1\" != \"--\" ]; do shift; done; shift; exec \"$@\" ;;\n"
       "esac\nexit 0\n")


def write(path, text, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as out:
        out.write(text)
    os.chmod(path, mode)


def options_of(script):
    """The letters of the getopts string of a script."""
    return re.sub(r"[^A-Za-z]", "", re.search(r'getopts ":([^"]+)"', base.read(script)).group(1))


class Scripts(base.Harness):
    """The harness of test_scripts plus the stubs, a gum, a Debian 13 and a ~/tpotce."""

    def setUp(self):
        super().setUp()
        for name, text in STUBS.items():
            write(os.path.join(self.bin, name), text, 0o755)
        write(os.path.join(self.home, "os-release"), 'NAME="Debian GNU/Linux"\nVERSION_ID="13"\n')
        self.data = os.path.join(self.home, ".local", "share")
        write(os.path.join(self.data, "tpotce", "bin", "gum"), GUM, 0o755)
        self.tpotce = os.path.join(self.home, "tpotce")

    def calls(self):
        path = os.path.join(self.home, "calls")
        if not os.path.exists(path):
            return ""
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def env(self, **extra):
        env = {"PATH": f"{self.bin}:{os.environ['PATH']}", "HOME": self.home, "LANG": "en_US.UTF-8",
               "XDG_CONFIG_HOME": os.path.join(self.home, "config"), "XDG_DATA_HOME": self.data,
               "TERM": "xterm-256color", "COLORTERM": "truecolor"}
        env.update(extra)
        return env

    def run_merged(self, script, *args, **env):
        """stdout and stderr in one, as the assistant reads install.sh -M (tpotctl/engine.py)."""
        return subprocess.run(["bash", script] + list(args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              universal_newlines=True, env=self.env(TPOT_GUM="off", **env), cwd=self.home,
                              stdin=subprocess.DEVNULL, timeout=60)

    def at_terminal(self, script, *args, cols=120, rows=49, **env):
        quoted = " ".join("'" + a.replace("'", "'\\''") + "'" for a in (script,) + args)
        return ui.at_terminal(f"exec bash {quoted}", self.env(**env), cols, rows, source="/dev/null", timeout=60)

    def assert_help(self, script, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        for head in ("Usage:", "Options:", "Examples:"):
            self.assertIn(head, result.stdout)
        for letter in options_of(script):
            self.assertRegex(result.stdout, rf"\n  -{letter}[ \n]", letter)

    def assert_usage_error(self, result, text, script):
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(text, result.stderr)
        self.assertIn(f"{script} -h shows the options.", result.stderr)
        self.assertNotIn("Options:", result.stdout + result.stderr)


class InstallShTest(Scripts):

    def setUp(self):
        super().setUp()
        # the clone the playbook would make: the installer finds its playbook, nothing is cloned
        write(os.path.join(self.tpotce, "installer", "install", "tpot.yml"), "- hosts: all\n")
        for edition in ("standard", "sensor"):
            write(os.path.join(self.tpotce, "compose", f"{edition}.yml"), "services: {}\n")
        write(os.path.join(self.tpotce, ".env"), "TPOT_TYPE=HIVE\nWEB_USER=\n")
        # install.sh as it runs from a download: no clone around it
        self.script = os.path.join(self.home, "src", "install.sh")
        write(self.script, base.read("install.sh"), 0o755)

    def install(self, *args, **env):
        return self.run_merged(self.script, *args, TPOT_INSTALL_PACKAGES_DONE="1", **env)

    def test_help(self):
        self.assert_help("install.sh", self.run_script(self.script, "-h"))

    def test_usage_errors(self):
        for args, text in ((["-Z"], "Unknown option -Z."), (["-t"], "Option -t requires an argument."),
                           (["-t", "x"], "Invalid installation type: x"),
                           (["-s"], "-t is required"), (["-s", "-t", "h"], "-u and -p (or -P) are required"),
                           (["-c", os.path.join(self.home, "missing.yml")], "does not exist"),
                           (["-c", self.script, "-t", "i"], "With -c the type is h")):
            with self.subTest(args=args):
                self.assert_usage_error(self.run_script(self.script, *args), text, "install.sh")

    def feed(self, text):
        progress = installer.Progress()
        for line in text.splitlines():
            progress.feed(line)
        return progress

    def test_marks_run_feeds_the_progress(self):
        result = self.install("-s", "-M", "-t", "s")
        self.assertEqual(result.returncode, 0, result.stdout)
        progress = self.feed(result.stdout)
        self.assertEqual((progress.phase, progress.tasks, progress.tasks_done), ("done", 2, 2))
        self.assertEqual((progress.images, progress.images_done), (2, 2))
        self.assertEqual((progress.failed_task, progress.warnings, progress.fraction), ("", [], 1.0))
        # the pull passes through for the assistant, it comes after its mark
        self.assertLess(result.stdout.index("@@tpot phase pull"), result.stdout.index(" cowrie Pulled"))
        self.assertNotIn("\x1b", result.stdout)
        self.assertNotIn("telekom security", result.stdout)
        self.assertLess(result.stdout.index("@@tpot phase done"), result.stdout.index("### T-Pot is installed"))

    def test_marks_run_warns_about_the_pull(self):
        result = self.install("-s", "-M", "-t", "s", FAKE_PULL_RC="1")
        self.assertEqual(result.returncode, 0, result.stdout)
        progress = self.feed(result.stdout)
        self.assertEqual(len(progress.warnings), 1)
        self.assertEqual(progress.phase, "done")
        self.assertIn("### [WARNING] - Not all images could be pulled", result.stdout)

    def test_plain_run_ends_with_a_summary(self):
        result = self.install("-s", "-t", "s")
        self.assertEqual(result.returncode, 0, result.stdout)
        out = result.stdout
        self.assertIn("### Pulling the images ...", out)
        self.assertIn("### [OK] - Pulling the images", out)
        # the output of the pull is in its log, not on the screen
        self.assertNotIn("cowrie Pulled", out)
        with open(os.path.join(self.home, "install_tpot_pull.log"), encoding="utf-8") as handle:
            self.assertIn("cowrie Pulled", handle.read())
        summary = out[out.index("### T-Pot is installed"):]
        self.assertIn("### [OK] - T-Pot sensor is installed (SENSOR)", summary)
        self.assertIn("### [NEXT] - Reboot, then re-connect via SSH on tcp/64295", summary)
        self.assertIn("tpot sensors add", summary)
        self.assertIn("grc netstat -tulpen", self.calls())

    def test_a_failed_playbook_ends_with_a_summary(self):
        result = self.install("-s", "-t", "s", FAKE_PLAYBOOK_RC="2")
        self.assertEqual(result.returncode, 1, result.stdout)
        summary = result.stdout[result.stdout.index("### T-Pot is not installed"):]
        self.assertIn("### [FAILED] - The playbook failed", summary)
        self.assertIn("install_tpot.log", summary)
        self.assertNotIn(" pull", self.calls())
        marks = self.install("-s", "-M", "-t", "s", FAKE_PLAYBOOK_RC="2")
        self.assertEqual(marks.returncode, 1)
        progress = self.feed(marks.stdout)
        self.assertEqual((progress.phase, progress.failed_task), ("failed", "Task two"))

    def test_a_port_conflict_ends_with_a_summary(self):
        write(os.path.join(self.bin, "ss"), "#!/bin/sh\necho 'LISTEN 0 100 0.0.0.0:25 0.0.0.0:*'\n", 0o755)
        result = self.install("-s", "-t", "s")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("### T-Pot is not installed", result.stdout)
        self.assertIn("### [NEXT] - ", result.stdout)
        self.assertNotIn("ansible-playbook", self.calls())

    def test_logo_at_a_terminal(self):
        for (cols, rows), shown in (((120, 49), True), ((80, 24), True), ((79, 24), False)):
            with self.subTest(size=(cols, rows)):
                out = ui.plain(self.at_terminal(self.script, cols=cols, rows=rows))
                self.assertEqual("telekom security" in out, shown, out[-400:])
                self.assertIn("T-Pot Installer", out)
                self.assertIn("Aborting!", out)
        out = ui.plain(self.at_terminal(self.script))
        self.assertIn("[ t-pot ]", out)
        # the logo comes before the title
        self.assertLess(out.index("telekom security"), out.index("T-Pot Installer"))

    def test_the_version_of_the_logo(self):
        # an older clone in ~/tpotce says nothing about what this installer installs
        write(os.path.join(self.tpotce, "version"), "99.9.9\n")
        self.assertNotIn("99.9.9", ui.plain(self.at_terminal(self.script, "-b", "feature-x")))
        self.assertIn("[ t-pot ]", ui.plain(self.at_terminal(self.script, "-b", "feature-x")))
        # a tag
        self.assertIn("[ t-pot 99.3.0 ]", ui.plain(self.at_terminal(self.script, "-b", "99.3.0")))
        self.assertIn("[ t-pot 99.3.0 ]", ui.plain(self.at_terminal(self.script, "-b", "v99.3.0")))
        # the clone this script runs from wins
        write(os.path.join(self.home, "src", "version"), NEW + "\n")
        self.assertIn(f"[ t-pot {NEW} ]", ui.plain(self.at_terminal(self.script, "-b", "99.3.0")))

    def test_ctrl_c_stops_the_pull(self):
        out = ui.plain(stopped(self, self.script, "-s", "-t", "s", TPOT_INSTALL_PACKAGES_DONE="1",
                               FAKE_GUM_STOP="Pulling the images", FAKE_PULL_SLOW="1"))
        self.assertIn("rc=130", out)
        self.assertIn("Stopped: Pulling the images", out)
        self.assertIn("The installation was stopped", out)
        self.assertFalse(re.search(r"^ *T-Pot is installed *$", out, re.M), out[-800:])   # not its summary
        self.assertNotIn("Not all images could be pulled", out)
        self.assertNotIn("grc netstat", self.calls())
        went_no_further(self)

    def test_no_logo_in_the_marks_mode(self):
        out = ui.plain(self.at_terminal(self.script, "-s", "-M", "-t", "s", TPOT_INSTALL_PACKAGES_DONE="1"))
        self.assertNotIn("telekom security", out)
        self.assertIn("@@tpot phase done", out)
        self.assertIn(" cowrie Pulled", out)
        out = ui.plain(self.at_terminal(self.script, TPOT_MARKS="1"))
        self.assertNotIn("telekom security", out)
        self.assertIn("T-Pot Installer", out)


class UninstallShTest(Scripts):

    def setUp(self):
        super().setUp()
        # uninstall.sh runs from ~/tpotce, as on a T-Pot host
        self.script = os.path.join(self.tpotce, "uninstall.sh")
        write(self.script, base.read("uninstall.sh"), 0o755)
        write(os.path.join(self.tpotce, "installer", "lib", "ui.sh"), base.read("installer/lib/ui.sh"))
        write(os.path.join(self.tpotce, "version"), NEW + "\n")
        write(os.path.join(self.tpotce, "update.sh"),
              "#!/bin/sh\necho \"update.sh $*\" >> \"$HOME/calls\"\nexit \"${FAKE_BACKUP_RC:-0}\"\n", 0o755)

    def test_help(self):
        self.assert_help("uninstall.sh", self.run_script(self.script, "-h"))

    def test_usage_errors(self):
        for args, text in ((["-Z"], "Unknown option -Z."), (["-B"], "Option -B requires an argument."),
                           (["-B", os.path.join(self.home, "missing")], "Cannot read the sudo password")):
            with self.subTest(args=args):
                self.assert_usage_error(self.run_script(self.script, *args), text, "uninstall.sh")

    def test_a_run_removes_the_checkout_with_a_spinner(self):
        result = self.run_merged(self.script, "-y")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertFalse(os.path.exists(self.tpotce))
        out = result.stdout
        self.assertIn(f"### Removing {self.tpotce} ...", out)
        self.assertIn(f"### [OK] - Removing {self.tpotce}", out)
        summary = out[out.index("### T-Pot is uninstalled"):]
        self.assertIn("### [NEXT] - Reboot, then re-connect via SSH on tcp/22", summary)
        self.assertIn("~/tpot_backups", summary)
        self.assertNotIn("update.sh", self.calls())

    def test_a_backup_first(self):
        result = self.run_merged(self.script, "-y", "-k")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("update.sh -y --backup-only --full", self.calls())
        self.assertIn("### [OK] - A full backup is in ~/tpot_backups", result.stdout)

    def test_a_failed_backup_ends_with_a_summary(self):
        result = self.run_merged(self.script, "-y", "-k", FAKE_BACKUP_RC="1")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("### T-Pot is not uninstalled", result.stdout)
        self.assertIn("### [FAILED] - The backup failed", result.stdout)
        self.assertNotIn("ansible-playbook", self.calls())
        self.assertTrue(os.path.exists(self.tpotce))

    def test_a_failed_playbook_ends_with_a_summary(self):
        result = self.run_merged(self.script, "-y", FAKE_PLAYBOOK_RC="2")
        self.assertEqual(result.returncode, 1, result.stdout)
        summary = result.stdout[result.stdout.index("### T-Pot is not uninstalled"):]
        self.assertIn("### [FAILED] - The playbook failed", summary)
        self.assertIn("uninstall_tpot.log", summary)
        self.assertTrue(os.path.exists(self.tpotce))

    def test_logo_at_a_terminal(self):
        out = ui.plain(self.at_terminal(self.script))
        self.assertIn(f"[ t-pot {NEW} ]", out)
        self.assertLess(out.index("telekom security"), out.index("T-Pot Uninstaller"))
        self.assertIn("Aborting!", out)
        self.assertTrue(os.path.exists(self.tpotce))
        out = ui.plain(self.at_terminal(self.script, TPOT_MARKS="1"))
        self.assertNotIn("telekom security", out)
        self.assertIn("T-Pot Uninstaller", out)

    def test_ctrl_c_stops_the_removal(self):
        write(os.path.join(self.bin, "rm"), "#!/bin/sh\ncase \"$*\" in *\"/tpotce\")\n"
              "  sleep 2; touch \"$HOME/marker\"; exit 0 ;;\nesac\n"
              f"exec {shutil.which('rm')} \"$@\"\n", 0o755)
        out = ui.plain(stopped(self, self.script, "-y", FAKE_GUM_STOP="Removing"))
        self.assertIn("rc=130", out)
        self.assertIn(f"Stopped: Removing {self.tpotce}", out)
        self.assertIn("The uninstallation was stopped", out)
        self.assertIn(f"sudo rm -rf {self.tpotce}", out)
        self.assertNotIn("T-Pot is uninstalled", out)
        went_no_further(self)


def stopped(case, script, *args, **env):
    """The script at a terminal (120 x 49) and its exit code as rc=<n>, the spin of FAKE_GUM_STOP
    ends at once with 130 like gum on Ctrl+C."""
    quoted = " ".join("'" + a.replace("'", "'\\''") + "'" for a in (script,) + args)
    start = time.time()
    out = ui.at_terminal(f'bash {quoted}; echo "rc=$?"', case.env(**env), 120, 49, source="/dev/null", timeout=60)
    case.assertLess(time.time() - start, 10)
    return out


def went_no_further(case):
    """The step under the spinner (2 s, then $HOME/marker) is gone."""
    time.sleep(2.5)
    case.assertFalse(os.path.exists(os.path.join(case.home, "marker")), "the step went on after Ctrl+C")


class HandoverTest(Scripts):
    """genuser.sh and deploy.sh hand over to tpot; at a terminal the T-Pot logo comes first."""

    def setUp(self):
        super().setUp()
        write(os.path.join(self.tpotce, "installer", "lib", "ui.sh"), base.read("installer/lib/ui.sh"))
        write(os.path.join(self.tpotce, "version"), NEW + "\n")
        write(os.path.join(self.tpotce, "tpot"),
              "#!/bin/sh\n[ \"$1\" = setup ] && exit 0\necho \"tpot $*\" >> \"$HOME/calls\"\n", 0o755)

    CASES = (("genuser.sh", "users add", "T-Pot Web user"), ("deploy.sh", "sensors add", "T-Pot Sensor deploy"))

    def test_without_a_terminal_it_only_hands_over(self):
        for script, command, _title in self.CASES:
            with self.subTest(script=script):
                result = self.run_script(os.path.join(REPO, script), "alice")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertIn(f"tpot {command} alice", self.calls())

    def test_logo_at_a_terminal(self):
        for script, command, title in self.CASES:
            with self.subTest(script=script):
                out = ui.plain(self.at_terminal(os.path.join(REPO, script), "alice"))
                self.assertIn(f"[ t-pot {NEW} ]", out)
                self.assertLess(out.index("telekom security"), out.index(title))
                self.assertIn(f"tpot {command} alice", self.calls())

    def test_no_logo_for_the_help_or_in_the_marks_mode(self):
        for script, command, title in self.CASES:
            with self.subTest(script=script):
                out = ui.plain(self.at_terminal(os.path.join(REPO, script), "-h"))
                self.assertNotIn("telekom security", out)
                self.assertNotIn(title, out)
                self.assertIn(f"tpot {command} -h", self.calls())
                out = ui.plain(self.at_terminal(os.path.join(REPO, script), TPOT_MARKS="1"))
                self.assertNotIn("telekom security", out)


if __name__ == "__main__":
    unittest.main()
