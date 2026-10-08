"""update.sh and restore.sh in the look of the T-Pot scripts: the T-Pot logo at a terminal (never in
the marks mode of the task screen, once per chain), one help style with exit 0, short usage errors with
exit 1, spinners whose output goes through in the marks mode, a summary at the end, the groups of
restore.sh chosen from one list, and the version of update.sh from the file `version`.

Every run has a temporary HOME with stubs for sudo, systemctl, docker and curl: nothing reaches the
network, the real docker or the .env / ~/.config/tpotce of the user running the tests.
"""

import os
import re
import shlex
import shutil
import stat
import subprocess
import time
import unittest

from tpotctl.tests import test_scripts as base
from tpotctl.tests import test_ui as ui

REPO = base.REPO
UPDATE_SH = os.path.join(REPO, "update.sh")
RESTORE_SH = os.path.join(REPO, "restore.sh")
LOGO = "telekom security"         # the credits of the logo, only the logo has them
UNAME = base.UNAME               # Linux, FAKE_UNAME_S says otherwise
# a curl that answers like an Elasticsearch / Kibana: -o files get a successful import
CURL_OK = ('#!/bin/sh\nout=""\nwhile [ $# -gt 0 ]; do [ "$1" = "-o" ] && out="$2"; shift; done\n'
           '[ -n "$out" ] && [ "$out" != /dev/null ] && echo \'{"success":true,"successCount":2}\' > "$out"\n'
           'exit 0\n')


def getopts_letters(text):
    """The option letters of the getopts line of a script."""
    spec = re.search(r'while getopts "([^"]+)" opt', text).group(1)
    return [letter for letter in spec if letter not in ":"]


class Scripts(base.Harness):

    def setUp(self):
        super().setUp()
        # no gum download at a terminal, and the internet check of update.sh fails
        self.stub("curl", "#!/bin/sh\nexit 7\n")
        self.stub("wget", "#!/bin/sh\nexit 4\n")
        self.stub("uname", UNAME)
        self.tpotce = os.path.join(self.home, "tpotce")
        os.makedirs(self.tpotce)

    def stub(self, name, text):
        path = os.path.join(self.bin, name)
        with open(path, "w", encoding="utf-8") as out:
            out.write(text)
        os.chmod(path, 0o755)
        return path

    def env(self, **extra):
        env = {"HOME": self.home, "PATH": f"{self.bin}:{os.environ['PATH']}",
               "XDG_CONFIG_HOME": os.path.join(self.home, "config"),
               "XDG_DATA_HOME": os.path.join(self.home, "data"),
               "TERM": "xterm-256color", "COLORTERM": "truecolor", "LANG": os.environ.get("LANG", "en_US.UTF-8")}
        for key, value in extra.items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = value
        return env

    def terminal(self, script, *args, cols=120, rows=49, cwd=None, **extra):
        """The script in a pty of cols x rows, gum not off (but not there: curl fails)."""
        line = "exec bash " + " ".join(shlex.quote(a) for a in (script,) + args)
        if cwd:
            line = f"cd {shlex.quote(cwd)} && {line}"
        return ui.at_terminal(line, self.env(**extra), cols, rows)

    def run_plain(self, script, *args, stdin=None, cwd=None, **extra):
        """The script without a terminal, TPOT_GUM=off like the Harness, stdin from the text."""
        values = dict({"TPOT_GUM": "off"}, **extra)
        return subprocess.run(["bash", script] + list(args), input=stdin or "", capture_output=True,
                              universal_newlines=True, env=self.env(**values),
                              cwd=cwd or self.home, timeout=60)

    def source_update(self, call, **extra):
        """update.sh sourced (its main section stays out) and one of its functions called."""
        script = f'source {shlex.quote(UPDATE_SH)}\n{call}'
        return subprocess.run(["bash", "-c", script], capture_output=True, universal_newlines=True,
                              env=self.env(TPOT_GUM="off", **extra), cwd=self.tpotce, timeout=60)

    def calls(self):
        path = os.path.join(self.home, "calls")
        if not os.path.exists(path):
            return ""
        with open(path, encoding="utf-8") as handle:
            return handle.read()


class LogoTest(Scripts):

    def test_update_sh_shows_the_logo_at_a_terminal(self):
        out = self.terminal(UPDATE_SH)
        self.assertEqual(out.count(LOGO), 1, out[-600:])
        self.assertIn("T-Pot Updater", out)
        self.assertIn("'-y'", out)

    def test_restore_sh_shows_the_logo_at_a_terminal(self):
        out = self.terminal(RESTORE_SH, "-l")
        self.assertEqual(out.count(LOGO), 1, out[-600:])
        self.assertIn("T-Pot Restorer", out)
        self.assertIn("No backups found.", out)

    def test_the_help_never_shows_it(self):
        """-h is the help and nothing else, at a terminal too (the decision of 8f)."""
        for script in (UPDATE_SH, RESTORE_SH):
            with self.subTest(script=os.path.basename(script)):
                out = self.terminal(script, "-h")
                self.assertNotIn(LOGO, out)
                self.assertIn("Usage:", out)

    def test_no_logo_in_the_marks_mode(self):
        """The task screen of the T-Pot Manager runs them with TPOT_MARKS=1 (and TPOT_GUM=off)."""
        for script, args in ((UPDATE_SH, ()), (RESTORE_SH, ("-l",)), (UPDATE_SH, ("-h",))):
            with self.subTest(script=os.path.basename(script), args=args):
                self.assertNotIn(LOGO, self.terminal(script, *args, TPOT_MARKS="1"))
                self.assertNotIn(LOGO, self.terminal(script, *args, TPOT_MARKS="1", TPOT_GUM="off"))

    def test_no_logo_once_it_was_shown(self):
        """The restart after the self update, and restore.sh run by update.sh, inherit TPOT_LOGO_SHOWN."""
        for script, args in ((UPDATE_SH, ()), (RESTORE_SH, ("-l",))):
            with self.subTest(script=os.path.basename(script)):
                out = self.terminal(script, *args, TPOT_LOGO_SHOWN="1")
                self.assertNotIn(LOGO, out)
                self.assertIn("T-Pot ", out)

    def test_the_restart_keeps_the_environment(self):
        text = base.read("update.sh")
        self.assertIn('exec bash "$0" -y "${myRESTART[@]}"', text)
        code = [line for line in text.split("\n") if not line.lstrip().startswith("#")]
        self.assertEqual([line for line in code if "TPOT_LOGO_SHOWN" in line], [])     # nothing unsets it
        self.assertTrue(re.search(r"^myUI_LOGO=1$", base.read("restore.sh"), re.M))
        # update.sh: the first pass only, the restart is mid-run
        self.assertTrue(re.search(r'^\[ -n "\$\{TPOT_UPDATE_PREPARED\}" \] \|\| myUI_LOGO=1$', text, re.M))

    def test_no_logo_in_the_restarted_update(self):
        """The update.sh of an earlier release restarts into this one without TPOT_LOGO_SHOWN, only
        TPOT_UPDATE_PREPARED (its handover since 24.04.1) says it is the second pass: no logo mid-run."""
        out = self.terminal(UPDATE_SH, TPOT_UPDATE_PREPARED="1", TPOT_LOGO_SHOWN=None)
        self.assertNotIn(LOGO, out)
        self.assertIn("T-Pot Updater", out)
        # the first pass still shows it
        self.assertEqual(self.terminal(UPDATE_SH, TPOT_UPDATE_PREPARED=None).count(LOGO), 1)

    def test_no_logo_without_a_terminal(self):
        result = self.run_plain(UPDATE_SH, TPOT_GUM=None)
        self.assertNotIn(LOGO, result.stdout)
        self.assertIn("### T-Pot Updater", result.stdout)


