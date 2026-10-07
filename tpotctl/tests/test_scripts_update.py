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
import subprocess
import unittest

from tpotctl.tests import test_scripts as base
from tpotctl.tests import test_ui as ui

REPO = base.REPO
UPDATE_SH = os.path.join(REPO, "update.sh")
RESTORE_SH = os.path.join(REPO, "restore.sh")
LOGO = "telekom security"         # the credits of the logo, only the logo has them
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

    def test_the_help_shows_it_at_a_terminal(self):
        for script in (UPDATE_SH, RESTORE_SH):
            with self.subTest(script=os.path.basename(script)):
                out = self.terminal(script, "-h")
                self.assertEqual(out.count(LOGO), 1, out[-600:])
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
        for script in ("update.sh", "restore.sh"):
            with self.subTest(script=script):
                self.assertTrue(re.search(r"^myUI_LOGO=1$", base.read(script), re.M))

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
        self.assertTrue(re.search(r"sudo -v\n(?:\s*#.*\n)*\s*if ! fuUI_SPIN (?:[^\n]|\\\n)*?sudo tar cf", backup), backup[-900:])
        # the Elastic export asks Kibana and waits for it, the pause waits for Ctrl+C: both in the open
        self.assertNotIn("fuUI_SPIN", body("fuEXPORT_ELASTIC"))
        self.assertNotIn("fuUI_SPIN", body("fuCHECK_ELASTIC"))


if __name__ == "__main__":
    unittest.main()
