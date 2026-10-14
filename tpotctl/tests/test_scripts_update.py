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
import sys
import tarfile
import textwrap
import time
import unittest

from tpotctl.tests import ptyhelper
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
    """update.sh compares the file `version` of ~/tpotce with the one of its own checkout, not a number in it."""

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
        checkout = self.checkout("tpotce", "99.1.0")
        result = self.run_plain(os.path.join(checkout, "update.sh"), "-y", cwd=checkout)
        out = result.stdout + result.stderr
        self.assertIn("99.1.0 is eligible for the update procedure.", out)
        self.assertIn("cannot be reached", out)                       # the curl stub, nothing went out
        self.assertEqual(result.returncode, 1)

    def test_a_checkout_newer_than_the_update_sh_is_refused(self):
        script = self.checkout("script", "25.0.0")
        checkout = self.checkout("tpotce", "99.1.0", with_script=False)
        result = self.run_plain(os.path.join(script, "update.sh"), "-y", cwd=checkout)
        out = result.stdout + result.stderr
        self.assertIn("99.1.0 cannot be upgraded automatically", out)
        self.assertEqual(result.returncode, 1, out[-400:])
        self.assertNotIn("cannot be reached", out)

    def compare(self, local, script_version):
        """update.sh of a checkout at script_version run in a checkout at local."""
        script = self.checkout("script", script_version)
        checkout = self.checkout("tpotce", local, with_script=False)
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
        checkout = self.checkout("tpotce", "24.04.2 \r", with_script=False)
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

    def test_the_warnings_of_a_run_are_in_its_summary(self):
        """A changed edition and a Kibana that does not answer: both warnings are in the summary too."""
        self.stub("docker", "#!/bin/sh\necho kibana\n")                  # the container runs, curl fails
        compose = "# T-Pot: STANDARD\nservices:\n  kibana:\n    image: mine\n"
        for name, text in ((".env", "TPOT_TYPE=HIVE\n"), ("docker-compose.yml", compose)):
            with open(os.path.join(self.tpotce, name), "w", encoding="utf-8") as out:
                out.write(text)
        os.makedirs(os.path.join(self.tpotce, "compose"))
        with open(os.path.join(self.tpotce, "compose", "standard.yml"), "w", encoding="utf-8") as out:
            out.write("# T-Pot: STANDARD\nservices: {}\n")
        result = self.source_update(
            'myRUNNING=1 myELASTIC_GRACE=0 myARCHIVE=/b/x.tar\nfuCHECK_EDITION\n'
            'fuELASTIC_READY kibana kibana "${myKIBANA}/api/status" Kibana\nfuRESTORE_EDITION\nexit 0\n')
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out[-800:])
        tail = result.stdout[result.stdout.index("### T-Pot is updated"):]
        self.assertIn("### [WARNING] - Kibana did not answer within 0s, it is NOT in the backup.", tail)
        self.assertIn("### [WARNING] - Your docker-compose.yml had been modified, the STANDARD edition of this "
                      "release replaces it", tail)

    def test_a_changed_edition_is_in_the_summary(self):
        with open(os.path.join(self.tpotce, ".env"), "w", encoding="utf-8") as out:
            out.write("TPOT_TYPE=HIVE\n")
        os.makedirs(os.path.join(self.tpotce, "compose"))
        with open(os.path.join(self.tpotce, "compose", "standard.yml"), "w", encoding="utf-8") as out:
            out.write("# T-Pot: STANDARD\nservices: {}\n")
        result = self.source_update('myRUNNING=1 myEDITION=STANDARD myCOMPOSE_CUSTOMIZED=1 myARCHIVE=/b/x.tar\n'
                                    'fuRESTORE_EDITION\nexit 0\n')
        tail = result.stdout[result.stdout.index("### T-Pot is updated"):]
        self.assertIn("### [WARNING] - Your docker-compose.yml had been modified, the STANDARD edition of this "
                      "release replaces it", tail)

    def test_a_restarted_update_keeps_the_items_of_the_first_pass(self):
        first = "ok:The backup is /b/x.tar.\nwarn:Kibana did not answer within 120s, it is NOT in the backup.\n"
        result = self.source_update('myRUNNING=1 myARCHIVE=/b/x.tar\nfuBACKUP\nfuDID ok "Pulled."\nexit 0\n',
                                    TPOT_UPDATE_PREPARED="1", TPOT_UPDATE_DONE=first)
        tail = result.stdout[result.stdout.index("### T-Pot is updated"):]
        self.assertEqual(tail.count("The backup is /b/x.tar."), 1, tail)
        self.assertIn("### [WARNING] - Kibana did not answer within 120s, it is NOT in the backup.", tail)
        self.assertIn("### [OK] - Pulled.", tail)
        # the restart hands them over
        text = base.read("update.sh")
        restart = text[text.index('export TPOT_UPDATE_COMPOSE_CUSTOMIZED'):text.index('exec bash "$0" -y')]
        self.assertIn("fuDONE_EXPORT", restart)

    def test_the_items_survive_the_handover_whole(self):
        """fuDID, fuDONE_EXPORT and the read of a restarted update.sh: every item comes back as it was, a
        newline in a text is a space (an item never splits in two); also with the bash 3.2 of macOS."""
        items = [("ok", "The backup is /b/x.tar."), ("warn", "quotes ' \" $HOME `x` \\ and  two  spaces"),
                 ("warn", "two\nlines"), ("info", "  leading blanks"), ("fail", "a: colon")]
        calls = "".join(f"fuDID {shlex.quote(kind)} {shlex.quote(text)}\n" for kind, text in items)
        shells = [shell for shell in (shutil.which("bash"), "/bin/bash") if shell and os.path.exists(shell)]
        for shell in dict.fromkeys(shells):
            with self.subTest(shell=shell):
                script = (f"source {shlex.quote(UPDATE_SH)}\n{calls}fuDONE_EXPORT\n"
                          f"TPOT_UPDATE_PREPARED=1 {shell} -c " + shlex.quote(
                              f'source {shlex.quote(UPDATE_SH)}; printf "<%s>\\n" "${{myDONE[@]}}"') + "\n")
                result = subprocess.run([shell, "-c", script], capture_output=True, universal_newlines=True,
                                        env=self.env(TPOT_GUM="off"), cwd=self.tpotce, timeout=60)
                self.assertEqual(result.stdout.splitlines(),
                                 [f"<{kind}:{text.replace(chr(10), ' ')}>" for kind, text in items],
                                 result.stdout + result.stderr)
        # nothing to hand over: nothing comes back
        script = (f"source {shlex.quote(UPDATE_SH)}\nfuDONE_EXPORT\nTPOT_UPDATE_PREPARED=1 bash -c "
                  + shlex.quote(f'source {shlex.quote(UPDATE_SH)}; echo "n=${{#myDONE[@]}}"') + "\n")
        result = subprocess.run(["bash", "-c", script], capture_output=True, universal_newlines=True,
                                env=self.env(TPOT_GUM="off"), cwd=self.tpotce, timeout=60)
        self.assertIn("n=0", result.stdout, result.stderr)

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
        # the groups are one list; the only yes / no left is the one before a loss of data (default no)
        calls = [line.strip() for line in rest.splitlines()
                 if "fuUI_CONFIRM" in line and not line.lstrip().startswith("#")]
        self.assertEqual(calls, ['fuUI_CONFIRM --default no "${3:-Accept this loss of data?}"'])
        loss = rest[rest.index("function fuLOSS_CONFIRM ()"):]
        self.assertIn("fuUI_CONFIRM --default no", loss[:loss.index("\n}\n")])
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
                            '  *buildx*) echo "sha256:aaa" ;;\n'               # the registry has the same digest
                            '  "image inspect"*) echo "ghcr.io/telekom-security/cowrie@sha256:aaa" ;;\n'
                            '  images*) echo "ghcr.io/telekom-security/cowrie:24.04.1" ;;\n'
                            '  rmi*) echo "Untagged: $2" ;;\nesac\n')
        self.stub("timeout", '#!/bin/sh\nshift\nexec "$@"\n')                  # macOS has none

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
        # the Elastic export asks Kibana and waits for it, the Elasticsearch upgrade asks: both in the open
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


REAL_TAR = shutil.which("tar") or "/usr/bin/tar"
# tar rf as GNU tar (the --transform of fuBACKUP: ./elk/data becomes data/elk/data) through Python's
# tarfile, everything else the real tar: GNU or bsdtar, both list and extract. HOME/tar_fail makes the
# append fail half way, as a full disk does: the header of a member and part of its data over the end
# marker, then exit 2 (HOME/cut.tar keeps that state); HOME/tar_fail_grow writes 2 MiB of it, so the
# archive grows past its old end before tar fails. HOME/tar_slow waits (for a Ctrl+C). Every call goes
# into HOME/calls.
TAR = f'''#!{sys.executable}
import os, sys, shutil, tarfile
home = os.environ["HOME"]
args = sys.argv[1:]
with open(os.path.join(home, "calls"), "a") as log:
    log.write("tar " + " ".join(args) + "\\n")
if args and args[0] == "rf":
    archive, folder, member = args[1], args[args.index("-C") + 1], args[-1]
    if args[args.index("--transform") + 1] != "flags=rh;s,^\\\\./,data/,":
        sys.exit("tar: unexpected --transform")
    if os.path.exists(os.path.join(home, "tar_slow")):
        open(os.path.join(home, "tar_started"), "w").close()
        import time
        time.sleep(30)
    for mode, written in (("tar_fail", 3000), ("tar_fail_grow", 2 * 1024 * 1024)):
        if os.path.exists(os.path.join(home, mode)):
            with tarfile.open(archive) as old:
                old.getmembers()
                end = old.offset
            with open(archive, "r+b") as out:
                out.seek(end)
                info = tarfile.TarInfo("data/elk/data/big")
                info.size = 4 * 1024 * 1024
                out.write(info.tobuf())
                out.write(b"x" * written)
                out.truncate()
            shutil.copy(archive, os.path.join(home, "cut.tar"))
            sys.exit("tar: Error writing to archive: No space left on device")
    source = os.path.join(folder, member)
    if not os.path.exists(source):
        sys.exit("tar: " + member + ": Cannot stat: No such file or directory")
    with tarfile.open(archive, "a") as out:
        out.add(source, arcname="data/" + member[2:])
    sys.exit(0)
os.execv({REAL_TAR!r}, [{REAL_TAR!r}] + args)
'''
# systemctl: is-enabled says what HOME/enabled holds (disabled without it), every call goes into HOME/calls
SYSTEMCTL = ('#!/bin/sh\necho "systemctl $*" >> "$HOME/calls"\n'
             'case "$1" in is-enabled) if [ -f "$HOME/enabled" ]; then echo enabled; else echo disabled; exit 1; fi ;; esac\n'
             'exit 0\n')
# sudo and du of the Harness, their calls (the whole argv) into HOME/calls as well
LOGGED = '#!/bin/sh\necho "{name} $*" >> "$HOME/calls"\n'