class HelpTest(Scripts):

    def test_update_sh_help(self):
        result = self.run_plain(UPDATE_SH, "-h")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        out = result.stdout
        for head in ("T-Pot Updater", "Usage: update.sh", "Options:", "Examples:"):
            self.assertIn(head, out)
        for option in ("-y", "-s, --start", "-F, --full", "-o, --backup-only", "-b <branch>", "-r <url>",
                       "-B <file>", "-h"):
            self.assertTrue(re.search(rf"^  {re.escape(option)}  ", out, re.M), option)
        for letter in getopts_letters(base.read("update.sh")):
            self.assertIn(f"-{letter}", out, letter)

    def test_restore_sh_help(self):
        result = self.run_plain(RESTORE_SH, "-h")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        out = result.stdout
        for head in ("T-Pot Restorer", "Usage: restore.sh", "Options:", "Examples:"):
            self.assertIn(head, out)
        for option in ("-l", "-f <archive>", "-y", "-c", "-g <groups>", "-B <file>", "-h"):
            self.assertTrue(re.search(rf"^  {re.escape(option)}  ", out, re.M), option)
        for group in ("git", "patch", "config", "untracked", "data", "elastic"):
            self.assertIn(group, out)

    def test_help_without_ui_sh(self):
        for name in ("update.sh", "restore.sh"):
            with self.subTest(script=name):
                alone = os.path.join(self.home, "old-" + name)
                os.makedirs(alone)
                shutil.copy(os.path.join(REPO, name), alone)
                result = self.run_plain(os.path.join(alone, name), "-h")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("Options:", result.stdout)
                self.assertNotIn("command not found", result.stdout + result.stderr)

    def test_a_wrong_option(self):
        for script, args, text in ((UPDATE_SH, ("-Z",), "Unknown option -Z."),
                                   (UPDATE_SH, ("--nonsense",), "Unknown option --nonsense."),
                                   (UPDATE_SH, ("-y", "-b"), "Option -b requires an argument."),
                                   (RESTORE_SH, ("-Z",), "Unknown option -Z."),
                                   (RESTORE_SH, ("-f",), "Option -f requires an argument."),
                                   (RESTORE_SH, ("-g", "config,nonsense"), "There is no group nonsense")):
            with self.subTest(args=args):
                result = self.run_plain(script, *args)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(text, result.stderr)
                self.assertIn(f"{os.path.basename(script)} -h shows the options.", result.stderr)
                self.assertNotIn("Options:", result.stdout)              # a hint, not the whole help
                self.assertEqual(self.calls(), "")                       # nothing was run


class LinuxOnlyTest(Scripts):
    """update.sh and restore.sh run on a T-Pot host: outside Linux (uname -s) they stop after the
    options, before sudo, docker or git; -h works everywhere. update.sh names the way of mac_win."""

    def setUp(self):
        super().setUp()
        for name in ("sudo", "docker", "git", "systemctl"):
            self.stub(name, f'#!/bin/sh\necho "{name} $*" >> "$HOME/calls"\nexit 0\n')

    def test_they_stop_outside_linux(self):
        for script, args in ((UPDATE_SH, ("-y",)), (UPDATE_SH, ()), (RESTORE_SH, ("-y",)), (RESTORE_SH, ("-l",))):
            for system, name in (("Darwin", "macOS"), ("MINGW64_NT-10.0-19045", "Windows (MINGW64_NT-10.0-19045)")):
                with self.subTest(script=os.path.basename(script), args=args, system=system):
                    result = self.run_plain(script, *args, cwd=self.tpotce, FAKE_UNAME_S=system)
                    me = os.path.basename(script)
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertIn(f"### [ERROR] - {me} does not run on {name}.", result.stderr)
                    self.assertIn(f"{me} runs on Linux: a T-Pot host, a build host or a VM, WSL2 on Windows.",
                                  result.stderr)
                    self.assertNotIn("T-Pot Updater", result.stdout)            # before the banner
                    self.assertNotIn("T-Pot Restorer", result.stdout)
                    self.assertEqual(self.calls(), "")
                    if script == UPDATE_SH:
                        self.assertIn("git pull in ~/tpotce, then tpot customize", result.stderr)

    def test_help_and_usage_errors_work_everywhere(self):
        for script in (UPDATE_SH, RESTORE_SH):
            with self.subTest(script=os.path.basename(script)):
                result = self.run_plain(script, "-h", FAKE_UNAME_S="Darwin")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("Usage:", result.stdout)
                result = self.run_plain(script, "-Z", FAKE_UNAME_S="Darwin")
                self.assertEqual(result.returncode, 1)
                self.assertIn("Unknown option -Z.", result.stderr)
                self.assertNotIn("does not run on", result.stderr)

    def test_wsl2_is_linux(self):
        """RA12: WSL2 says Linux with uname -s, everything else about it says Microsoft (its kernel in
        uname -r / -v / -a, /proc/version): only uname -s counts. A uname that answers anything but -s
        with Windows / Darwin names keeps update.sh going (here to the version check of the empty
        ~/tpotce), and fuUI_LINUX_ONLY reads nothing else (no /proc/version, no other uname)."""
        self.stub("uname", '#!/bin/sh\ncase "$*" in\n  -s) echo Linux ;;\n'
                           '  *) echo "MINGW64_NT-10.0-19045 Darwin 5.15.153.1-microsoft-standard-WSL2" ;;\nesac\n')
        result = self.run_plain(UPDATE_SH, "-y", cwd=self.tpotce)
        self.assertNotIn("does not run on", result.stderr)
        self.assertIn("Checking for version tag", result.stdout)
        result = subprocess.run(["bash", "-c", f"source {shlex.quote(os.path.join(REPO, 'installer', 'lib', 'ui.sh'))}"
                                               "; fuUI_LINUX_ONLY test.sh; echo linux"],
                                capture_output=True, universal_newlines=True, env=self.env(TPOT_GUM="off"),
                                timeout=60)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "linux\n", ""))
        ui_sh = base.read("installer/lib/ui.sh")
        function = re.search(r"^fuUI_LINUX_ONLY \(\) \{\n(.*?)\n\}", ui_sh, re.M | re.S).group(1)
        code = "\n".join(line for line in function.splitlines() if not line.lstrip().startswith("#"))
        self.assertEqual(re.findall(r"\buname\b[^)\n]*", code), ["uname -s 2>/dev/null"])
        self.assertNotIn("/proc", function)


class VersionTest(Scripts):
    """update.sh compares with the version of its own checkout (the file `version`), not a number in it."""

    def checkout(self, name, version, with_script=True):
        path = os.path.join(self.home, name)
        os.makedirs(os.path.join(path, "installer", "lib"))
        if with_script:
            shutil.copy(UPDATE_SH, path)
            shutil.copy(os.path.join(REPO, "installer", "lib", "ui.sh"), os.path.join(path, "installer", "lib"))
        with open(os.path.join(path, "version"), "w", encoding="utf-8") as out:
            out.write(version + "\n")
        return path

    def test_the_version_of_the_checkout_is_the_newest_it_knows(self):
        checkout = self.checkout("next", "99.1.0")
        result = self.run_plain(os.path.join(checkout, "update.sh"), "-y", cwd=checkout)
        out = result.stdout + result.stderr
        self.assertIn("99.1.0 is eligible for the update procedure.", out)
        self.assertIn("cannot be reached", out)                       # the curl stub, nothing went out
        self.assertEqual(result.returncode, 1)

    def test_a_checkout_newer_than_the_update_sh_is_refused(self):
        script = self.checkout("script", "25.0.0")
        checkout = self.checkout("next", "99.1.0", with_script=False)
        result = self.run_plain(os.path.join(script, "update.sh"), "-y", cwd=checkout)
        out = result.stdout + result.stderr
        self.assertIn("99.1.0 cannot be upgraded automatically", out)
        self.assertEqual(result.returncode, 1, out[-400:])
        self.assertNotIn("cannot be reached", out)

    def compare(self, local, script_version):
        """update.sh of a checkout at script_version run in a checkout at local."""
        script = self.checkout("script", script_version)
        checkout = self.checkout("next", local, with_script=False)
        result = self.run_plain(os.path.join(script, "update.sh"), "-y", cwd=checkout)
        return result.stdout + result.stderr

    def test_versions_compare_as_numbers(self):
        """24.04.10 is newer than 24.04.9: number by number, not letter by letter (fuUI_VERSION_GE)."""
        out = self.compare("24.04.9", "24.04.10")
        self.assertIn("24.04.9 is eligible for the update procedure.", out)
        self.assertIn("cannot be reached", out)                       # it went on to the internet check

    def test_a_newer_checkout_is_refused_by_number(self):
        out = self.compare("24.04.10", "24.04.9")
        self.assertIn("24.04.10 cannot be upgraded automatically", out)
        self.assertNotIn("is eligible", out)
        self.assertNotIn("cannot be reached", out)

    def test_spaces_and_crlf_in_the_version_files(self):
        """A version file written on Windows (CRLF) or with spaces is the same version."""
        script = self.checkout("script", " 24.04.10\r")
        checkout = self.checkout("next", "24.04.2 \r", with_script=False)
        result = self.run_plain(os.path.join(script, "update.sh"), "-y", cwd=checkout)
        out = result.stdout + result.stderr
        self.assertIn("### [OK] - 24.04.2 is eligible for the update procedure.", out)
        self.assertNotIn("\r", out)

    def test_no_version_written_into_update_sh(self):
        self.assertFalse(re.search(r'myMASTERVERSION="[0-9]', base.read("update.sh")))