class EsHarness(base.Harness):
    """fuCHECK_ELASTIC with stubs for df, du, sudo, tar and systemctl, a checkout of its own and the
    archive an update.sh of the release before hands over. No tests here, they are in the classes below."""

    HELPERS = base.DataLossTest.HELPERS + 'fuDID () { myDONE+=("$1:$2"); echo "DID: $1: $2"; }\n'
    functions = staticmethod(base.DataLossTest.functions)
    # the check of --check-data-loss as DataLossTest runs it
    CHECK = base.DataLossTest.CHECK
    CHECK_FUNCTIONS = base.DataLossTest.CHECK_FUNCTIONS
    target = base.DataLossTest.target
    check = base.DataLossTest.check
    write = base.DataLossTest.write
    environment = base.DataLossTest.environment
    bash = base.DataLossTest.bash

    def setUp(self):
        super().setUp()
        for name, text in (("df", base.DF), ("du", base.DU.replace("#!/bin/sh\n", LOGGED.format(name="du"), 1)),
                           ("sudo", base.SUDO.replace("#!/bin/sh\n", LOGGED.format(name="sudo"), 1)),
                           ("tar", TAR), ("systemctl", SYSTEMCTL)):
            path = os.path.join(self.bin, name)
            with open(path, "w", encoding="utf-8") as out:
                out.write(text)
            os.chmod(path, 0o755)
        self.tpotce = os.path.join(self.home, "tpotce")
        self.backups = os.path.join(self.home, "tpot_backups")
        os.makedirs(os.path.join(self.tpotce, "docker", "elk", "elasticsearch"))
        with open(os.path.join(self.tpotce, "docker", "elk", "elasticsearch", "Dockerfile"), "w") as out:
            out.write("FROM alpine\nARG ES_VER=9.5.4\n")
        with open(os.path.join(self.tpotce, "docker-compose.yml"), "w") as out:
            out.write("services:\n  elasticsearch:\n    image: x\n")
        restore = os.path.join(self.tpotce, "restore.sh")
        with open(restore, "w", encoding="utf-8") as out:
            out.write('#!/bin/sh\necho "restore.sh $*" >> "$HOME/calls"\n')
        os.chmod(restore, 0o755)
        subprocess.run(["git", "init", "-q", self.tpotce], check=True)
        subprocess.run(["git", "-C", self.tpotce, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
                        "--allow-empty", "-m", "release"], check=True)
        # 1 GB disk, 900 MB free, 10 % reserve: room for 1 MB of Elasticsearch data, not for 900 MB
        self.write("df_size", "1000000000\n")
        self.write("df_avail", "900000000\n")
        self.write("es_need", "1000000\n")

    def data(self, folder):
        """A data folder with Elasticsearch data that runs."""
        os.makedirs(os.path.join(folder, "elk", "data", "nodes", "0"))
        with open(os.path.join(folder, "elk", "data", "nodes", "0", "live"), "w") as out:
            out.write("live\n")
        return folder

    def master_archive(self, extra=None, name="20261005101010_tpot_backup.tar"):
        """The archive the update.sh of master writes before its pull and hands over: MANIFEST without
        a data folder, rollback.txt of the commit before the pull, no data/elk/data without --full."""
        members = {"MANIFEST": "T-Pot backup\nTPOT_VERSION: 24.04.1\n", "rollback.txt": "0123456789abcdef\n",
                   "env": "TPOT_VERSION=24.04.1\n", "data/uuid": "x\n"}
        members.update(extra or {})
        return base.make_backup(self.home, members, name=name)

    def tag_tgz(self):
        """What the update.sh of the tag 24.04.1 wrote before it restarted into this one without a
        handover: tar cvf of ~/tpotce, data included, to HOME/*.tgz, with the .env of 24.04.1."""
        path = os.path.join(self.home, "20261014090000_tpot_backup.tgz")
        source = os.path.join(self.home, "src")
        os.makedirs(os.path.join(source, "data", "elk", "data", "nodes", "0"))
        with open(os.path.join(source, "data", "elk", "data", "nodes", "0", "x"), "w") as out:
            out.write("x\n")
        with open(os.path.join(source, ".env"), "w") as out:
            out.write("TPOT_VERSION=24.04.1\n")
        with tarfile.open(path, "w") as archive:
            archive.add(os.path.join(source, "data"), arcname="data")
            archive.add(os.path.join(source, ".env"), arcname=".env")
        return path

    # the Elasticsearch version of the images that ran so far
    DOCKER = """docker () { [ "$1 $2" = "image inspect" ] && printf 'PATH=/bin\\nES_VER=9.3.5\\n'; }\n"""

    def run_check(self, archive, prepared="1", env="", signal_when=None, send=None, extra="", late=""):
        """fuCHECK_ELASTIC after the handover (prepared) or in the first pass; with signal_when the signal
        send goes to the whole run once that file exists. extra: shell code before the functions, late after."""
        with open(os.path.join(self.tpotce, ".env"), "w") as out:
            out.write("TPOT_REPO=ghcr.io/telekom-security\nTPOT_VERSION=24.04.2\n" + env)
        script = textwrap.dedent(f"""
            myOLDVERSION=24.04.1 myARCHIVE="{archive}" myPREPARED="{prepared}" myDATE=20261014120000
            myBACKUP_RETAIN=10 myBACKUP_RETAIN_FULL=2
            export TPOT_UPDATE_ARCHIVE="{archive}"
            myDONE=("ok:The backup is {archive}.")
            trap '[ -z "${{myTMPDIR}}" ] || rm -rf "${{myTMPDIR}}"; printf "ITEM %s\\n" "${{myDONE[@]}}"; echo "HANDOVER $TPOT_UPDATE_ARCHIVE"' EXIT
        """) + self.DOCKER + extra + self.functions("fuLOSS_IN_FILE", "fuLOSS_CONFIRM", "fuENV_VALUE", "fuDATA_PATH", "fuCOMPOSE_HAS",
                              "fuES_IMAGE_VERSION", "fuOLD_ES_VERSION", "fuCAN_ROLLBACK", "fuROLLBACK_CHECKOUT",
                              "fuSTART_TPOT", "fuELASTIC_REFUSED", "fuOLD_RELEASE_BACKUP", "fuTMPDIR",
                              "fuOLD_UPDATER_ARCHIVE", "fuOLD_UPDATER_FRESH", "fuOLD_UPDATER_READ",
                              "fuOLD_UPDATER_FILE", "fuARCHIVE_MEMBER", "fuARCHIVE_MEMBER_NAME", "fuARCHIVE_ENV", *base.ES_APPEND,
                              "fuCHECK_ELASTIC") + late + 'fuCHECK_ELASTIC\necho "went on"\n'
        proc = subprocess.Popen(["bash", "-c", self.HELPERS + script], cwd=REPO, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, universal_newlines=True, env=self.environment(),
                                stdin=subprocess.DEVNULL, start_new_session=True)
        if signal_when:
            deadline = time.time() + 30
            while not os.path.exists(signal_when) and proc.poll() is None and time.time() < deadline:
                time.sleep(0.01)
            self.assertTrue(os.path.exists(signal_when), "the step did not start")
            time.sleep(0.2)
            os.killpg(proc.pid, send)
        out, _err = proc.communicate(timeout=60)
        return proc.returncode, out

    def members(self, archive):
        with tarfile.open(archive) as handle:
            return handle.getnames()

    def calls(self):
        path = os.path.join(self.home, "calls")
        if not os.path.exists(path):
            return ""
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def forget_calls(self):
        if os.path.exists(os.path.join(self.home, "calls")):
            os.remove(os.path.join(self.home, "calls"))

    def read_bytes(self, path):
        with open(path, "rb") as handle:
            return handle.read()

    def assert_ways_on(self, out):
        """No refusal sends anyone to --full, and no text names the release of the updater."""
        flat = " ".join(out.split())
        self.assertNotIn("--full", flat)
        self.assertNotIn("24.04.1", flat.replace("TPOT_VERSION: 24.04.1", ""))
        return flat