class SummaryTest(Scripts):

    def test_update_sh_ends_with_a_summary_when_it_stops_early(self):
        with open(os.path.join(REPO, "version"), encoding="utf-8") as handle:
            version = handle.read().strip()
        with open(os.path.join(self.tpotce, "version"), "w", encoding="utf-8") as out:
            out.write(version + "\n")
        result = self.run_plain(UPDATE_SH, "-y", cwd=self.tpotce)
        self.assertEqual(result.returncode, 1, result.stdout[-400:])
        tail = result.stdout[result.stdout.index("### The update did not finish"):]
        self.assertIn("### [FAILED] - ", tail)
        self.assertIn("T-Pot was left running.", tail)
        self.assertNotIn("systemctl stop", self.calls())

    def test_update_sh_says_why_the_version_is_refused(self):
        with open(os.path.join(self.tpotce, "version"), "w", encoding="utf-8") as out:
            out.write("20.06.0\n")
        result = self.run_plain(UPDATE_SH, "-y", cwd=self.tpotce)
        self.assertEqual(result.returncode, 1)
        tail = result.stdout[result.stdout.index("### The update did not finish"):]
        self.assertIn("### [FAILED] - 20.06.0 cannot be upgraded automatically", tail)

    def test_the_summary_keeps_the_exit_code(self):
        cases = (('fuDID ok "Pulled."; exit 0', 0, ["### T-Pot is updated", "### [OK] - Pulled."]),
                 ("myBACKUP_ONLY=1; exit 0", 0, ["### The backup is written"]),
                 ("mySTOPPED=1; exit 1", 1, ["### The update did not finish", "### [FAILED] - It stopped",
                                              "### [NEXT] - T-Pot is stopped, 'sudo systemctl start tpot'"]),
                 ('fuDID fail "No room."; exit 1', 1, ["### [FAILED] - No room.", "### T-Pot was left running."]),
                 ("mySTOPPED=1; exit 130", 130, ["### The update was stopped"]))
        for call, rc, lines in cases:
            with self.subTest(call=call):
                result = self.source_update("myRUNNING=1; " + call)
                self.assertEqual(result.returncode, rc, result.stdout + result.stderr)
                for line in lines:
                    self.assertIn(line, result.stdout)
                self.assertEqual(result.stdout.count("[FAILED]"), 1 if rc == 1 else 0, result.stdout)
        self.assertEqual(self.source_update("exit 3").stdout, "")         # not confirmed: no summary

    def test_no_summary_for_the_help_or_without_y(self):
        for args in (("-h",), ()):
            with self.subTest(args=args):
                self.assertNotIn("did not finish", self.run_plain(UPDATE_SH, *args).stdout)

    def test_restore_sh_sums_up_each_group(self):
        archive = base.make_backup(self.home, {"MANIFEST": base.MANIFEST, "env": "TPOT_TYPE=HIVE\nBEFORE=1\n",
                                               "untracked/notes.txt": "x\n"})
        with open(os.path.join(self.tpotce, ".env"), "w", encoding="utf-8") as out:
            out.write("TPOT_TYPE=HIVE\nNOW=1\n")
        os.chmod(os.path.join(self.tpotce, ".env"), 0o444)          # read only: the config group fails
        self.addCleanup(os.chmod, os.path.join(self.tpotce, ".env"), 0o644)
        result = self.run_plain(RESTORE_SH, "-f", archive, "-g", "config,untracked")
        self.assertEqual(result.returncode, 1)
        tail = result.stdout[result.stdout.index("### Restored from"):]
        self.assertTrue(re.search(r"### \[FAILED\] - The configuration .*not restored", tail), tail)
        self.assertIn("### [OK] - Your untracked files", tail)
        self.assertIn("Not restored: config.", tail)

    def test_restore_sh_all_back_says_what_comes_next(self):
        archive = base.make_backup(self.home, {"MANIFEST": base.MANIFEST, "env": "TPOT_TYPE=HIVE\nBEFORE=1\n"})
        result = self.run_plain(RESTORE_SH, "-f", archive, "-g", "config")
        self.assertEqual(result.returncode, 0, result.stdout[-400:] + result.stderr[-400:])
        tail = result.stdout[result.stdout.index("### Restored from"):]
        self.assertIn("### [OK] - The configuration (.env and docker-compose.yml)", tail)
        self.assertIn("### [NEXT] - ", tail)
        self.assertIn("systemctl start tpot", tail)


class RestoreChoiceTest(Scripts):
    """Without -y, -c or -g restore.sh offers the groups of the archive in one list (fuUI_CHOOSE_MANY)."""

    def setUp(self):
        super().setUp()
        self.stub("curl", CURL_OK)
        with open(os.path.join(self.tpotce, ".env"), "w", encoding="utf-8") as out:
            out.write("TPOT_TYPE=HIVE\nNOW=1\n")
        self.archive = base.make_backup(self.home, {
            "MANIFEST": base.MANIFEST, "env": "TPOT_TYPE=HIVE\nBEFORE=1\n", "untracked/notes.txt": "x\n",
            "elastic/kibana_export.ndjson": "{}\n"})

    def env_text(self):
        with open(os.path.join(self.tpotce, ".env"), encoding="utf-8") as handle:
            return handle.read()

    def test_choose_groups_by_number(self):
        result = self.run_plain(RESTORE_SH, "-f", self.archive, stdin="1,3\n", TPOT_KIBANA_TIMEOUT="0")
        self.assertEqual(result.returncode, 0, result.stdout[-600:] + result.stderr[-600:])
        from tpotctl import ops
        listed = result.stderr
        self.assertIn("### What should be restored?", listed)
        self.assertIn(f"1) [x] {ops.GROUP_TEXT['config']}", listed)      # --all: everything marked first
        self.assertIn(f"2) [x] {ops.GROUP_TEXT['untracked']}", listed)
        self.assertIn(f"3) [x] {ops.GROUP_TEXT['elastic']}", listed)
        self.assertIn("BEFORE=1", self.env_text())                        # 1: config
        self.assertFalse(os.path.exists(os.path.join(self.tpotce, "notes.txt")))   # 2 left out
        self.assertIn("Imported 2 Kibana objects.", result.stdout)        # 3: elastic
        self.assertIn("systemctl stop tpot.service", self.calls())

    def test_enter_takes_everything(self):
        result = self.run_plain(RESTORE_SH, "-f", self.archive, stdin="\n", TPOT_KIBANA_TIMEOUT="0")
        self.assertEqual(result.returncode, 0, result.stdout[-600:] + result.stderr[-600:])
        self.assertTrue(os.path.exists(os.path.join(self.tpotce, "notes.txt")))
        self.assertIn("BEFORE=1", self.env_text())

    def test_none_or_no_answer_leaves_everything(self):
        for answer in ("n\n", ""):
            with self.subTest(answer=answer):
                result = self.run_plain(RESTORE_SH, "-f", self.archive, stdin=answer)
                self.assertEqual(result.returncode, 0, result.stderr[-400:])
                self.assertIn("Nothing selected, leaving everything as it is.", result.stdout)
                self.assertIn("NOW=1", self.env_text())
                self.assertNotIn("systemctl", self.calls())

    def test_no_question_left_in_restore_sh(self):
        text = base.read("restore.sh")
        rest = text.replace(text[text.index("# >>> plain fallback"):text.index("# <<< plain fallback")], "")
        self.assertNotIn("fuUI_CONFIRM", rest)
        self.assertIn("fuUI_CHOOSE_MANY --all", rest)


class TaskScreenTest(Scripts):
    """With TPOT_MARKS=1 and TPOT_GUM=off (screens/task.py) the output stays marks and plain lines; what
    runs under a spinner goes through to the task screen instead of into the log."""

    def restore_marks(self, **extra):
        self.stub("systemctl", '#!/bin/sh\necho "systemctl $*" >> "$HOME/calls"\necho "unit says hello"\n')
        archive = base.make_backup(self.home, {"MANIFEST": base.MANIFEST, "env": "TPOT_TYPE=HIVE\nBEFORE=1\n"})
        with open(os.path.join(self.tpotce, ".env"), "w", encoding="utf-8") as out:
            out.write("TPOT_TYPE=HIVE\nNOW=1\n")
        return self.run_plain(RESTORE_SH, "-f", archive, "-g", "config", **extra)

    def test_restore_sh_in_the_task_screen(self):
        result = self.restore_marks(TPOT_MARKS="1")
        self.assertEqual(result.returncode, 0, result.stdout[-400:] + result.stderr[-400:])
        out = result.stdout
        self.assertNotIn("\x1b", out + result.stderr)
        self.assertNotIn(LOGO, out)
        for mark in ("@@tpot phase pick", "@@tpot phase stop", "@@tpot phase config", "@@tpot phase done"):
            self.assertIn(mark, out)
        self.assertIn("unit says hello", out)                              # the spinner passes it on
        for line in out.split("\n"):
            self.assertTrue(line == "" or line.startswith(("@@tpot ", "### ", "    ")) or line == "unit says hello",
                            line)

    def test_restore_sh_without_marks_keeps_the_output_in_its_log(self):
        result = self.restore_marks()
        self.assertEqual(result.returncode, 0, result.stdout[-400:])
        self.assertNotIn("unit says hello", result.stdout)
        self.assertNotIn("@@tpot", result.stdout)
        with open(os.path.join(self.home, "tpot_backups", "restore.log"), encoding="utf-8") as handle:
            self.assertIn("unit says hello", handle.read())

    def docker_with_images(self):
        with open(os.path.join(self.tpotce, ".env"), "w", encoding="utf-8") as out:
            out.write("TPOT_REPO=ghcr.io/telekom-security\nTPOT_VERSION=24.04.2\n")
        self.stub("docker", '#!/bin/sh\necho "docker $*" >> "$HOME/calls"\ncase "$*" in\n'
                            '  *pull*) echo "Pulled cowrie" ;;\n'
                            '  images*) echo "ghcr.io/telekom-security/cowrie:24.04.1" ;;\n'
                            '  rmi*) echo "Untagged: $2" ;;\nesac\n')

    def test_the_sourced_update_sh_runs_nothing(self):
        result = self.source_update('echo "sourced"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "sourced\n")
        self.assertEqual(self.calls(), "")

    def test_the_pull_and_the_cleanup_go_through_in_the_marks_mode(self):
        self.docker_with_images()
        result = self.source_update("fuUPDATER", TPOT_MARKS="1")
        out = result.stdout
        self.assertIn("Pulled cowrie", out)
        self.assertIn("@@tpot phase cleanup", out)
        self.assertIn("Untagged: ghcr.io/telekom-security/cowrie:24.04.1", out)
        self.assertIn("docker rmi ghcr.io/telekom-security/cowrie:24.04.1", self.calls())
        self.assertNotIn("\x1b", out)

    def test_the_pull_without_marks_goes_into_the_log(self):
        self.docker_with_images()
        result = self.source_update("fuLOG_START; fuUPDATER")
        self.assertNotIn("Pulled cowrie", result.stdout)
        self.assertIn("### [OK] - Pulling the images", result.stdout)
        with open(os.path.join(self.home, "tpot_backups", "update.log"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("Pulled cowrie", text)
        self.assertIn("Untagged:", text)

    def test_tpot_setup_runs_under_the_spinner(self):
        launcher = os.path.join(self.tpotce, "tpot")
        with open(launcher, "w", encoding="utf-8") as out:
            out.write("#!/bin/sh\necho \"installing the packages\"\n")
        os.chmod(launcher, 0o755)
        call = f'myTPOT_LINK={shlex.quote(os.path.join(self.home, "link"))}; fuLOG_START; fuTPOT_SETUP'
        self.assertIn("installing the packages", self.source_update(call, TPOT_MARKS="1").stdout)
        result = self.source_update(call)
        self.assertNotIn("installing the packages", result.stdout)
        self.assertIn("The T-Pot Manager is ready.", result.stdout)

    def test_what_runs_under_a_spinner(self):
        text = base.read("update.sh")

        def body(name):
            return re.search(rf"function {name} \(\) \{{.*?\n\}}", text, re.S).group(0)
        self.assertIn("fuUI_SPIN", body("fuUPDATER"))
        self.assertIn("fuUI_SPIN", body("fuREMOVEOLDIMAGES"))
        self.assertIn("fuUI_SPIN", body("fuTPOT_SETUP"))
        backup = body("fuBACKUP")
        self.assertTrue(re.search(r"sudo -v\n(?:\s*#.*\n)*\s*(?:if ! )?fuUI_SPIN (?:[^\n]|\\\n)*?sudo tar cf", backup),
                        backup[-900:])
        # the Elastic export asks Kibana and waits for it, the pause waits for Ctrl+C: both in the open
        self.assertNotIn("fuUI_SPIN", body("fuEXPORT_ELASTIC"))
        self.assertNotIn("fuUI_SPIN", body("fuCHECK_ELASTIC"))


class SshdDropinTest(Scripts):
    """K: update.sh does not run the playbook, so an earlier T-Pot keeps its sshd drop-in with
    "AcceptEnv COLORTERM" only. fuSSHD_DROPIN brings it to the line of this release, but only the very
    file an earlier T-Pot wrote; sshd -t checks it (with /run/sshd first, as the playbook does) and a
    failed check puts the old line back; SSH is reloaded as ssh or sshd. Stubs only: the drop-in and
    /run/sshd are in the temporary HOME, sudo, sshd and systemctl write what they are asked to do."""

    OLD = "AcceptEnv COLORTERM\n"
    NEW = "AcceptEnv COLORTERM LC_TERMINAL LC_TERMINAL_VERSION\n"

    def setUp(self):
        super().setUp()
        self.dropin = os.path.join(self.home, "etc", "ssh", "sshd_config.d", "tpot.conf")
        os.makedirs(os.path.dirname(self.dropin))
        self.run_dir = os.path.join(self.home, "run", "sshd")
        os.makedirs(os.path.dirname(self.run_dir))
        self.stub("sudo", '#!/bin/sh\necho "sudo $*" >> "$HOME/calls"\nexec "$@"\n')
        # sshd -t: whether /run/sshd is there by then and what the drop-in says, FAKE_SSHD_RC its answer
        self.stub("sshd", '#!/bin/sh\n{ echo "sshd $*"; [ -d "$myTPOT_SSHD_RUN" ] && echo "  run dir there"\n'
                          '  sed "s/^/  file: /" "$myTPOT_SSHD_DROPIN"; } >> "$HOME/calls"\n'
                          '[ -n "$FAKE_SSHD_RC" ] && echo "sshd: bad configuration option" >&2\n'
                          'exit "${FAKE_SSHD_RC:-0}"\n')
        self.stub("systemctl", '#!/bin/sh\necho "systemctl $*" >> "$HOME/calls"\n'
                               'case "$*" in "is-active --quiet ${FAKE_ACTIVE:-ssh}") exit 0 ;; is-active*) exit 3 ;; esac\n')

    def write_dropin(self, text, mode=0o644):
        with open(self.dropin, "w", encoding="utf-8", newline="") as out:
            out.write(text)
        os.chmod(self.dropin, mode)

    def dropin_text(self):
        with open(self.dropin, encoding="utf-8", newline="") as handle:
            return handle.read()

    def migrate(self, **extra):
        return self.source_update("fuSSHD_DROPIN; fuEND_LIST", myTPOT_SSHD_DROPIN=self.dropin,
                                  myTPOT_SSHD_RUN=self.run_dir, **extra)

    def source_update(self, call, **extra):
        # fuEND_LIST: what the summary would say (myDONE)
        call = call.replace("fuEND_LIST", 'for myITEM in "${myDONE[@]}"; do echo "done ${myITEM}"; done')
        return super().source_update(call, **extra)

    def test_the_line_of_an_earlier_t_pot_is_replaced(self):
        for unit in ("ssh", "sshd"):
            with self.subTest(unit=unit):
                shutil.rmtree(self.run_dir, ignore_errors=True)
                self.write_dropin(self.OLD)
                result = self.migrate(FAKE_ACTIVE=unit)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(self.dropin_text(), self.NEW)
                self.assertEqual(stat.S_IMODE(os.stat(self.dropin).st_mode), 0o644)
                calls = self.calls()
                # the privilege separation directory before the check, the check sees the new line
                self.assertTrue(os.path.isdir(self.run_dir))
                self.assertIn(f"sudo install -d -m 0755 {self.run_dir}", calls)
                self.assertIn("sshd -t\n  run dir there\n  file: " + self.NEW, calls)
                self.assertLess(calls.index("sshd -t"), calls.index(f"sudo systemctl reload {unit}"))
                self.assertIn("done ok:SSH takes LC_TERMINAL along", result.stdout)
                os.remove(os.path.join(self.home, "calls"))

    def test_a_file_of_your_own_stays(self):
        """Anything but the exact old file: yours (more lines, a comment, no line end, already the new
        line, another case, a link), nothing is written, checked or reloaded."""
        for text in ("AcceptEnv COLORTERM\nAcceptEnv FOO\n", "# mine\nAcceptEnv COLORTERM\n", "AcceptEnv COLORTERM",
                     "AcceptEnv COLORTERM\n\n", " AcceptEnv COLORTERM\n", "acceptenv colorterm\n", self.NEW,
                     "AcceptEnv COLORTERM\r\n", ""):
            with self.subTest(text=text):
                self.write_dropin(text)
                result = self.migrate()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.dropin_text(), text)
                self.assertEqual(self.calls(), "")
                self.assertNotIn("done ", result.stdout)
        with self.subTest(case="link"):
            other = os.path.join(self.home, "mine.conf")
            with open(other, "w", encoding="utf-8") as out:
                out.write(self.OLD)
            os.remove(self.dropin)
            os.symlink(other, self.dropin)
            self.migrate()
            self.assertEqual(self.calls(), "")
            with open(other, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), self.OLD)
        with self.subTest(case="missing"):
            os.remove(self.dropin)
            result = self.migrate()
            self.assertFalse(os.path.lexists(self.dropin))
            self.assertEqual(self.calls(), "")

    def test_a_failed_check_puts_the_old_line_back(self):
        self.write_dropin(self.OLD)
        result = self.migrate(FAKE_SSHD_RC="255")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.dropin_text(), self.OLD)
        self.assertNotIn("reload", self.calls())
        self.assertIn("sshd -t did not accept", result.stdout)
        self.assertIn("bad configuration option", result.stdout)
        self.assertIn("done warn:", result.stdout)

    def lock_folder(self, sudo_works=True):
        """r2-RA 1: AlmaLinux, Fedora, RHEL and Rocky keep /etc/ssh/sshd_config.d at 0700 root: the user
        sees neither the file nor what it says, only sudo does. The folder is 000 here, the sudo stub opens
        it for the one command it runs (or fails, FAKE_SUDO_FAIL)."""
        if os.geteuid() == 0:
            self.skipTest("root reads a folder of mode 000")
        folder = os.path.dirname(self.dropin)
        self.stub("sudo", '#!/bin/sh\necho "sudo $*" >> "$HOME/calls"\n'
                          '[ -n "$FAKE_SUDO_FAIL" ] && { echo "sudo: a password is required" >&2; exit 1; }\n'
                          'chmod 700 "$FAKE_LOCKED"; "$@"; rc=$?; chmod 000 "$FAKE_LOCKED"; exit $rc\n')
        os.chmod(folder, 0)
        self.addCleanup(os.chmod, folder, 0o700)
        extra = {"FAKE_LOCKED": folder}
        if not sudo_works:
            extra["FAKE_SUDO_FAIL"] = "1"
        return extra

    def test_a_folder_only_root_can_read_is_read_through_sudo(self):
        self.write_dropin(self.OLD)
        result = self.migrate(**self.lock_folder())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        os.chmod(os.path.dirname(self.dropin), 0o700)
        self.assertEqual(self.dropin_text(), self.NEW)
        self.assertIn("sshd -t\n  run dir there\n  file: " + self.NEW, self.calls())
        self.assertIn("done ok:SSH takes LC_TERMINAL along", result.stdout)

    def test_a_file_of_your_own_in_such_a_folder_stays(self):
        for text in ("AcceptEnv COLORTERM\nAcceptEnv FOO\n", self.NEW):
            with self.subTest(text=text):
                os.chmod(os.path.dirname(self.dropin), 0o700)
                self.write_dropin(text)
                result = self.migrate(**self.lock_folder())
                self.assertEqual(result.returncode, 0, result.stderr)
                os.chmod(os.path.dirname(self.dropin), 0o700)
                self.assertEqual(self.dropin_text(), text)
                self.assertNotIn("tee", self.calls())
                self.assertNotIn("sshd -t", self.calls())
                self.assertNotIn("done ", result.stdout)
                if os.path.exists(os.path.join(self.home, "calls")):
                    os.remove(os.path.join(self.home, "calls"))

    def test_a_folder_sudo_cannot_read_either_is_named_in_the_summary(self):
        self.write_dropin(self.OLD)
        result = self.migrate(**self.lock_folder(sudo_works=False))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        os.chmod(os.path.dirname(self.dropin), 0o700)
        self.assertEqual(self.dropin_text(), self.OLD)
        self.assertNotIn("tee", self.calls())
        warn = [line for line in result.stdout.splitlines() if line.startswith("done warn:")]
        self.assertEqual(len(warn), 1, result.stdout)
        self.assertIn(os.path.dirname(self.dropin), warn[0])
        self.assertIn("LC_TERMINAL", warn[0])

    def test_update_sh_runs_it_after_the_confirmation(self):
        text = base.read("update.sh")
        main = text[text.index("# Main section #"):]
        self.assertLess(main.index('myRUNNING="1"'), main.index("\nfuSSHD_DROPIN\n"))
        self.assertLess(main.index("\nfuTPOT_SETUP\n"), main.index("\nfuSSHD_DROPIN\n"))
        self.assertLess(main.index("\nfuSSHD_DROPIN\n"), main.index("fuMARK phase pull"))
        # not in --backup-only, which ends before
        self.assertLess(main.index("--backup-only: the backup"), main.index("\nfuSSHD_DROPIN\n"))
        self.assertLess(main[main.index("--backup-only: the backup"):].index("exit 0"),
                        main[main.index("--backup-only: the backup"):].index("\nfuSSHD_DROPIN\n"))

    def test_the_old_and_the_new_line_are_the_ones_of_the_playbook(self):
        """The old line is what the regexp of the playbook task replaces, the new one is its line."""
        text = base.read("update.sh")
        function = re.search(r"function fuSSHD_DROPIN \(\) \{.*?\n\}", text, re.S).group(0)
        playbook = base.read("installer/install/tpot.yml")
        line = re.search(r'path: /etc/ssh/sshd_config.d/tpot.conf\n\s*regexp: .*\n\s*line: "([^"]+)"', playbook)
        self.assertIn(f'myNEW="{line.group(1)}"', function)
        self.assertIn(f'myOLD="{self.OLD.strip()}"', function)
        self.assertIn("/etc/ssh/sshd_config.d/tpot.conf", function)
        self.assertIn("/run/sshd", function)