class EsDataAppendTest(EsHarness):
    """fuCHECK_ELASTIC puts the Elasticsearch data into the archive of the run by itself where there is
    room, also into the one an update.sh of the release before (branch master) hands over: no question,
    the archive gets the name of a full one. The fresh .tgz of the tag 24.04.1 (no handover) needs no
    copy. Without room, or with a data/elk/data of another folder in the archive, the old way: the
    question, or without a terminal the refusal, the rollback and T-Pot started again where
    tpot.service is enabled, with the ways on that fit the reason and never --full."""

    def test_handover_without_es_data_appends_and_renames_without_asking(self):
        self.data(os.path.join(self.tpotce, "data"))
        archive = self.master_archive()
        code, out = self.run_check(archive)
        self.assertEqual(code, 0, out)
        self.assertIn("went on", out)
        self.assertNotIn("ASKED", out)
        self.assertNotIn("Not confirmed", out)
        self.assertFalse(os.path.exists(archive))
        self.assertEqual(os.listdir(self.backups), ["20261014120000_tpot_backup_full.tar"])   # fuARCHIVE_NAME
        new = os.path.join(self.backups, "20261014120000_tpot_backup_full.tar")
        names = self.members(new)
        self.assertIn("data/elk/data/nodes/0/live", names)
        self.assertIn("rollback.txt", names)                                  # what master wrote stays
        self.assertIn(f"DID: ok: The Elasticsearch data is now in {new}.", out)
        self.assertIn(f"ITEM ok:The Elasticsearch data is now in {new}.", out)
        self.assertIn(f"ITEM ok:The backup is {new}.", out)                   # the summary names the new one
        self.assertNotIn(f"ITEM ok:The backup is {archive}.", out)
        self.assertIn(f"HANDOVER {new}", out)
        calls = self.calls()
        self.assertNotIn("restore.sh", calls)
        # the size and the copy as root: data/elk belongs to the uid of the containers
        self.assertIn(f"sudo du -sb {self.tpotce}/data/elk/data", calls)
        self.assertIn(f"sudo tar rf {archive} -p --numeric-owner --transform", calls)

    def test_the_full_backups_rotate_after_the_rename(self):
        self.data(os.path.join(self.tpotce, "data"))
        for number in (1, 2):                                                 # two full ones before
            old = self.master_archive(name=f"2026100{number}101010_tpot_backup_full.tar")
            os.utime(old, (1_700_000_000 + number * 100, 1_700_000_000 + number * 100))
        archive = self.master_archive()
        os.utime(archive, (1_700_000_500, 1_700_000_500))
        code, out = self.run_check(archive)
        self.assertEqual(code, 0, out)
        self.assertEqual(sorted(os.listdir(self.backups)),
                         ["20261002101010_tpot_backup_full.tar", "20261014120000_tpot_backup_full.tar"])

    def test_a_fresh_backup_of_the_tag_needs_no_copy(self):
        """The update.sh of the tag 24.04.1 restarted into this one without a handover: its .tgz holds the
        data, the run copies nothing. After a handover such a .tgz is from an earlier run: the copy."""
        self.data(os.path.join(self.tpotce, "data"))
        tgz = self.tag_tgz()
        archive = self.master_archive()
        code, out = self.run_check(archive, prepared="")
        self.assertEqual(code, 0, out)
        self.assertIn(f"OK: The Elasticsearch data is in {tgz}, the backup of the update.sh of the release before.",
                      out)
        self.assertNotIn("tar rf", self.calls())
        self.assertNotIn("du -sb", self.calls())
        self.assertTrue(os.path.exists(archive))
        self.forget_calls()
        code, out = self.run_check(archive, prepared="1")
        self.assertEqual(code, 0, out)
        self.assertNotIn(tgz, out)
        self.assertIn("sudo tar rf", self.calls())

    def test_no_room_refuses_starts_tpot_and_names_the_ways_on(self):
        self.data(os.path.join(self.tpotce, "data"))
        self.write("es_need", "900000000\n")
        self.write("enabled", "")
        archive = self.master_archive()
        before = self.read_bytes(archive)
        code, out = self.run_check(archive)
        self.assertEqual(code, 5, out)
        self.assertNotIn("went on", out)
        self.assertNotIn("ASKED", out)
        calls = self.calls()
        self.assertIn(f"restore.sh -f {archive} -c", calls)
        self.assertIn("systemctl start tpot.service", calls)
        self.assertLess(calls.index("restore.sh"), calls.index("systemctl start"))
        self.assertNotIn("tar rf", calls)
        self.assertIn("DID: ok: T-Pot runs again, on the release from before this update.", out)
        flat = self.assert_ways_on(out)
        self.assertIn("DID: info: The Elasticsearch data did not go into the backup: no room for it in the backup:",
                      flat)
        self.assertIn(f"DID: next: Make room in {self.backups} (it needs about 944 MB), then run the update "
                      "again: the Elasticsearch data goes into the backup by itself.", flat)
        self.assertIn("DID: next: To upgrade without a copy: ./update.sh -y -- --accept-data-loss (an older "
                      "update.sh rejects --accept-data-loss alone, after -- every one takes it).", flat)
        self.assertIn("Each run of the update.sh that started this run writes a backup", flat)
        self.assertNotIn("DID: next: Copy", flat)                              # T-Pot runs again
        self.assertEqual(self.read_bytes(archive), before)                     # untouched, same name

    def test_no_room_in_the_first_pass_names_accept_without_dashes(self):
        self.data(os.path.join(self.tpotce, "data"))
        self.write("es_need", "900000000\n")
        code, out = self.run_check(self.master_archive(), prepared="")
        self.assertEqual(code, 5, out)
        flat = self.assert_ways_on(out)
        self.assertIn("DID: next: To upgrade without a copy: ./update.sh -y --accept-data-loss.", flat)
        self.assertIn("DID: next: Copy ", flat)                                # T-Pot stays stopped
        self.assertNotIn("update.sh that started this run", flat)

    def test_the_filesystem_of_the_data_stays_below_85_percent(self):
        """Room beyond the reserve, but the copy would fill the filesystem of the data to 86 %."""
        self.data(os.path.join(self.tpotce, "data"))
        self.write("df_avail", "250000000\n")
        self.write("es_need", "100000000\n")
        code, out = self.run_check(self.master_archive())
        self.assertEqual(code, 5, out)
        self.assertNotIn("tar rf", self.calls())
        self.assertIn("would be 86% full, Elasticsearch needs the filesystem of", out)

    def test_outside_data_path_stale_member_does_not_count(self):
        """master archives ~/tpotce/data: with the data elsewhere a data/elk/data in its archive is stale.
        It is no backup, and no copy goes next to it (they would mix on a restore)."""
        self.data(os.path.join(self.tpotce, "data"))
        archive = self.master_archive({"data/elk/data/nodes/0/stale": "old\n"})
        before = self.read_bytes(archive)
        for folder in ("/srv/tpot", self.data(os.path.join(self.home, "srv", "tpot"))):
            with self.subTest(folder=folder):
                self.forget_calls()
                code, out = self.run_check(archive, env=f"TPOT_DATA_PATH={folder}\n")
                self.assertEqual(code, 5, out)
                self.assertNotIn(f"OK: The Elasticsearch data is in {archive}", out)
                self.assertNotIn("tar rf", self.calls())
                self.assertIn(f"{archive} holds a data/elk/data already that is not the one of {folder}", out)
                flat = self.assert_ways_on(out)
                self.assertIn(f"DID: next: Copy {folder}/elk/data first (sudo cp -a {folder}/elk/data", flat)
                self.assertNotIn("Make room", flat)
                self.assertEqual(self.read_bytes(archive), before)

    def test_outside_data_path_appends_the_data_folder_in_use(self):
        folder = self.data(os.path.join(self.home, "srv", "tpot"))
        archive = self.master_archive()
        code, out = self.run_check(archive, env=f"TPOT_DATA_PATH={folder}\n")
        self.assertEqual(code, 0, out)
        self.assertIn(f" -C {folder} ./elk/data", self.calls())
        new = os.path.join(self.backups, "20261014120000_tpot_backup_full.tar")
        self.assertIn("data/elk/data/nodes/0/live", self.members(new))

    def test_the_data_of_the_data_folder_that_runs_counts_by_the_manifest(self):
        folder = self.data(os.path.join(self.home, "srv", "tpot"))
        archive = self.master_archive({"MANIFEST": f"T-Pot backup\nData folder:  {folder}\n",
                                       "data/elk/data/nodes/0/live": "live\n"})
        code, out = self.run_check(archive, env=f"TPOT_DATA_PATH={folder}\n")
        self.assertEqual(code, 0, out)
        self.assertIn(f"OK: The Elasticsearch data is in {archive}", out)
        self.assertNotIn("tar rf", self.calls())

    def test_failed_append_leaves_the_archive_readable_and_refuses(self):
        self.data(os.path.join(self.tpotce, "data"))
        self.write("tar_fail", "")
        archive = self.master_archive()
        before = self.read_bytes(archive)
        code, out = self.run_check(archive)
        self.assertEqual(code, 5, out)
        self.assertIn("tar rf", self.calls())
        # cut short like that, tar lists what was there before and then fails: the end is put back
        cut = subprocess.run([REAL_TAR, "tf", os.path.join(self.home, "cut.tar")], capture_output=True,
                             universal_newlines=True)
        self.assertIn("rollback.txt", cut.stdout)
        self.assertNotEqual(cut.returncode, 0, cut.stdout)
        self.assertEqual(self.read_bytes(archive), before)                     # as it was, byte for byte
        listed = subprocess.run([REAL_TAR, "tf", archive], capture_output=True, universal_newlines=True)
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertEqual(os.listdir(self.backups), [os.path.basename(archive)])
        self.assertIn("The archive is as it was before.", out)
        self.assertIn(f"restore.sh -f {archive} -c", self.calls())
        flat = self.assert_ways_on(out)
        self.assertIn("DID: info: The Elasticsearch data did not go into the backup: tar could not add it", flat)
        self.assertIn("DID: next: Or see why tar could not add the data", flat)

    def test_a_large_archive_that_grew_is_put_back(self):
        """An archive of more than the MiB kept aside, and a failed tar that wrote past its old end: the put
        back truncates and writes the end at its place (dd with seek > 0)."""
        self.data(os.path.join(self.tpotce, "data"))
        self.write("tar_fail_grow", "")
        archive = self.master_archive({"untracked/blob": "y" * (3 * 1024 * 1024 // 2)})
        before = self.read_bytes(archive)
        self.assertGreater(len(before), 1024 * 1024)
        code, out = self.run_check(archive)
        self.assertEqual(code, 5, out)
        self.assertGreater(os.path.getsize(os.path.join(self.home, "cut.tar")), len(before))
        self.assertEqual(self.read_bytes(archive), before)
        self.assertIn("The archive is as it was before.", out)

    def test_ctrl_c_while_the_data_goes_in_is_a_no(self):
        """Ctrl+C ends tar: the archive is put back and the run refuses as for a no at the question."""
        import signal
        self.data(os.path.join(self.tpotce, "data"))
        self.write("tar_slow", "")
        archive = self.master_archive()
        before = self.read_bytes(archive)
        code, out = self.run_check(archive, signal_when=os.path.join(self.home, "tar_started"),
                                   send=signal.SIGINT)
        self.assertEqual(code, 5, out)
        self.assertIn("it was stopped while it went into", out)
        self.assertNotIn("ASKED", out)
        self.assertIn(f"restore.sh -f {archive} -c", self.calls())
        self.assertEqual(self.read_bytes(archive), before)

    def test_a_full_data_partition_refuses_and_starts_tpot_again(self):
        self.data(os.path.join(self.tpotce, "data"))
        self.write("df_pcent", " 92%\n")
        self.write("enabled", "")
        archive = self.master_archive()
        code, out = self.run_check(archive)
        self.assertEqual(code, 1, out)
        calls = self.calls()
        self.assertIn(f"restore.sh -f {archive} -c", calls)
        self.assertIn("systemctl start tpot.service", calls)
        self.assertLess(calls.index("restore.sh"), calls.index("systemctl start"))
        self.assertNotIn("tar rf", calls)
        self.assertIn("is 92% full, Elasticsearch needs it below 90%", out)
        self.assertIn("DID: ok: T-Pot runs again, on the release from before this update.", out)

    def test_check_data_loss_reports_elastic_only_without_room(self):
        for need, expected in (("1000000", []), ("900000000", ["elastic"])):
            with self.subTest(need=need):
                shutil.rmtree(os.path.join(self.home, "origin"), ignore_errors=True)
                self.target()
                self.write("es_need", need + "\n")
                out, lines = self.check()
                found = [line.split()[2] for line in lines if line.split()[2] == "elastic"]
                self.assertEqual(found, expected, out)
        self.assertIn("The backup of the run cannot take the Elasticsearch data:", out)

    def test_check_data_loss_counts_the_regular_backup_first(self):
        """Room for the Elasticsearch data alone, not after the regular backup the run writes before it."""
        self.target()
        self.write("es_need", "650000000\n")                 # 715 MB with the margin, 900 MB free, 100 reserve
        self.write("need", "100000000\n")                    # the regular backup takes 104 MB first
        out, lines = self.check()
        self.assertEqual([line.split()[2] for line in lines if line.split()[2] == "elastic"], ["elastic"], out)
        self.write("need", "1000\n")
        out, lines = self.check()
        self.assertEqual([line.split()[2] for line in lines if line.split()[2] == "elastic"], [], out)

    def test_not_enabled_service_is_not_started_after_refusal(self):
        self.data(os.path.join(self.tpotce, "data"))
        self.write("es_need", "900000000\n")
        archive = self.master_archive()
        code, out = self.run_check(archive)
        self.assertEqual(code, 5, out)
        calls = self.calls()
        self.assertIn(f"restore.sh -f {archive} -c", calls)
        self.assertIn("systemctl is-enabled tpot", calls)
        self.assertNotIn("systemctl start", calls)
        self.assertNotIn("T-Pot runs again", out)
        self.assertIn("DID: next: Copy ", out)                                 # T-Pot stays stopped


# curl of the step: _cluster/health answers with the status of HOME/es_health (green without it, no
# answer for "none"), /api/status with the code of HOME/kb_code (200 without it); HOME/health_asked once
# the health was asked for. Every call goes into HOME/calls
STEP_CURL = ('#!/bin/sh\necho "curl $*" >> "$HOME/calls"\nfor a; do u="$a"; done\ncase "$u" in\n'
             '  *_cluster/health*) : > "$HOME/health_asked"; s=$(cat "$HOME/es_health" 2>/dev/null || echo green)\n'
             '    [ "$s" = none ] && exit 7\n'
             '    printf \'{"cluster_name":"tpotcluster","status":"%s","timed_out":false}\' "$s" ;;\n'
             '  *api/status*) printf "%s" "$(cat "$HOME/kb_code" 2>/dev/null || echo 200)" ;;\nesac\n')
# uname -m says what HOME/arch holds (x86_64 without it), -s Linux
STEP_UNAME = ('#!/bin/sh\ncase "$*" in\n  -m) cat "$HOME/arch" 2>/dev/null || echo x86_64; exit 0 ;;\n'
              '  -s|"") echo Linux; exit 0 ;;\nesac\n' f'exec {base.REAL_UNAME} "$@"\n')
# chown logs and does nothing (the step hands the data to 1000:0 and back to 2000:2000)
STEP_CHOWN = ('#!/bin/sh\necho "chown $*" >> "$HOME/calls"\n[ ! -f "$HOME/chown_fail" ] || exit 1\n'
              'case "$*" in *2000:2000*) [ ! -f "$HOME/chown_back_fail" ] ;; esac\n')
# df of the Docker root (docker info says $HOME/docker-root): HOME/docker_avail, 9 GB without it; the
# rest as base.DF
STEP_DF = ('#!/bin/sh\nfor a; do l="$a"; done\ncase "$l" in\n  "$HOME/docker-root")\n'
           '    if [ -f "$HOME/df_slow" ]; then : > "$HOME/df_started"; sleep 30; fi\n'
           '    echo Avail; cat "$HOME/docker_avail" 2>/dev/null || echo 9000000000; exit 0 ;;\nesac\n'
           + base.DF[len("#!/bin/sh\n"):])


class EsStepTest(EsHarness):
    """The data of an Elasticsearch before 8.19 cannot be read by 9: fuCHECK_ELASTIC takes it through the
    official images of 8.19 first (fuES_STEP_819), after a yes (elastic-step, never -y) and with the data in
    the backup of this run. A no, no room for the data or the images, or a step that does not go through is a
    refusal; once Elasticsearch 8.19 ran on the data, T-Pot stays stopped and the way back is the restore of
    that backup. Everything of the step goes on every way out, and the data belongs to 2000:2000 again."""

    # the constants of update.sh, a short wait; docker records its calls (run with every word in [])
    DOCKER = textwrap.dedent("""
        eval "$(sed -nE '/^my(ES|KB)_STEP_[A-Z_]*=/p' update.sh)"
        myES_STEP_WAIT=2 myES_STEP_PAUSE=0.1
        timeout () { [ "$1" != "--foreground" ] || shift; shift; "$@"; }
        docker () {
          local myA=""
          echo "docker $*" >> "$HOME/calls"
          case "$1" in
            image)
              [ "$2" = inspect ] || return 0
              # the image of the release before (--format), or one of the step that is there already
              [ "$3" = --format ] || { [ -f "$HOME/have_${3##*/}" ]; return; }
              printf 'PATH=/bin\\nES_VER=%s\\n' "$(cat "$HOME/es_old")" ;;
            info) echo "$HOME/docker-root" ;;
            pull) [ ! -f "$HOME/pull_fail" ] || return 1 ;;
            run)
              { printf 'RUN'; printf ' [%s]' "$@"; echo; } >> "$HOME/calls"
              [ ! -f "$HOME/run_fail" ] || return 125
              echo 0123abcd ;;
            inspect)
              for myA; do :; done
              if [ -f "$HOME/dead_${myA}" ]; then echo false; else echo true; fi ;;
          esac
          return 0
        }
    """)
    ACCEPT = "myACCEPT_LOSS=1\n"

    def setUp(self):
        super().setUp()
        for name, text in (("curl", STEP_CURL), ("uname", STEP_UNAME), ("chown", STEP_CHOWN), ("df", STEP_DF)):
            path = os.path.join(self.bin, name)
            with open(path, "w", encoding="utf-8") as out:
                out.write(text)
            os.chmod(path, 0o755)
        self.write("es_old", "8.18.2\n")
        self.edition(kibana=True)
        self.version = re.search(r'^myES_STEP_VERSION="([0-9.]+)"$', base.read("update.sh"), re.M).group(1)
        self.es_image = f"docker.elastic.co/elasticsearch/elasticsearch:{self.version}"
        self.kb_image = f"docker.elastic.co/kibana/kibana:{self.version}"
        self.full = os.path.join(self.backups, "20261014120000_tpot_backup_full.tar")

    def edition(self, kibana):
        """The compose file: Elasticsearch, with Kibana (STANDARD) or without it (MOBILE)."""
        with open(os.path.join(self.tpotce, "docker-compose.yml"), "w") as out:
            out.write("services:\n  elasticsearch:\n    image: x\n" + ("  kibana:\n    image: y\n" if kibana else ""))

    def runs(self):
        """The docker run of the step, each a list of its words."""
        return [re.findall(r"\[([^\]]*)\]", line) for line in self.calls().splitlines() if line.startswith("RUN ")]

    def order(self, *texts):
        """Each text is in the calls, in this order."""
        calls = self.calls()
        at = -1
        for text in texts:
            found = calls.find(text, at + 1)
            self.assertGreater(found, at, f"{text!r} not after the one before in:\n{calls}")
            at = found

    def assert_cleaned_up(self, data, chowned=True, kibana=True):
        calls = self.calls()
        lines = calls.splitlines()
        self.assertEqual("docker stop -t 60 tpot_kb_step" in lines, kibana)
        self.assertEqual("docker rm -f tpot_kb_step" in lines, kibana)
        self.assertIn("docker stop -t 120 tpot_es_step", lines)
        self.assertIn("docker rm -f tpot_es_step", lines)
        self.assertIn("docker network rm tpot_es_step", calls)
        self.assertIn(f"docker rmi {self.es_image} {self.kb_image}" if kibana else f"docker rmi {self.es_image}",
                      lines)
        if chowned:
            self.order("sudo chown -R 1000:0 " + data + "/elk/data", "docker rmi",
                       "sudo chown -R 2000:2000 " + data + "/elk/data")
        else:
            self.assertNotIn("chown", calls)

    def assert_manual(self, out, data):
        flat = " ".join(out.split())
        self.assertIn("To take the step by hand instead, with T-Pot stopped (sudo systemctl stop tpot):", flat)
        self.assertIn(f"sudo cp -a {data}/elk/data {self.backups}/elk_data_8.18.2", flat)
        self.assertIn("docker network create tpot_es_step", flat)
        self.assertIn(f"sudo chown -R 1000:0 {data}/elk/data", flat)
        self.assertIn("docker run -d --name tpot_es_step --network tpot_es_step -p 127.0.0.1:64398:9200 "
                      f"-v {data}/elk/data:/usr/share/elasticsearch/data -e discovery.type=single-node "
                      "-e xpack.security.enabled=false -e cluster.routing.allocation.disk.threshold_enabled=false",
                      flat)
        self.assertIn(f'-e "ES_JAVA_OPTS=-Xms1g -Xmx1g" {self.es_image}', flat)
        self.assertIn("docker run -d --name tpot_kb_step --network tpot_es_step -p 127.0.0.1:64396:5601 "
                      f"-e ELASTICSEARCH_HOSTS=http://tpot_es_step:9200 -e TELEMETRY_OPTIN=false {self.kb_image}",
                      flat)
        self.assertIn(f"sudo chown -R 2000:2000 {data}/elk/data", flat)
        self.assertIn("then run the update again", flat)
        return flat

    def test_old_818_without_confirmation_refuses_before_any_step(self):
        data = self.data(os.path.join(self.tpotce, "data"))
        self.write("enabled", "")
        archive = self.master_archive()
        before = self.read_bytes(archive)
        code, out = self.run_check(archive)
        self.assertEqual(code, 5, out)
        self.assertNotIn("went on", out)
        flat = self.assert_ways_on(out)
        self.assertIn(f"WARN: The data of Elasticsearch 8.18.2 cannot be read by 9.5.4: the update first runs "
                      f"Elasticsearch and Kibana {self.version} once on it (in {data}/elk), then the new release. "
                      "Both steps change the data for good, only the backup of this run brings it back.", flat)
        self.assertIn("Not confirmed: no terminal.", flat)
        self.assert_manual(out, data)
        calls = self.calls()
        for step in ("RUN", "docker pull", "docker network create", "chown", "tar rf", "docker info"):
            self.assertNotIn(step, calls)
        self.assertEqual(self.read_bytes(archive), before)                    # no backup before the yes
        self.order(f"restore.sh -f {archive} -c", "systemctl start tpot.service")
        self.assertIn("DID: fail: Not updated: the step through Elasticsearch 8.19 was not confirmed, the checkout "
                      "and the configuration are back.", flat)
        self.assertIn("DID: next: To take the step without a terminal: ./update.sh -y -- --accept-data-loss", flat)

    def test_a_no_at_the_terminal_refuses(self):
        """At a terminal it asks, default no (fuLOSS_CONFIRM as at a terminal, the stub of fuUI_CONFIRM says
        no)."""
        data = self.data(os.path.join(self.tpotce, "data"))
        archive = self.master_archive()
        terminal = """eval "$(declare -f fuLOSS_CONFIRM | sed 's/\\[ -t 0 \\]/true/')"\n"""
        code, out = self.run_check(archive, late=terminal)
        self.assertEqual(code, 5, out)
        self.assertIn("ASKED: --default no Accept this loss of data?", out)
        self.assertNotIn("Not confirmed: no terminal", out)
        self.assert_manual(out, data)
        self.assertNotIn("RUN", self.calls())
        self.assertIn("DID: fail: Not updated: the step through Elasticsearch 8.19 was not confirmed", out)

    def test_y_is_no_confirmation_and_only_its_key_confirms_it(self):
        data = self.data(os.path.join(self.tpotce, "data"))
        found = os.path.join(self.home, "found")
        for line, confirmed in (("@@tpot loss elastic Elasticsearch 8.18.2 -> 9.5.4 upgrades the data.", False),
                                ("@@tpot loss elastic-step The data of Elasticsearch 8.18.2 ...", True)):
            with self.subTest(line=line):
                shutil.rmtree(self.backups, ignore_errors=True)
                self.forget_calls()
                self.write("found", line + "\n")
                code, out = self.run_check(self.master_archive(), extra=f'myCONFIRMED=y myACCEPT_FILE="{found}"\n')
                self.assertEqual(code, 0 if confirmed else 5, out)
                self.assertEqual(bool(self.runs()), confirmed, out)
                if confirmed:
                    self.assertIn("(confirmed with --accept-data-loss-file)", out)
                else:
                    self.assert_manual(out, data)

    def test_old_818_confirmed_backup_fails_refuses(self):
        data = self.data(os.path.join(self.tpotce, "data"))
        self.write("es_need", "900000000\n")                                  # no room for it in the backup
        self.write("enabled", "")
        archive = self.master_archive()
        before = self.read_bytes(archive)
        code, out = self.run_check(archive, extra=self.ACCEPT)
        self.assertEqual(code, 5, out)
        self.assertIn("(confirmed with --accept-data-loss)", out)
        flat = self.assert_ways_on(out)
        self.assert_manual(out, data)
        calls = self.calls()
        for step in ("RUN", "docker pull", "docker network create", "chown", "tar rf"):
            self.assertNotIn(step, calls)
        self.assertEqual(self.read_bytes(archive), before)
        self.order(f"restore.sh -f {archive} -c", "systemctl start tpot.service")
        self.assertIn("DID: fail: Not updated: the Elasticsearch data did not go into the backup of this run, the "
                      "step through 8.19 needs it there, the checkout and the configuration are back.", flat)
        self.assertIn("DID: info: The Elasticsearch data did not go into the backup: no room for it in the backup:",
                      flat)
        self.assertIn(f"DID: next: Make room in {self.backups}", flat)
        self.assertNotIn("accept-data-loss (", flat.replace("(confirmed with --accept-data-loss)", ""))

    def test_old_818_confirmed_runs_the_step_then_goes_on(self):
        data = self.data(os.path.join(self.tpotce, "data"))
        archive = self.master_archive()
        code, out = self.run_check(archive, extra=self.ACCEPT)
        self.assertEqual(code, 0, out)
        self.assertIn("went on", out)                                          # on to the pull of the release
        self.assertIn("data/elk/data/nodes/0/live", self.members(self.full))   # the backup first, then the step
        self.order("tar rf", "docker info --format {{.DockerRootDir}}", f"docker pull {self.es_image}",
                   f"docker pull {self.kb_image}", "docker network create tpot_es_step",
                   f"sudo chown -R 1000:0 {data}/elk/data", "RUN [run] [-d] [--name] [tpot_es_step]",
                   "RUN [run] [-d] [--name] [tpot_kb_step]",
                   "curl -s -m 40 http://127.0.0.1:64398/_cluster/health?wait_for_status=yellow&timeout=30s",
                   "http://127.0.0.1:64396/api/status", "docker stop -t 60 tpot_kb_step",
                   "docker stop -t 120 tpot_es_step", "docker network rm tpot_es_step",
                   f"docker rmi {self.es_image} {self.kb_image}", f"sudo chown -R 2000:2000 {data}/elk/data")
        es, kb = self.runs()
        self.assertEqual(es, ["run", "-d", "--name", "tpot_es_step", "--network", "tpot_es_step",
                              "-p", "127.0.0.1:64398:9200", "-v", f"{data}/elk/data:/usr/share/elasticsearch/data",
                              "-e", "discovery.type=single-node", "-e", "xpack.security.enabled=false",
                              "-e", "cluster.routing.allocation.disk.threshold_enabled=false",
                              "-e", "cluster.name=tpotcluster", "-e", "node.name=tpotcluster-node-01",
                              "-e", "xpack.ml.enabled=false", "-e", "ES_JAVA_OPTS=-Xms1g -Xmx1g", self.es_image])
        self.assertEqual(kb, ["run", "-d", "--name", "tpot_kb_step", "--network", "tpot_es_step",
                              "-p", "127.0.0.1:64396:5601", "-e", "ELASTICSEARCH_HOSTS=http://tpot_es_step:9200",
                              "-e", "TELEMETRY_OPTIN=false", self.kb_image])
        self.assert_cleaned_up(data)
        self.assertIn("DID: ok: Elasticsearch data upgraded to 8.19 for the step to 9.5.4.", out)
        self.assertIn(f"ITEM ok:The Elasticsearch data is now in {self.full}.", out)
        self.assertNotIn("restore.sh", self.calls())
        self.assertNotIn("systemctl", self.calls())

    def test_master_full_archive_counts_and_the_step_runs(self):
        """master's --full archive holds data/elk/data of ~/tpotce/data already: no append, the step runs."""
        self.data(os.path.join(self.tpotce, "data"))
        archive = self.master_archive({"data/elk/data/nodes/0/live": "live\n"},
                                      name="20261005101010_tpot_backup_full.tar")
        code, out = self.run_check(archive, extra=self.ACCEPT)
        self.assertEqual(code, 0, out)
        self.assertNotIn("tar rf", self.calls())
        self.assertEqual(len(self.runs()), 2)
        self.assertTrue(os.path.exists(archive))

    def test_step_sets_usesve_only_on_aarch64(self):
        data = self.data(os.path.join(self.tpotce, "data"))
        for arch, sve in (("aarch64", True), ("arm64", True), ("x86_64", False)):
            with self.subTest(arch=arch):
                shutil.rmtree(self.backups, ignore_errors=True)
                self.forget_calls()
                self.write("arch", arch + "\n")
                code, out = self.run_check(self.master_archive(), extra=self.ACCEPT)
                self.assertEqual(code, 0, out)
                es, kb = self.runs()
                if sve:
                    self.assertIn("ES_JAVA_OPTS=-Xms1g -Xmx1g -XX:UseSVE=0", es)
                    self.assertEqual(es[-3:], ["-e", "CLI_JAVA_OPTS=-XX:UseSVE=0", self.es_image])
                else:
                    self.assertIn("ES_JAVA_OPTS=-Xms1g -Xmx1g", es)
                    self.assertFalse([word for word in es + kb if "SVE" in word], es)
                self.assertFalse([word for word in kb if "JAVA" in word], kb)
                self.assertIn(f"{data}/elk/data:/usr/share/elasticsearch/data", es)

    def test_step_timeout_cleans_up_and_refuses(self):
        data = self.data(os.path.join(self.tpotce, "data"))
        self.write("enabled", "")
        for health, why in (("red", "the cluster of Elasticsearch 8.19.23 stayed red"),
                            ("none", "Elasticsearch 8.19.23 did not answer within")):
            with self.subTest(health=health):
                shutil.rmtree(self.backups, ignore_errors=True)
                self.forget_calls()
                self.write("es_health", health + "\n")
                archive = self.master_archive()
                code, out = self.run_check(archive, extra=self.ACCEPT)
                self.assertEqual(code, 5, out)
                flat = " ".join(out.split())
                self.assertIn(f"ERROR: The step through Elasticsearch 8.19 did not go through: "
                              f"{why.replace('8.19.23', self.version)}", flat)
                self.assertIn(f"may be changed already: the backup {self.full} puts the data from before the step "
                              "back, the summary says how.", flat)
                self.assert_cleaned_up(data)
                calls = self.calls()
                self.assertIn(f"restore.sh -f {self.full} -c", calls)              # the checkout goes back
                self.assertNotIn("systemctl start", calls)                         # not on the changed data
                self.assertNotIn("api/status", calls)
                self.assertIn("DID: warn: T-Pot stays stopped: the release before may not start on the data the "
                              "step through Elasticsearch 8.19 changed.", flat)
                # the restore.sh of the release before knows no -g: by hand, the changed data aside until the
                # archive's is back; the same way on the output and in the summary
                how = (f"sudo mv {data}/elk/data {data}/elk/data.step && sudo tar xpf {self.full} --numeric-owner "
                       f"-C {data} --strip-components=1 data/elk/data && sudo rm -rf {data}/elk/data.step (should the "
                       f"tar fail: sudo rm -rf {data}/elk/data; sudo mv {data}/elk/data.step {data}/elk/data)")
                self.assertIn(f"To put the data from before the step back: {how}", flat)
                self.assertIn(f"DID: next: Put the Elasticsearch data from before the step back: {how}", flat)
                self.assertIn("DID: next: Then start T-Pot (sudo systemctl start tpot): the release from before this "
                              "update reads that data again.", flat)
                self.assertIn("DID: info: tpot.service is enabled: a reboot before the data is back", flat)
                self.assertIn(f"Copy {self.full} out of {self.backups} before the update runs again", flat)
                self.assertNotIn("went on", out)
                self.assertNotIn("To take the step by hand", flat)

    def test_the_restore_hint_takes_g_where_restore_sh_knows_it(self):
        data = self.data(os.path.join(self.tpotce, "data"))
        with open(os.path.join(self.tpotce, "restore.sh"), "a") as out:
            out.write('# while getopts ":lf:ycg:B:h" opt; do\n')
        self.write("dead_tpot_es_step", "")                                    # it ends on the data
        code, out = self.run_check(self.master_archive(), extra=self.ACCEPT)
        self.assertEqual(code, 5, out)
        flat = " ".join(out.split())
        self.assertIn(f"Elasticsearch {self.version} ended on the data", flat)
        self.assertIn(f"DID: next: Put the Elasticsearch data from before the step back: ./restore.sh -f {self.full} "
                      "-g data", flat)
        self.assert_cleaned_up(data)

    def test_a_failed_pull_touches_nothing_and_starts_tpot_again(self):
        data = self.data(os.path.join(self.tpotce, "data"))
        self.write("enabled", "")
        self.write("pull_fail", "")
        code, out = self.run_check(self.master_archive(), extra=self.ACCEPT)
        self.assertEqual(code, 5, out)
        flat = self.assert_manual(out, data)
        self.assertIn("could not be pulled", flat)
        self.assertIn("The data was not touched.", flat)
        calls = self.calls()
        self.assertNotIn("RUN", calls)
        self.assertNotIn("chown", calls)
        self.assertNotIn(f"docker pull {self.kb_image}", calls)               # it stops at the first
        self.assertIn(f"docker rmi {self.es_image}", calls.splitlines())
        self.order(f"restore.sh -f {self.full} -c", "systemctl start tpot.service")

    def test_no_room_for_the_images_refuses_before_the_pull(self):
        data = self.data(os.path.join(self.tpotce, "data"))
        self.write("enabled", "")
        self.write("docker_avail", "1000000000\n")
        code, out = self.run_check(self.master_archive(), extra=self.ACCEPT)
        self.assertEqual(code, 5, out)
        flat = self.assert_manual(out, data)
        self.assertIn(f"The images of the step need about 4096 MB free in {self.home}/docker-root, it has 953 MB.",
                      flat)
        self.assertIn(f"DID: next: Make room in {self.home}/docker-root (about 4096 MB free for the images of the "
                      "step), then run the update again.", flat)
        calls = self.calls()
        for step in ("RUN", "docker pull", "chown"):
            self.assertNotIn(step, calls)
        self.order(f"restore.sh -f {self.full} -c", "systemctl start tpot.service")

    def test_a_signal_during_the_step_cleans_up(self):
        import signal
        data = self.data(os.path.join(self.tpotce, "data"))
        self.write("es_health", "none\n")
        for send in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=send):
                shutil.rmtree(self.backups, ignore_errors=True)
                self.forget_calls()
                asked = os.path.join(self.home, "health_asked")
                if os.path.exists(asked):
                    os.remove(asked)
                code, out = self.run_check(self.master_archive(), extra=self.ACCEPT + "myES_STEP_WAIT=60\n",
                                           signal_when=asked, send=send)
                self.assertEqual(code, 5, out)
                self.assertIn("did not go through: it was stopped", out)
                self.assert_cleaned_up(data)
                self.assertNotIn("systemctl start", self.calls())

    def test_check_data_loss_reports_the_step(self):
        self.CHECK = base.DataLossTest.CHECK.replace("ES_VER=8.19.0", "ES_VER=8.18.2")
        for need, hint in (("1000000", False), ("900000000", True)):
            with self.subTest(need=need):
                shutil.rmtree(os.path.join(self.home, "origin"), ignore_errors=True)
                self.target()
                self.write("es_need", need + "\n")
                out, lines = self.check(extra=self.DOCKER.split("timeout ()")[0])
                keys = [line.split()[2] for line in lines]
                self.assertIn("elastic-step", keys, out)
                self.assertNotIn("elastic", keys, out)
                line = [line for line in lines if line.split()[2] == "elastic-step"][0]
                self.assertTrue(line.startswith(
                    f"@@tpot loss elastic-step The data of Elasticsearch 8.18.2 cannot be read by 9.1.0: the update "
                    f"first runs Elasticsearch {self.version} once on it"), line)      # its edition has no Kibana
                self.assertEqual("the run refuses the step then" in out, hint, out)
                self.assertIn("rc=3", out)

    def test_819_and_9x_take_no_step(self):
        self.data(os.path.join(self.tpotce, "data"))
        dockerfile = os.path.join(self.tpotce, "docker", "elk", "elasticsearch", "Dockerfile")
        for old, new in (("8.19.0", "9.5.4"), ("9.3.5", "9.5.4"), ("8.18.2", "8.19.5"), ("7.17.9", "9.5.4")):
            with self.subTest(old=old, new=new):
                shutil.rmtree(self.backups, ignore_errors=True)
                self.forget_calls()
                self.write("es_old", old + "\n")
                with open(dockerfile, "w") as out:
                    out.write(f"FROM alpine\nARG ES_VER={new}\n")
                code, out = self.run_check(self.master_archive())
                self.assertEqual(code, 0, out)                                 # A1: into the backup, no question
                self.assertIn("went on", out)
                self.assertEqual(self.runs(), [])
                self.assertNotIn("docker pull", self.calls())
                self.assertNotIn("elastic-step", out)
        self.CHECK = base.DataLossTest.CHECK
        self.target()
        out, lines = self.check(extra=self.DOCKER.split("timeout ()")[0])
        self.assertEqual([line.split()[2] for line in lines if "elastic" in line.split()[2]], [], out)

    def test_without_kibana_the_step_runs_elasticsearch_only(self):
        """MOBILE runs Elasticsearch without Kibana: the step pulls, runs, waits for and removes no Kibana."""
        data = self.data(os.path.join(self.tpotce, "data"))
        self.edition(kibana=False)
        code, out = self.run_check(self.master_archive(), extra=self.ACCEPT)
        self.assertEqual(code, 0, out)
        self.assertIn("went on", out)
        self.assertEqual([run[3] for run in self.runs()], ["tpot_es_step"])
        calls = self.calls()
        self.assertNotIn(f"docker pull {self.kb_image}", calls)
        self.assertNotIn("api/status", calls)
        self.assertNotIn(self.kb_image, calls)
        self.assert_cleaned_up(data, kibana=False)
        self.assertIn(f"the update first runs Elasticsearch {self.version} once on it", " ".join(out.split()))
        # the way by hand waits for Elasticsearch then
        shutil.rmtree(self.backups, ignore_errors=True)
        self.forget_calls()
        code, out = self.run_check(self.master_archive())
        self.assertEqual(code, 5, out)
        flat = " ".join(out.split())
        self.assertNotIn("tpot_kb_step", flat)
        self.assertNotIn("api/status", flat)
        self.assertIn("wait until curl -s 'http://127.0.0.1:64398/_cluster/health?wait_for_status=yellow&timeout=30s' "
                      "says yellow or green", flat)
        self.assertIn("docker stop -t 120 tpot_es_step; docker rm tpot_es_step; docker network rm tpot_es_step", flat)

    def test_a_red_cluster_stops_the_wait_early(self):
        """Red for longer than myES_STEP_RED in a row ends the wait long before its bound."""
        data = self.data(os.path.join(self.tpotce, "data"))
        self.write("es_health", "red\n")
        started = time.time()
        code, out = self.run_check(self.master_archive(), extra=self.ACCEPT + "myES_STEP_WAIT=600 myES_STEP_RED=1\n")
        self.assertEqual(code, 5, out)
        self.assertLess(time.time() - started, 30)
        self.assertIn(f"the cluster of Elasticsearch {self.version} stayed red for more than 1 s", out)
        self.assert_cleaned_up(data)
        self.assertNotIn("api/status", self.calls())

    def head(self):
        return subprocess.run(["git", "-C", self.tpotce, "rev-parse", "HEAD"], capture_output=True,
                              universal_newlines=True).stdout.strip()

    def test_touched_without_rollback_says_not_to_start_on_this_checkout(self):
        """The archive holds no earlier commit: the new release stays, and it cannot read the data of 8.18."""
        data = self.data(os.path.join(self.tpotce, "data"))
        with open(os.path.join(self.tpotce, "restore.sh"), "a") as out:
            out.write('# while getopts ":lf:ycg:B:h" opt; do\n')
        self.write("enabled", "")
        self.write("dead_tpot_es_step", "")
        code, out = self.run_check(self.master_archive({"rollback.txt": self.head() + "\n"}), extra=self.ACCEPT)
        self.assertEqual(code, 5, out)
        flat = " ".join(out.split())
        self.assertIn("The checkout is already the new release: T-Pot stays stopped.", flat)
        self.assertIn(f"DID: next: Put the Elasticsearch data from before the step back: ./restore.sh -f {self.full} "
                      "-g data", flat)
        self.assertIn("DID: next: Do not start T-Pot on this checkout with that data: the new release cannot read the "
                      "data of Elasticsearch 8.18.2. Run the update again instead: it takes the step again.", flat)
        self.assertNotIn("Then start T-Pot", flat)
        calls = self.calls()
        self.assertNotIn("restore.sh -f", calls)
        self.assertNotIn("systemctl start", calls)
        self.assert_cleaned_up(data)

    def test_touched_with_a_failed_rollback_puts_the_checkout_back_first(self):
        data = self.data(os.path.join(self.tpotce, "data"))
        with open(os.path.join(self.tpotce, "restore.sh"), "w") as out:
            out.write('#!/bin/sh\necho "restore.sh $*" >> "$HOME/calls"\n# getopts ":lf:ycg:B:h"\nexit 1\n')
        self.write("dead_tpot_es_step", "")
        code, out = self.run_check(self.master_archive(), extra=self.ACCEPT)
        self.assertEqual(code, 5, out)
        flat = " ".join(out.split())
        self.assertIn("and the checkout could not be put back: T-Pot stays stopped.", flat)
        self.assertIn(f"DID: next: Put the Elasticsearch data from before the step back: ./restore.sh -f {self.full} "
                      "-g data", flat)
        self.assertIn("DID: next: Do not start T-Pot before the checkout is back as well: the new release cannot read "
                      f"the data of Elasticsearch 8.18.2. ./restore.sh -f {self.full} -c puts the checkout back", flat)
        self.assertIn("DID: next: Or leave the data as it is and run the update again: it takes the step again.", flat)
        self.assertNotIn("systemctl start", self.calls())

    def test_refused_without_rollback_names_what_the_next_start_does(self):
        """Not touched, no rollback: the next start cannot read the data (no upgrade from 8.18 to 9)."""
        self.data(os.path.join(self.tpotce, "data"))
        code, out = self.run_check(self.master_archive({"rollback.txt": self.head() + "\n"}))
        self.assertEqual(code, 5, out)
        flat = " ".join(out.split())
        self.assertIn("the next start of T-Pot cannot read the data of Elasticsearch 8.18.2: T-Pot stays stopped.", flat)
        self.assertNotIn("upgrades the data", flat)

    def test_the_accept_hint_without_the_handover(self):
        self.data(os.path.join(self.tpotce, "data"))
        code, out = self.run_check(self.master_archive(), prepared="")
        self.assertEqual(code, 5, out)
        flat = self.assert_ways_on(out)
        self.assertIn("DID: next: To take the step without a terminal: ./update.sh -y --accept-data-loss.", flat)
        self.assertNotIn("-- --accept-data-loss", flat)

    def test_a_failed_owner_back_is_named(self):
        data = self.data(os.path.join(self.tpotce, "data"))
        self.write("chown_back_fail", "")
        code, out = self.run_check(self.master_archive(), extra=self.ACCEPT)
        self.assertEqual(code, 0, out)
        self.assertIn(f"DID: warn: The owner of {data}/elk/data could not be set back after the step: sudo chown -R "
                      f"2000:2000 {data}/elk/data", out)
        # sudo is refreshed before each owner change: the pulls and the wait may outlast its timestamp
        self.order("docker network create", "sudo -v", "sudo chown -R 1000:0", "docker rmi", "sudo -v",
                   "sudo chown -R 2000:2000")

    def test_images_that_were_there_stay(self):
        data = self.data(os.path.join(self.tpotce, "data"))
        for image in (self.es_image, self.kb_image):
            self.write("have_" + image.rsplit("/", 1)[1], "")
        code, out = self.run_check(self.master_archive(), extra=self.ACCEPT)
        self.assertEqual(code, 0, out)
        calls = self.calls()
        self.assertNotIn("docker pull", calls)
        self.assertNotIn("docker rmi", calls)
        self.assertEqual(len(self.runs()), 2)
        self.assertIn(f"sudo chown -R 2000:2000 {data}/elk/data", calls)

    def test_ctrl_c_between_the_backup_and_the_step_is_a_refusal(self):
        import signal
        self.data(os.path.join(self.tpotce, "data"))
        self.write("enabled", "")
        self.write("df_slow", "")
        code, out = self.run_check(self.master_archive(), extra=self.ACCEPT,
                                   signal_when=os.path.join(self.home, "df_started"), send=signal.SIGINT)
        self.assertEqual(code, 5, out)
        self.assertIn("was stopped before it began", out)
        calls = self.calls()
        self.assertNotIn("RUN", calls)
        self.order(f"restore.sh -f {self.full} -c", "systemctl start tpot.service")

    def test_a_wrong_step_wait_is_a_usage_error(self):
        for value, ok in (("60", True), ("1200", True), ("86400", True), ("30m", False), ("59", False),
                          ("86401", False), ("0600", False), ("", False), ("1e3", False)):
            with self.subTest(value=value):
                result = self.bash(f'myES_STEP_WAIT="{value}"\n'
                                   'fuUI_USAGE_ERROR () { echo "USAGE: $1"; return 1; }\n'
                                   + self.functions("fuCHECK_STEP_WAIT") + 'fuCHECK_STEP_WAIT; echo "rc=$?"\n')
                self.assertIn("rc=0" if ok else "rc=1", result.stdout, result.stdout + result.stderr)
                self.assertEqual("USAGE: TPOT_ES_STEP_WAIT=" in result.stdout, not ok)
        # update.sh stops on it with the code of a wrong option, before sudo or anything else
        environment = dict(self.environment(), TPOT_ES_STEP_WAIT="30m")
        done = subprocess.run(["bash", UPDATE_SH, "-y"], cwd=REPO, capture_output=True, universal_newlines=True,
                              env=environment, stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("TPOT_ES_STEP_WAIT=30m is no whole number of seconds from 60 to 86400.", done.stdout + done.stderr)
        self.assertEqual(self.calls(), "")


class HandoverTest(Scripts):
    """The handover of an earlier update.sh (TPOT_UPDATE_PREPARED): it has stopped T-Pot, written its
    backup and reset the checkout already. What it handed over counts, never the files of the reset
    checkout, and a wrong setting of this run no longer ends it with T-Pot stopped."""

    STANDARD = "# T-Pot: STANDARD\nservices:\n  tpotinit:\n    image: x\n"

    def test_handover_with_empty_edition_is_unknown_not_standard(self):
        """master hands over an empty edition for another TPOT_DOCKER_COMPOSE or no compose file: the
        docker-compose.yml here is the reset one of the release, it says nothing about the edition."""
        for name, text in ((".env", "TPOT_TYPE=HIVE\n"), ("docker-compose.yml", self.STANDARD)):
            with open(os.path.join(self.tpotce, name), "w", encoding="utf-8") as out:
                out.write(text)
        call = 'fuCHECK_EDITION\necho "EDITION=${myEDITION} CUSTOMIZED=${myCOMPOSE_CUSTOMIZED}"\n'
        result = self.source_update(call, TPOT_UPDATE_PREPARED="1", TPOT_UPDATE_EDITION="",
                                    TPOT_UPDATE_ARCHIVE="/b/x.tar")
        out = result.stdout + result.stderr
        self.assertIn("EDITION=UNKNOWN CUSTOMIZED=\n", out)
        self.assertNotIn("Edition STANDARD", out)
        self.assertIn("handed over no edition", out)
        # the first pass still reads the file
        result = self.source_update(call, TPOT_UPDATE_PREPARED=None, TPOT_UPDATE_EDITION=None)
        self.assertIn("EDITION=STANDARD", result.stdout, result.stdout + result.stderr)
        # an edition that was handed over stays as it is
        result = self.source_update(call, TPOT_UPDATE_PREPARED="1", TPOT_UPDATE_EDITION="MINI")
        self.assertIn("EDITION=MINI", result.stdout, result.stdout + result.stderr)

    def test_a_wrong_step_wait_in_a_handover_falls_back_to_1200(self):
        """T-Pot is stopped and the checkout pulled when the handover starts this script: a usage error
        would leave it so. The run goes on with 1200 s and says so, in the summary too."""
        result = self.source_update('fuCHECK_STEP_WAIT; echo "rc=$? wait=${myES_STEP_WAIT}"\n',
                                    TPOT_UPDATE_PREPARED="1", TPOT_ES_STEP_WAIT="30m")
        self.assertIn("rc=0 wait=1200\n", result.stdout, result.stdout + result.stderr)
        self.assertNotIn("USAGE", result.stdout + result.stderr)
        # the whole run: no usage error, the warning after the start of the run and in its summary
        with open(os.path.join(self.tpotce, "version"), "w", encoding="utf-8") as out:
            out.write("20.06.0\n")
        result = self.run_plain(UPDATE_SH, "-y", cwd=self.tpotce, TPOT_UPDATE_PREPARED="1",
                                TPOT_ES_STEP_WAIT="30m")
        out = result.stdout + result.stderr
        self.assertNotIn("is no whole number of seconds from 60 to 86400.", out)
        text = ("TPOT_ES_STEP_WAIT=30m is no whole number of seconds from 60 to 86400, the step through "
                "Elasticsearch 8.19 waits 20 min (1200 s) instead.")
        self.assertIn(text, out)
        tail = result.stdout[result.stdout.index("### The update did not finish"):]
        self.assertIn(f"### [WARNING] - {text}", tail)
        self.assertIn("20.06.0 cannot be upgraded automatically", tail)        # it went on to the checks

    def test_a_wrong_step_wait_outside_a_handover_is_a_usage_error(self):
        result = self.run_plain(UPDATE_SH, "-y", cwd=self.tpotce, TPOT_UPDATE_PREPARED=None,
                                TPOT_ES_STEP_WAIT="59")
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, out)
        self.assertIn("TPOT_ES_STEP_WAIT=59 is no whole number of seconds from 60 to 86400.", out)
        self.assertNotIn("### The update did not finish", out)
        self.assertEqual(self.calls(), "")


# compose/customizer.py of the checkout for the tests of --adopt: every call goes into HOME/calls (with the
# file it adopts in HOME/adopted_input), the report on stdout from HOME/adopt_report, the exit code from
# HOME/adopt_rc (HOME/adopt_rc_dry for --dry-run); without --dry-run and with 0 it writes -o with the text
# of HOME/adopt_out
ADOPT_STUB = textwrap.dedent('''\
    # a stub of compose/customizer.py --adopt
    import os, shutil, sys
    home = os.environ["HOME"]
    args = sys.argv[1:]
    with open(os.path.join(home, "calls"), "a") as out:
        out.write("customizer " + " ".join(args) + "\\n")
    def read(name, default=""):
        try:
            with open(os.path.join(home, name)) as handle:
                return handle.read()
        except OSError:
            return default
    dry = "--dry-run" in args
    shutil.copy(args[args.index("--adopt") + 1], os.path.join(home, "adopted_input"))
    rc = int((read("adopt_rc_dry" if dry else "adopt_rc", "0") or "0").strip() or "0")
    sys.stdout.write(read("adopt_report", "kept-out: kibana: mem_limit 1g (the release has 2g)\\n"))
    if rc == 0 and not dry:
        with open(args[args.index("-o") + 1], "w") as out:
            out.write(read("adopt_out", ADOPTED))
    sys.exit(rc)
''').replace("ADOPTED", repr("# T-Pot: CUSTOM\n# customizer: version=2 base=STANDARD\nservices: {}\n"))


class ComposeAdoptTest(Scripts):
    """A compose file that is no edition: one of the customizer before v2 ('# T-Pot: CUSTOM EDITION') is
    rebuilt with compose/customizer.py --adopt without a question; one of your own is adapted only after a
    yes at a terminal (default: cancel) or with --adapt-compose, before the stop in the first pass, after
    the pull (from the file of the archive) after a handover. A no stops the update: before the stop
    nothing changed, after the handover the checkout goes back and T-Pot starts again."""

    V1 = "# T-Pot: CUSTOM EDITION\n# Generated on: 2025-03-01 12:00:00\nservices:\n  cowrie:\n    image: x\n"
    OWN = "services:\n  kibana:\n    image: mine\n    mem_limit: 1g\n"
    STANDARD = "# T-Pot: STANDARD\nservices:\n  tpotinit:\n    image: release\n"
    ADOPTED = "# T-Pot: CUSTOM\n# customizer: version=2 base=STANDARD\nservices: {}\n"

    def setUp(self):
        super().setUp()
        os.makedirs(os.path.join(self.tpotce, "compose"))
        self.write(os.path.join(self.tpotce, "compose", "customizer.py"), ADOPT_STUB)
        self.write(os.path.join(self.tpotce, "compose", "standard.yml"), self.STANDARD)
        self.write(os.path.join(self.tpotce, "compose", "sensor.yml"), "# T-Pot: SENSOR\nservices: {}\n")
        self.write(os.path.join(self.tpotce, "compose", "tpot_services.yml"), "services: {}\n")
        self.write(os.path.join(self.tpotce, ".env"), "TPOT_TYPE=HIVE\n")
        restore = os.path.join(self.tpotce, "restore.sh")
        self.write(restore, '#!/bin/sh\necho "restore.sh $*" >> "$HOME/calls"\n')
        os.chmod(restore, 0o755)
        # systemctl: is-enabled says enabled, every call is logged
        self.stub("systemctl", '#!/bin/sh\necho "systemctl $*" >> "$HOME/calls"\n'
                               '[ "$1" = "is-enabled" ] && echo enabled\nexit 0\n')
        git = ["git", "-C", self.tpotce, "-c", "user.email=t@t", "-c", "user.name=t"]
        subprocess.run(["git", "init", "-q", self.tpotce], check=True)
        subprocess.run(git + ["checkout", "-q", "-b", "master"], check=True)
        subprocess.run(git + ["add", "-A"], check=True)
        subprocess.run(git + ["commit", "-q", "-m", "before"], check=True)
        self.before = subprocess.run(["git", "-C", self.tpotce, "rev-parse", "HEAD"], capture_output=True,
                                     universal_newlines=True, check=True).stdout.strip()
        subprocess.run(git + ["commit", "-q", "--allow-empty", "-m", "release"], check=True)

    def write(self, path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as out:
            out.write(text)

    def read(self, path):
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def archive(self, compose=None, env="TPOT_TYPE=HIVE\n"):
        members = {"MANIFEST": "T-Pot backup\n", "rollback.txt": self.before + "\n", "env": env}
        if compose is not None:
            members["docker-compose.yml"] = compose
        return base.make_backup(self.home, members)

    def compose(self):
        return self.read(os.path.join(self.tpotce, "docker-compose.yml"))

    def summary(self, out):
        return [line for line in out.splitlines() if line.startswith("### [")]

    # --- a file of the customizer before v2 ---------------------------------------------------------

    def test_v1_is_rebuilt_without_a_question(self):
        self.write(os.path.join(self.tpotce, "docker-compose.yml"), self.V1)
        archive = self.archive(self.V1)
        self.write(os.path.join(self.home, "adopt_report"),
                   "dropped: spiderfoot (removed in this release)\nport: cowrie:2222/tcp=22\n"
                   "new: conpot_x (in this release, not in your file: compose/customizer.py --add conpot_x)\n"
                   "kept-out: cowrie: environment COWRIE_X=1\n")
        result = self.source_update(f'myRUNNING=1 myARCHIVE=""\nfuCHECK_EDITION\nmyARCHIVE="{archive}"\n'
                                    'fuRESTORE_EDITION\necho "went on"\nexit 0\n')
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        self.assertIn("went on", out)
        self.assertNotIn("Choice", out)
        self.assertNotIn("compose file of your own", out)
        self.assertEqual(self.compose(), self.ADOPTED)
        calls = [line for line in self.calls().splitlines() if line.startswith("customizer ")]
        self.assertEqual(len(calls), 2, calls)
        self.assertIn("--dry-run", calls[0])                                # before the stop: only a check
        self.assertNotIn("--dry-run", calls[1])
        self.assertIn("--report", calls[1])
        self.assertIn("--tpot-type HIVE", calls[1])
        self.assertIn("--old-catalog", calls[1])                            # the catalogue that ran
        self.assertEqual(self.read(os.path.join(self.home, "adopted_input")), self.V1)
        self.assertIn(f"tar xOf {archive} docker-compose.yml | diff - {self.tpotce}/docker-compose.yml", out)
        self.assertIn("dropped: spiderfoot (removed in this release)", out)
        self.assertIn("your host port, kept: cowrie:2222/tcp=22", out)
        self.assertIn("compose/customizer.py --add conpot_x", out)
        summary = self.summary(result.stdout)
        self.assertIn("### [WARNING] - Not carried over from your docker-compose.yml: cowrie: environment "
                      "COWRIE_X=1", summary)
        self.assertIn("### [WARNING] - Dropped from your docker-compose.yml: spiderfoot (removed in this release)",
                      summary)
        self.assertFalse([line for line in summary if "conpot_x" in line or "2222" in line], summary)

    def test_v1_rebuild_failure_falls_back_to_own(self):
        """--adopt ends with 3 (conflicts): the file counts as one of your own, it cannot be adapted either,
        so the update stops before anything changed."""
        self.write(os.path.join(self.tpotce, "docker-compose.yml"), self.V1)
        for rc in ("3", "2"):
            with self.subTest(rc=rc):
                self.write(os.path.join(self.home, "adopt_rc_dry"), rc + "\n")
                result = self.source_update('myRUNNING=1\nfuCHECK_EDITION\necho "went on"\n')
                out = result.stdout + result.stderr
                self.assertEqual(result.returncode, 1, out)
                self.assertNotIn("went on", out)
                self.assertIn(f"cannot rebuild your docker-compose.yml of the customizer before v2 (--adopt ended "
                              f"with {rc}), it counts as a compose file of your own.", out)
                self.assertIn("is a compose file of your own", out)
                self.assertIn(f"It cannot be adapted automatically: compose/customizer.py --adopt ended with {rc}",
                              out)
                self.assertEqual(self.compose(), self.V1)
        # after the pull: the rebuild fails, the file counts as yours, without a terminal the update goes back
        self.write(os.path.join(self.home, "adopt_rc_dry"), "0\n")
        self.write(os.path.join(self.home, "adopt_rc"), "2\n")
        archive = self.archive(self.V1)
        self.write(os.path.join(self.tpotce, "docker-compose.yml"), self.STANDARD)
        result = self.source_update(f'myRUNNING=1 mySTOPPED=1 myEDITION=UNKNOWN myARCHIVE="{archive}"\n'
                                    'fuRESTORE_EDITION\necho "went on"\n')
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, out)
        self.assertIn("it counts as a compose file of your own", out)
        self.assertIn(f"restore.sh -f {archive} -c", self.calls())

    # --- a compose file of your own -----------------------------------------------------------------

    def first_pass(self, *args, **extra):
        """update.sh -y of this checkout up to its edition check: the version of the release, the internet
        check answers, a branch to update from."""
        with open(os.path.join(REPO, "version"), encoding="utf-8") as handle:
            self.write(os.path.join(self.tpotce, "version"), handle.read())
        self.stub("curl", '#!/bin/sh\necho "curl $*" >> "$HOME/calls"\nexit 0\n')
        return self.run_plain(UPDATE_SH, "-y", *args, cwd=self.tpotce, TPOT_UPDATE_PREPARED=None, **extra)

    def test_own_without_tty_cancels_before_stop_and_backup(self):
        self.write(os.path.join(self.tpotce, "docker-compose.yml"), self.OWN)
        result = self.first_pass()
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, out[-1500:])
        calls = self.calls()
        self.assertIn("--dry-run", calls)
        self.assertNotIn("systemctl stop", calls)
        self.assertFalse([c for c in calls.splitlines() if c.startswith("docker ")], calls)   # no export, stop, pull
        backups = os.path.join(self.home, "tpot_backups")
        self.assertEqual([n for n in os.listdir(backups) if n.endswith(".tar")] if os.path.isdir(backups) else [], [])
        self.assertEqual(self.compose(), self.OWN)
        self.assertIn("Not adapted: no terminal. --adapt-compose adapts it, -y does not.", out)
        self.assertIn("not carried over: kibana: mem_limit 1g (the release has 2g)", out)
        flat = " ".join(out.split())
        self.assertIn("tpot edition set <edition>, or cp compose/<edition>.yml docker-compose.yml", flat)
        self.assertIn("your selection of services: compose/customizer.py", flat)
        self.assertIn("diff -u docker-compose.yml compose/standard.yml", flat)
        self.assertIn("and keep: kibana: mem_limit 1g (the release has 2g)", flat)
        tail = result.stdout[result.stdout.index("### The update did not finish"):]
        self.assertIn("### [FAILED] - Not updated: your docker-compose.yml is a compose file of your own and was "
                      "not adapted, nothing was changed.", tail)
        self.assertIn("T-Pot was left running.", tail)
        self.assertNotIn("Each run of the update.sh that started this run", out)   # no backup was written

    def pty(self, script, answer):
        """The script (update.sh sourced, gum off) in a pty, the answer typed at the choice."""
        line = f'source {shlex.quote(UPDATE_SH)}\n{script}'
        _code, out, _piped = ptyhelper.run(["bash", "-c", line], self.env(TPOT_GUM="off"), cwd=self.tpotce,
                                           size=(120, 49), answers=(("Choice (1-2", answer),), timeout=30)
        return out.decode("utf-8", "replace").replace("\r\n", "\n")

    def test_own_at_tty_lists_kept_out_before_the_choice_and_cancel_is_default(self):
        self.write(os.path.join(self.tpotce, "docker-compose.yml"), self.OWN)
        out = self.pty('myRUNNING=1\nfuCHECK_EDITION\necho "went on"\n', b"\r")
        self.assertIn("Choice (1-2, enter = 2)", out)                       # Cancel is the default
        self.assertIn("1) Back up and adapt automatically", out)
        self.assertIn("2) Cancel and adapt it myself", out)
        self.assertLess(out.index("is a compose file of your own"), out.index("Choice (1-2"))
        self.assertLess(out.index("not carried over: kibana: mem_limit 1g"), out.index("Choice (1-2"))
        self.assertNotIn("went on", out)
        self.assertIn("Not updated: your docker-compose.yml is a compose file of your own and was not adapted", out)
        self.assertEqual(self.compose(), self.OWN)
        self.assertNotIn("systemctl", self.calls())

    def test_own_adapt_rebuilds_and_warns(self):
        self.write(os.path.join(self.tpotce, "docker-compose.yml"), self.OWN)
        archive = self.archive(self.OWN)
        out = self.pty('myRUNNING=1\nfuCHECK_EDITION\necho "adapt=${myADAPT_COMPOSE}"\n'
                       f'myARCHIVE="{archive}"\nfuRESTORE_EDITION\nexit 0\n', b"1\r")
        self.assertIn("adapt=1", out)                       # a restarted update.sh gets --adapt-compose
        self.assertEqual(out.count("Choice (1-2"), 1, out)                  # asked once, before the stop
        self.assertEqual(self.compose(), self.ADOPTED)
        self.assertIn(f"tar xOf {archive} docker-compose.yml | diff - {self.tpotce}/docker-compose.yml", out)
        self.assertIn("### [WARNING] - Not carried over from your docker-compose.yml: kibana: mem_limit 1g (the "
                      "release has 2g)", out)
        self.assertIn("### [OK] - Your docker-compose.yml is adapted to this release", out)
        self.assertIn("### T-Pot is updated", out)

    def test_adapt_compose_flag_without_tty_adapts(self):
        self.write(os.path.join(self.tpotce, "docker-compose.yml"), self.OWN)
        archive = self.archive(self.OWN)
        result = self.source_update(f'myRUNNING=1 myADAPT_COMPOSE=1\nfuCHECK_EDITION\nmyARCHIVE="{archive}"\n'
                                    'fuRESTORE_EDITION\nexit 0\n')
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        self.assertIn("--adapt-compose: back up your compose file and adapt it to this release; the kept-out list "
                      "above is not carried over.", out)
        self.assertLess(out.index("not carried over: kibana"), out.index("--adapt-compose: back up"))
        self.assertEqual(self.compose(), self.ADOPTED)
        self.assertIn("Not carried over from your docker-compose.yml: kibana: mem_limit 1g", result.stdout)
        # the option: read before getopts, handed on to a restarted update.sh that knows it
        text = base.read("update.sh")
        main = text.split("################\n# Main section #\n################", 1)[1]
        self.assertIn("--adapt-compose) myADAPT_COMPOSE=1 ;;", main)
        self.assertIn('fuSELFUPDATE "$@" ${myLOSS_ARGS[@]+"${myLOSS_ARGS[@]}"} ${myADAPT_COMPOSE:+--adapt-compose}',
                      main)
        y = re.search(r"\n    y\)\n(.*?);;", main, re.S).group(1)
        self.assertNotIn("myADAPT_COMPOSE", y)                               # -y does not choose
        restart = self.source_update(f'fuRESTART_ARGS {shlex.quote(UPDATE_SH)} -F --adapt-compose -s\n')
        self.assertEqual(restart.stdout.split(), ["-F", "--adapt-compose", "-s"], restart.stderr)
        older = os.path.join(self.home, "older.sh")
        self.write(older, 'while getopts ":yFsob:r:B:h" opt; do\n')
        restart = self.source_update(f'fuRESTART_ARGS {shlex.quote(older)} -F --adapt-compose -s\n')
        self.assertEqual(restart.stdout.split(), ["-F", "-s"], restart.stderr)
        # the whole run: the option is taken, the question is not asked
        self.write(os.path.join(self.home, "adopt_rc_dry"), "3\n")           # stops right after the check
        self.write(os.path.join(self.tpotce, "docker-compose.yml"), self.OWN)
        result = self.first_pass("--adapt-compose")
        out = result.stdout + result.stderr
        self.assertNotIn("Unknown option", out)
        self.assertIn("It cannot be adapted automatically", out)

    # --- after the handover of the update.sh of the release before ------------------------------------

    def handover(self, call, edition="UNKNOWN", **extra):
        return self.source_update(call, TPOT_UPDATE_PREPARED="1", TPOT_UPDATE_EDITION=edition, **extra)

    def test_handover_own_cancel_rolls_back_and_starts_tpot(self):
        self.write(os.path.join(self.tpotce, "docker-compose.yml"), self.STANDARD)     # reset by the pull
        archive = self.archive(self.OWN)
        result = self.handover(f'myRUNNING=1 myARCHIVE="{archive}"\nfuCHECK_EDITION\nfuRESTORE_EDITION\n'
                               'echo "went on"\n', TPOT_UPDATE_ARCHIVE=archive)
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, out)
        self.assertNotIn("went on", out)
        calls = self.calls()
        self.assertIn(f"restore.sh -f {archive} -c", calls)
        self.assertIn("systemctl start tpot", calls)
        self.assertLess(calls.index("restore.sh"), calls.index("systemctl start"))
        self.assertIn("not carried over: kibana: mem_limit 1g", out)
        self.assertIn("Not adapted: no terminal.", out)
        tail = result.stdout[result.stdout.index("### The update did not finish"):]
        self.assertIn("### [FAILED] - Not updated: your docker-compose.yml is a compose file of your own and was "
                      "not adapted, the checkout and the configuration are back.", tail)
        self.assertIn("T-Pot runs again, on the release from before this update.", tail)
        # the update.sh before wrote its backup already, and does so on every run
        self.assertIn("Each run of the update.sh that started this run writes a backup", tail)
        self.assertNotIn("T-Pot is stopped", tail)
        # without an earlier commit in the archive: your file back as it was, T-Pot stays stopped
        self.forget()
        archive = base.make_backup(self.home, {"MANIFEST": "x\n", "env": "TPOT_TYPE=HIVE\n",
                                               "docker-compose.yml": self.OWN}, name="20261006101010_tpot_backup.tar")
        result = self.handover(f'myRUNNING=1 mySTOPPED=1 myARCHIVE="{archive}"\nfuCHECK_EDITION\nfuRESTORE_EDITION\n')
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, out)
        self.assertNotIn("restore.sh", self.calls())
        self.assertNotIn("systemctl start", self.calls())
        self.assertEqual(self.compose(), self.OWN)
        self.assertIn("T-Pot is stopped, 'sudo systemctl start tpot' brings it back.", result.stdout)

    def forget(self):
        if os.path.exists(os.path.join(self.home, "calls")):
            os.remove(os.path.join(self.home, "calls"))

    def test_handover_reads_the_archive_compose_not_the_reset_one(self):
        """The checkout is reset already: the compose file of the archive is the one adapted, as the type of
        the env of the archive says (the .env here may be the one of the release)."""
        self.write(os.path.join(self.tpotce, "docker-compose.yml"), self.STANDARD)
        archive = self.archive(self.OWN, env="TPOT_TYPE=SENSOR\n")
        for edition in ("UNKNOWN", ""):
            with self.subTest(edition=edition):
                self.forget()
                self.write(os.path.join(self.tpotce, "docker-compose.yml"), self.STANDARD)
                result = self.handover(f'myRUNNING=1 myADAPT_COMPOSE=1 myARCHIVE="{archive}"\nfuCHECK_EDITION\n'
                                       'fuRESTORE_EDITION\nexit 0\n', edition=edition)
                out = result.stdout + result.stderr
                self.assertEqual(result.returncode, 0, out)
                self.assertEqual(self.read(os.path.join(self.home, "adopted_input")), self.OWN)
                self.assertEqual(self.compose(), self.ADOPTED)
                calls = [line for line in self.calls().splitlines() if line.startswith("customizer ")]
                self.assertTrue(calls and all("--tpot-type SENSOR" in line for line in calls), calls)
                self.assertTrue(all("--old-catalog" in line for line in calls), calls)
                self.assertNotIn("Edition STANDARD", out)

    def test_handover_with_another_compose_file_leaves_it(self):
        """No edition handed over because TPOT_DOCKER_COMPOSE names another file: the tracked one stays as
        the release has it, no warning about an edition it does not match."""
        self.write(os.path.join(self.tpotce, "docker-compose.yml"), self.STANDARD)
        archive = self.archive(self.OWN, env="TPOT_TYPE=HIVE\nTPOT_DOCKER_COMPOSE=./mine.yml\n")
        result = self.handover(f'myRUNNING=1 myARCHIVE="{archive}"\nfuCHECK_EDITION\nfuRESTORE_EDITION\nexit 0\n',
                               edition="")
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        self.assertIn("Your compose file is ./mine.yml (TPOT_DOCKER_COMPOSE), it is not touched.", out)
        self.assertNotIn("matches no edition", out)
        self.assertNotIn("compose file of your own", out)
        self.assertNotIn("customizer", self.calls())
        self.assertEqual(self.compose(), self.STANDARD)

    def test_handover_without_a_compose_file_puts_the_edition_of_tpot_type(self):
        self.write(os.path.join(self.tpotce, "docker-compose.yml"), self.STANDARD)
        for env, edition, template in (("TPOT_TYPE=SENSOR\n", "SENSOR", "# T-Pot: SENSOR\nservices: {}\n"),
                                       ("TPOT_TYPE=HIVE\n", "STANDARD", self.STANDARD)):
            with self.subTest(edition=edition):
                archive = self.archive(None, env=env)
                result = self.handover(f'myRUNNING=1 myARCHIVE="{archive}"\nfuCHECK_EDITION\nfuRESTORE_EDITION\n'
                                       'exit 0\n', edition="")
                out = result.stdout + result.stderr
                self.assertEqual(result.returncode, 0, out)
                self.assertNotIn("[ERROR]", out)
                self.assertIn(f"There was no docker-compose.yml before the update, the {edition} edition of this "
                              "release is put in place", out)
                self.assertIn(f"### [WARNING] - There was no docker-compose.yml before the update", result.stdout)
                self.assertEqual(self.compose(), template)
                self.assertNotIn("customizer", self.calls())

    # --- --check-data-loss ----------------------------------------------------------------------------

    def test_check_data_loss_reports_own_compose(self):
        cases = ((self.OWN, "", True), (self.V1, "", False), (self.STANDARD, "", False),
                 (self.OWN, "TPOT_DOCKER_COMPOSE=./mine.yml\n", False))
        for compose, env, found in cases:
            with self.subTest(compose=compose[:20], env=env):
                self.write(os.path.join(self.tpotce, ".env"), "TPOT_TYPE=HIVE\n" + env)
                self.write(os.path.join(self.tpotce, "docker-compose.yml"), compose)
                result = self.source_update('myCHECK_LOSS=1 myLOSSES=0\nfuCOMPOSE_LOSS\necho "losses=${myLOSSES}"\n')
                out = result.stdout + result.stderr
                lines = [line for line in result.stdout.splitlines() if line.startswith("@@tpot loss ")]
                self.assertEqual(len(lines), 1 if found else 0, out)
                self.assertIn(f"losses={1 if found else 0}", out)
        self.write(os.path.join(self.tpotce, ".env"), "TPOT_TYPE=HIVE\n")
        self.write(os.path.join(self.tpotce, "docker-compose.yml"), self.OWN)
        self.write(os.path.join(self.home, "adopt_report"),
                   "kept-out: kibana: mem_limit 1g (the release has 2g)\nkept-out: volumes: mine\n")
        result = self.source_update('fuCOMPOSE_LOSS\n')
        self.assertIn("@@tpot loss compose Your docker-compose.yml is a compose file of your own: the update adapts "
                      "it to the release only after a yes (--adapt-compose), the backup keeps your file. Not carried "
                      "over: kibana: mem_limit 1g (the release has 2g); volumes: mine.\n", result.stdout)
        self.assertEqual(self.compose(), self.OWN)                             # a dry --adopt, nothing written
        self.assertIn("--dry-run", self.calls())
        self.write(os.path.join(self.home, "adopt_rc_dry"), "3\n")
        result = self.source_update('fuCOMPOSE_LOSS\n')
        self.assertIn("@@tpot loss compose Your docker-compose.yml is a compose file of your own and cannot be adapted "
                      "to the release automatically (compose/customizer.py --adopt ended with 3)", result.stdout)
        # the check runs it for an update, not for --backup-only
        check = re.search(r"function fuCHECK_DATA_LOSS \(\) \{.*?\n\}", base.read("update.sh"), re.S).group(0)
        block = check[check.index('if [ -z "${myBACKUP_ONLY}" ];'):check.index("if fuCOMPOSE_HAS elasticsearch")]
        self.assertIn("fuCOMPOSE_LOSS", block)

    def test_backup_only_does_not_ask(self):
        self.write(os.path.join(self.tpotce, "docker-compose.yml"), self.OWN)
        result = self.source_update('myRUNNING=1 myBACKUP_ONLY=1\nfuCHECK_EDITION\necho "went on"\nexit 0\n')
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        self.assertIn("went on", out)
        self.assertNotIn("customizer", self.calls())
        self.assertIn("Unable to determine the edition, the backup keeps docker-compose.yml as it is.", out)

    def test_with_the_real_customizer(self):
        """The plumbing against compose/customizer.py of this checkout and the venv of the T-Pot Manager:
        a v1 file of the MINI services is rebuilt into a file of the customizer v2."""
        venv = os.path.join(os.path.expanduser("~"), ".local", "share", "tpotce", "venv")
        if not os.path.exists(os.path.join(venv, "bin", "python3")):
            self.skipTest("no venv of the T-Pot Manager")
        shutil.rmtree(os.path.join(self.tpotce, "compose"))
        os.symlink(os.path.join(REPO, "compose"), os.path.join(self.tpotce, "compose"))
        os.symlink(os.path.join(REPO, "tpotctl"), os.path.join(self.tpotce, "tpotctl"))
        os.makedirs(os.path.join(self.home, "data", "tpotce"))
        os.symlink(venv, os.path.join(self.home, "data", "tpotce", "venv"))
        v1 = self.read(os.path.join(REPO, "compose", "mini.yml")).replace("# T-Pot: MINI\n",
                                                                         "# T-Pot: CUSTOM EDITION\n", 1)
        self.write(os.path.join(self.tpotce, "docker-compose.yml"), v1)
        archive = self.archive(v1)
        result = self.source_update(f'myRUNNING=1 myARCHIVE=""\nfuCHECK_EDITION\nmyARCHIVE="{archive}"\n'
                                    'fuRESTORE_EDITION\nexit 0\n')
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out[-2000:])
        self.assertTrue(self.compose().startswith("# T-Pot: CUSTOM\n# customizer: version=2 base=STANDARD\n"),
                        self.compose()[:200])
        self.assertIn("rebuilt for this release", out)


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