class SshdDocsTest(unittest.TestCase):
    """RC11 and K: the README on true colours over SSH (update.sh, sudo, e.g.), the names of the
    playbook tasks."""

    def section(self):
        readme = base.read("README.md")
        text = readme[readme.index("### True colours over SSH"):]
        return text[:text.index("\n## ")]

    def test_the_readme_says_what_update_sh_does(self):
        section = self.section()
        self.assertNotIn("i.e. Debian 13", section)
        self.assertIn("e.g. Debian 13", section)
        self.assertIn("update.sh", section)

    def test_the_readme_does_not_say_sudo_drops_the_colours(self):
        """r2-RA 2: sudo's own env_check keeps TERM, COLORTERM, LANG and LC_* (Debian 13, Ubuntu 26.04 with
        sudo-rs, Fedora 44, openSUSE Tumbleweed; sudo and sudo -i), so a script under sudo sees the colours
        of the terminal as well: no advice against a problem that is not there."""
        section = self.section()
        self.assertNotIn("--preserve-env", section)
        self.assertNotIn("clean environment", section)
        self.assertNotIn("without `COLORTERM` and `LC_TERMINAL`", section)

    def test_the_readme_on_tmux_and_gnu_screen(self):
        """r2-RA 10, r2-RC 9: in GNU screen (STY, or a TERM of screen* that is no tmux) the rule ignores
        COLORTERM, only TPOT_COLORS (or the choice in tpot.json) helps; a tmux with its default TERM
        screen-256color counts as GNU screen too (TMUX does not come over SSH), tmux-256color does not."""
        from tpotctl import prefs
        section = self.section()
        sentences = re.split(r"(?<=[.:])\s+", " ".join(section.split()))
        screen = [s for s in sentences if "GNU screen" in s and "COLORTERM" in s]
        self.assertTrue(screen, section)
        self.assertTrue(any("only" in s and "TPOT_COLORS" in s for s in screen), screen)
        self.assertIn("`screen*`", section)
        self.assertIn("set -g default-terminal tmux-256color", section)
        self.assertNotIn("`COLORTERM` has to reach the shell in tmux, or `TPOT_COLORS` decides", section)
        # what the README says is the rule
        isolated = {"XDG_CONFIG_HOME": "/nonexistent-tpot-test"}
        self.assertEqual(prefs.detect_colors(dict(isolated, TERM="screen-256color", COLORTERM="truecolor")), "256")
        self.assertEqual(prefs.detect_colors(dict(isolated, TERM="tmux-256color", COLORTERM="truecolor")),
                         "truecolor")
        self.assertEqual(prefs.detect_colors(dict(isolated, TERM="xterm-256color", STY="1.pts", COLORTERM="truecolor")),
                         "256")
        self.assertEqual(prefs.detect_colors(dict(isolated, TERM="screen-256color", TPOT_COLORS="truecolor")),
                         "truecolor")

    def test_the_readme_tells_tmux_on_the_host_from_tmux_on_your_computer(self):
        """r3-RA 5: a TERM of screen* counts as GNU screen only where nothing tells it is tmux, that is a
        tmux on your own computer with SSH in it (TMUX does not come over SSH). A tmux on the T-Pot host
        sets TMUX, there COLORTERM counts with its default TERM screen-256color as well."""
        from tpotctl import prefs
        sentences = re.split(r"(?<=[.:])\s+", " ".join(self.section().split()))
        screen = [s for s in sentences if "`screen*`" in s]
        self.assertTrue(screen)
        for sentence in screen:
            self.assertIn("your own computer", sentence)
        host = [s for s in sentences if "tmux" in s and "T-Pot host" in s and "`TMUX`" in s]
        self.assertTrue(host, sentences)
        self.assertTrue(any("`COLORTERM`" in s for s in host), host)
        # what the README says is the rule
        isolated = {"XDG_CONFIG_HOME": "/nonexistent-tpot-test"}
        self.assertEqual(prefs.detect_colors(dict(isolated, TERM="screen-256color", TMUX="/tmp/tmux-1000/default,1,0",
                                                  COLORTERM="truecolor")), "truecolor")
        self.assertEqual(prefs.detect_colors(dict(isolated, TERM="screen-256color", TMUX="/tmp/tmux-1000/default,1,0")),
                         "256")

    def test_the_task_names_name_both_variables(self):
        install = base.read("installer/install/tpot.yml")
        directory = re.search(r"- name: ([^\n]*)\n(?:\s*#[^\n]*\n)*\s*file:\n\s*path: /etc/ssh/sshd_config.d\n",
                              install).group(1)
        self.assertIn("COLORTERM", directory)
        self.assertIn("LC_TERMINAL", directory)
        remove = base.read("installer/remove/tpot.yml")
        name = re.search(r"- name: ([^\n]*)\n\s*file:\n\s*path: /etc/ssh/sshd_config.d/tpot.conf\n", remove).group(1)
        self.assertNotIn("(AcceptEnv COLORTERM)", name)
        self.assertIn("LC_TERMINAL", name)


class StopTest(Scripts):
    """Ctrl+C under a gum spinner: gum ends with 130 (it reads the key in raw mode), the step stops and
    the script ends there with 130 and says so, instead of waiting for the step and going on."""

    def setUp(self):
        super().setUp()
        gum_dir = os.path.join(self.home, "data", "tpotce", "bin")
        os.makedirs(gum_dir)
        self.gum = ui.fake_gum(gum_dir)          # fuUI_INIT takes it: gum 2.0.2 at the pinned place
        self.marker = os.path.join(self.home, "marker")

    def slow(self, name, pattern, rest):
        """A command that takes 2 s and then writes the marker when its arguments match the pattern."""
        self.stub(name, f'#!/bin/sh\necho "{name} $*" >> "$HOME/calls"\n'
                        f'case "$*" in {pattern}) sleep 2; touch {shlex.quote(self.marker)}; exit 0 ;; esac\n'
                        f'{rest}\n')

    def assert_went_no_further(self):
        time.sleep(2.5)
        self.assertFalse(os.path.exists(self.marker), "the step went on after Ctrl+C")

    def test_update_sh_stops_at_the_pull(self):
        self.slow("docker", "*pull*", 'case "$*" in images*) echo "ghcr.io/telekom-security/cowrie:24.04.1" ;; esac')
        with open(os.path.join(self.tpotce, ".env"), "w", encoding="utf-8") as out:
            out.write("TPOT_REPO=ghcr.io/telekom-security\nTPOT_VERSION=24.04.2\n")
        call = f'myRUNNING=1; mySTOPPED=1; fuLOG_START; myUI_GUM={shlex.quote(self.gum)}; fuUPDATER'
        start = time.time()
        result = self.source_update(call, FAKE_GUM_SPIN_RC="130")
        self.assertLess(time.time() - start, 1.8)
        self.assertEqual(result.returncode, 130, result.stdout + result.stderr)
        out = result.stdout
        self.assertIn("Stopped: Pulling the images of this release", out)
        self.assertIn("The update was stopped", out)
        self.assertIn("T-Pot is stopped, 'sudo systemctl start tpot' brings it back.", out)
        self.assertNotIn("Could not pull all images", out + result.stderr)
        self.assertNotIn("docker images", self.calls())                    # no cleanup after the stop
        self.assert_went_no_further()

    def test_restore_sh_stops_at_the_extract(self):
        archive = base.make_backup(self.home, {"MANIFEST": base.MANIFEST, "data/uuid": "x\n"})
        self.slow("tar", "*'data/*'*", f'exec {shutil.which("tar")} "$@"')
        line = f'bash {shlex.quote(RESTORE_SH)} -f {shlex.quote(archive)} -g data; echo "rc=$?"'
        out = ui.plain(ui.at_terminal(line, self.env(FAKE_GUM_SPIN_RC="130", FAKE_GUM_STOP="Extracting"),
                                      120, 49, source="/dev/null", timeout=30))
        self.assertIn("rc=130", out)
        self.assertIn("Stopped: Extracting data/ from the archive", out)
        self.assertIn("The restore was stopped", out)
        self.assertNotIn("Restored from", out)
        self.assertIn("systemctl start tpot", out)                  # T-Pot was stopped for the restore
        self.assertNotIn("systemctl start", self.calls())
        self.assert_went_no_further()


if __name__ == "__main__":
    unittest.main()
