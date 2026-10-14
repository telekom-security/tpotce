"""The T-Pot scripts a person runs look like tpot: installer/lib/ui.sh, @@tpot marks, -B.

Bash harnesses: the scripts run in a temporary HOME with a sudo that only runs the
command, never on the real checkout.
"""

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import textwrap
import time
import unittest

from tpotctl.tests import isolate, ptyhelper

isolate()

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SUDO = "#!/bin/sh\n# runs the command without sudo, options of sudo are left out\nwhile [ $# -gt 0 ]; do\n" \
       "  case \"$1\" in -S|-k|-v|-n) shift ;; -p) shift 2 ;; *) break ;; esac\ndone\n" \
       "[ $# -eq 0 ] && exit 0\nexec \"$@\"\n"
# uname -s (and uname alone) says FAKE_UNAME_S, Linux without it; the rest is the real uname. The
# host scripts stop outside Linux (fuUI_LINUX_ONLY), the tests run on macOS as well
REAL_UNAME = shutil.which("uname") or "/usr/bin/uname"
UNAME = ('#!/bin/sh\ncase "$*" in\n  -s|"") echo "${FAKE_UNAME_S-Linux}"; exit 0 ;;\nesac\n'
         f'exec {REAL_UNAME} "$@"\n')


def read(path):
    with open(os.path.join(REPO, path), encoding="utf-8") as handle:
        return handle.read()


def at_terminal(argv, env, cwd, answers=(), timeout=60):
    """argv with stdin, stdout and stderr on a pty: (exit code, output). answers: (text, keys) in their
    order, the keys are typed once the text is in the output (after the text of the answer before)."""
    code, out, _piped = ptyhelper.run(argv, env, cwd, answers=answers, timeout=timeout)
    return code, out.decode("utf-8", "replace").replace("\r\n", "\n")


@unittest.skipUnless(shutil.which("bash"), "no bash")
class Harness(unittest.TestCase):

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="tpot-scripts-")
        self.addCleanup(shutil.rmtree, self.home)
        self.bin = os.path.join(self.home, "bin")
        os.makedirs(self.bin)
        for name, text in (("sudo", SUDO), ("systemctl", "#!/bin/sh\necho \"systemctl $*\" >> \"$HOME/calls\"\n"),
                           ("docker", "#!/bin/sh\necho \"docker $*\" >> \"$HOME/calls\"\n"), ("uname", UNAME)):
            path = os.path.join(self.bin, name)
            with open(path, "w", encoding="utf-8") as out:
                out.write(text)
            os.chmod(path, 0o755)

    def run_script(self, script, *args, env=None, cwd=None):
        environment = dict(os.environ, HOME=self.home, PATH=f"{self.bin}:{os.environ['PATH']}", TPOT_GUM="off")
        environment.pop("TPOT_MARKS", None)
        environment.update(env or {})
        return subprocess.run(["bash", script] + list(args), capture_output=True, universal_newlines=True,
                              env=environment, cwd=cwd or self.home, stdin=subprocess.DEVNULL, timeout=60)


class UpdateShTest(Harness):

    def test_runs_without_ui_sh(self):
        """A checkout of an earlier release has no installer/lib/ui.sh (the self update)."""
        alone = os.path.join(self.home, "old")
        os.makedirs(alone)
        shutil.copy(os.path.join(REPO, "update.sh"), alone)
        result = self.run_script(os.path.join(alone, "update.sh"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("command not found", result.stdout + result.stderr)
        self.assertIn("### T-Pot Updater", result.stdout)

    def test_with_ui_sh_plain(self):
        result = self.run_script(os.path.join(REPO, "update.sh"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("### T-Pot Updater", result.stdout)
        self.assertIn("'-y'", result.stdout)

    def test_no_raw_hash_lines_left(self):
        text = read("update.sh")
        fallback = text[text.index("# >>> plain fallback"):text.index("# <<< plain fallback")]
        rest = text.replace(fallback, "")
        self.assertEqual(re.findall(r'.*echo -?n? ?"#{3}.*', rest), [])
        self.assertNotIn("$myBLUE", rest)

    def test_phases_are_marked(self):
        text = read("update.sh")
        for key in ("check", "backup", "selfupdate", "restore", "pull", "cleanup", "start", "done"):
            self.assertTrue(re.search(rf'fuMARK phase "?{key}"? [A-Z]', text), key)

    def test_become_file_option(self):
        text = read("update.sh")
        self.assertIn("-B <file>", text)
        self.assertTrue(re.search(r'getopts ":[a-zA-Z:]*B:', text))

    def test_selfupdate_hands_on_all_options(self):
        self.assertIn('exec bash "$0" -y "${myRESTART[@]}"', read("update.sh"))
        self.assertIn('fuSELFUPDATE "$@"', read("update.sh"))

    def restart_args(self, script_text, *args):
        function = re.search(r"function fuRESTART_ARGS \(\) \{.*?\n\}", read("update.sh"), re.S).group(0)
        target = os.path.join(self.home, "target.sh")
        with open(target, "w", encoding="utf-8") as out:
            out.write(script_text)
        result = subprocess.run(["bash", "-c", f'{function}\nfuRESTART_ARGS "$@"', "x", target] + list(args),
                                capture_output=True, universal_newlines=True)
        return result.stdout.split("\n")[:-1]

    def test_restart_into_an_older_update_sh_drops_the_become_file(self):
        old = 'while getopts ":yFsb:r:h" opt; do\n'
        self.assertEqual(self.restart_args(old, "-y", "-B", "/run/f", "-F", "-b", "master"),
                         ["-y", "-F", "-b", "master"])

    RESTORE_NOW = '#!/bin/sh\n# while getopts ":lf:ycg:B:h" opt; do\necho "marks=${TPOT_MARKS:-none} args=$*"\nexit 1\n'
    RESTORE_OLD = '#!/bin/sh\n# while getopts ":lf:ycg:h" opt; do\necho "marks=${TPOT_MARKS:-none} args=$*"\nexit 1\n'

    def rollback(self, restore_text, become=""):
        function = "\n".join(re.search(rf"function {name} \(\) \{{.*?\n\}}", read("update.sh"), re.S).group(0)
                             for name in ("fuCAN_ROLLBACK", "fuROLLBACK_CHECKOUT"))
        tpotce = os.path.join(self.home, "tpotce")
        os.makedirs(tpotce)
        subprocess.run(["git", "init", "-q", tpotce], check=True)
        subprocess.run(["git", "-C", tpotce, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
                        "--allow-empty", "-m", "now"], check=True)
        fake = os.path.join(tpotce, "restore.sh")
        with open(fake, "w", encoding="utf-8") as out:
            out.write(restore_text)
        os.chmod(fake, 0o755)
        archive = make_backup(self.home, {"rollback.txt": "0123456789abcdef\n"})
        script = (f'source "{REPO}/installer/lib/ui.sh"; fuUI_INIT; myARCHIVE="{archive}"; '
                  f'myBECOME_FILE="{become}"\n{function}\nfuROLLBACK_CHECKOUT; echo "rc=$?"')
        result = subprocess.run(["bash", "-c", script], capture_output=True, universal_newlines=True,
                                env=dict(os.environ, HOME=self.home, TPOT_MARKS="1", TPOT_GUM="off"))
        return result.stdout + result.stderr

    @staticmethod
    def args_of(text):
        return text.split("args=")[1].split("\n")[0]

    def test_rollback_runs_restore_sh_without_marks_and_says_when_it_fails(self):
        text = self.rollback(self.RESTORE_NOW)
        self.assertIn("marks=none", text)
        self.assertIn("rc=1", text)
        self.assertTrue("could not be put back completely" in text)
        self.assertNotIn("-B", self.args_of(text))

    def test_rollback_marks_the_old_checkout(self):
        text = self.rollback(self.RESTORE_NOW.replace("exit 1", "exit 0"))
        self.assertIn("rc=0", text)
        self.assertIn("@@tpot changed back", text)

    def test_a_failed_rollback_does_not_say_back(self):
        self.assertNotIn("@@tpot changed back", self.rollback(self.RESTORE_NOW))

    def test_rollback_hands_the_become_file_on(self):
        text = self.rollback(self.RESTORE_NOW, become="/run/tpot-become")
        self.assertIn("-c -B /run/tpot-become", self.args_of(text))

    def test_rollback_leaves_the_become_file_out_for_an_older_restore_sh(self):
        text = self.rollback(self.RESTORE_OLD, become="/run/tpot-become")
        self.assertNotIn("-B", self.args_of(text))

    @unittest.skipUnless(shutil.which("sha256sum"), "no sha256sum (macOS), runs in the VM")
    def test_selfupdate_marks_a_moved_head_only(self):
        function = re.search(r"function fuSELFUPDATE \(\) \{.*?\n\}", read("update.sh"), re.S).group(0)
        origin = os.path.join(self.home, "origin")
        clone = os.path.join(self.home, "tpotce")
        git = ["-c", "user.email=t@t", "-c", "user.name=t"]
        subprocess.run(["git", "init", "-q", origin], check=True)
        subprocess.run(["git", "-C", origin] + git + ["commit", "-q", "--allow-empty", "-m", "one"], check=True)
        subprocess.run(["git", "clone", "-q", origin, clone], check=True)
        me = os.path.join(self.home, "update.sh")
        with open(me, "w", encoding="utf-8") as out:
            out.write("# stays the same\n")
        script = (f'source "{REPO}/installer/lib/ui.sh"; fuUI_INIT; fuSWITCH_SOURCE () {{ :; }}\n{function}\n'
                  f'cd "{self.home}" && fuSELFUPDATE')

        def run():
            return subprocess.run(["bash", "-c", script, me], capture_output=True, universal_newlines=True,
                                  env=dict(os.environ, HOME=self.home, TPOT_MARKS="1", TPOT_GUM="off")).stdout
        self.assertNotIn("@@tpot changed checkout", run())            # nothing new upstream
        subprocess.run(["git", "-C", origin] + git + ["commit", "-q", "--allow-empty", "-m", "two"], check=True)
        self.assertIn("@@tpot changed checkout", run())

    def tpot_setup(self, link_is_a_file):
        """fuTPOT_SETUP with a stub launcher and the link in the test home."""
        function = re.search(r"function fuTPOT_SETUP \(\) \{.*?\n\}", read("update.sh"), re.S).group(0)
        launcher = os.path.join(self.home, "tpotce", "tpot")
        os.makedirs(os.path.dirname(launcher))
        with open(launcher, "w", encoding="utf-8") as out:
            out.write("#!/bin/sh\nexit 0\n")
        os.chmod(launcher, 0o755)
        link = os.path.join(self.home, "usr-local-bin-tpot")
        if link_is_a_file:
            with open(link, "w", encoding="utf-8") as out:
                out.write("#!/bin/sh\n")
        script = os.path.join(self.home, "setup.sh")
        with open(script, "w", encoding="utf-8") as out:
            out.write(f'source "{REPO}/installer/lib/ui.sh"; fuUI_INIT\nmyTPOT_LINK="{link}"\n'
                      f'myLOG="{self.home}/update.log"\nfuDID () {{ :; }}\n{function}\nfuTPOT_SETUP\n')
        result = self.run_script(script)
        return result.stdout + result.stderr, os.path.islink(link)

    def test_tpot_setup_links_the_manager(self):
        out, linked = self.tpot_setup(link_is_a_file=False)
        self.assertTrue(linked, out)
        self.assertTrue("The T-Pot Manager is ready" in out, out)

    def test_tpot_setup_warns_about_a_file_of_its_own(self):
        out, linked = self.tpot_setup(link_is_a_file=True)
        self.assertFalse(linked, out)
        self.assertTrue("[WARNING]" in out and "leaves it alone" in out, out)

    def test_the_elastic_check_comes_before_the_tpot_setup(self):
        main = read("update.sh").split("\nfuREMOVE_DROPPED_SERVICES\n", 1)[1]     # the main section
        self.assertLess(main.index("\nfuCHECK_ELASTIC\n"), main.index("\nfuTPOT_SETUP\n"))

    @staticmethod
    def check_elastic():
        return re.search(r"function fuCHECK_ELASTIC \(\) \{.*?\n\}", read("update.sh"), re.S).group(0)

    def test_the_trap_only_covers_the_question(self):
        """Ctrl+C at the question of the Elasticsearch upgrade is a no (DataLossTest runs it)."""
        text = self.check_elastic()
        self.assertTrue(re.search(r"trap fuELASTIC_REFUSED INT\n\s*if fuLOSS_CONFIRM elastic .*\n\s*then\n"
                                  r"\s*trap - INT\n", text), text[-900:])
        refused = re.search(r"function fuELASTIC_REFUSED \(\) \{\n\ttrap - INT\n", read("update.sh"))
        self.assertTrue(refused)
        # the append of the Elasticsearch data (Ctrl+C is a no there as well) puts back the trap it found
        # on both of its ways out, the rest of update.sh has the five above
        append = re.search(r"function fuAPPEND_ES_DATA \(\) \{.*?\n\}", read("update.sh"), re.S).group(0)
        self.assertEqual(len(re.findall(r'eval "\$\{myTRAP:-trap - INT\}"', append)), 2)
        # the step through 8.19: INT, TERM and HUP only end it, its cleanup cannot be stopped, the traps it
        # found come back; Ctrl+C from its question up to the step is a refusal, after it the trap goes
        step = re.search(r"function fuES_STEP_819 \(\) \{.*?\n\}", read("update.sh"), re.S).group(0)
        self.assertIn("trap 'myES_STEP_STOPPED=\"1\"' INT TERM HUP", step)
        self.assertTrue(re.search(r"trap '' INT TERM HUP\n\tfuES_STEP_CLEANUP \"\$1\"\n"
                                  r"\teval \"\$\{myTRAP_INT:-trap - INT\}\"\n\teval \"\$\{myTRAP_TERM:-trap - TERM\}\"\n"
                                  r"\teval \"\$\{myTRAP_HUP:-trap - HUP\}\"", step), step)
        block = re.search(r"trap fuES_STEP_REFUSED INT\n\s*fuLOSS_CONFIRM elastic-step .*?\n\s*trap - INT\n"
                          r"\s*fuDID ok \"Elasticsearch data upgraded", text, re.S)
        self.assertTrue(block, text[-900:])
        self.assertEqual(re.findall(r"trap .*INT", block.group(0)), ["trap fuES_STEP_REFUSED INT", "trap - INT"])
        self.assertTrue(re.search(r"function fuES_STEP_REFUSED \(\) \{\n\ttrap - INT\n", read("update.sh")))
        rest = read("update.sh").replace(append, "").replace(step, "")
        self.assertEqual(len(re.findall(r"trap .*INT", rest)), 8)

    def test_the_refusal_never_names_full(self):
        """The update.sh of the release before makes room for --full by deleting older backups without a
        question: no hint or summary item of the Elasticsearch check sends anyone there."""
        text = read("update.sh")
        for name in ("fuCHECK_ELASTIC", "fuELASTIC_REFUSED", "fuES_ROOM_TEXT", "fuES_ACCEPT_TEXT",
                     "fuAPPEND_ES_DATA", "fuRESTART_AFTER_REFUSAL", "fuES_STEP_REFUSED_ITEMS", "fuES_STEP_RESTORE_ITEMS",
                     "fuES_STEP_MANUAL", "fuES_STEP_ROOM", "fuES_STEP_819", "fuES_STEP_RESTORE_TEXT"):
            body = re.search(rf"function {name} \(\) \{{.*?\n\}}", text, re.S).group(0)
            said = [line for line in body.splitlines()
                    if re.search(r"fuUI_(HINT|WARN|ERROR|OK)|fuDID|echo \"|myTEXT=|myES_APPEND_WHY=", line)]
            with self.subTest(name=name):
                self.assertTrue(said)
                self.assertFalse([line for line in said if "--full" in line], said)

    def test_restart_into_this_update_sh_keeps_it(self):
        self.assertEqual(self.restart_args(read("update.sh"), "-y", "-B", "/run/f", "-F"),
                         ["-y", "-B", "/run/f", "-F"])

    def test_marks_with_tpot_marks(self):
        result = self.run_script(os.path.join(REPO, "update.sh"), "-y", "--backup-only",
                                 env={"TPOT_MARKS": "1"})
        # no ~/tpotce here: the run ends early, but it says where it is
        self.assertIn("@@tpot phase", result.stdout)


def make_backup(home, members, name="20261005101010_tpot_backup.tar"):
    """An archive like update.sh writes, with the given {member: text}."""
    folder = os.path.join(home, "tpot_backups")
    os.makedirs(folder, exist_ok=True)
    stage = tempfile.mkdtemp(prefix="tpot-stage-")
    path = os.path.join(folder, name)
    with tarfile.open(path, "w") as archive:
        for member, text in members.items():
            target = os.path.join(stage, member)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8") as out:
                out.write(text)
            archive.add(target, arcname=member)
    shutil.rmtree(stage)
    return path


MANIFEST = "T-Pot backup\nwritten: 2026-10-05 10:10\nversion: 24.04.2\nedition: STANDARD\n"


class RestoreShTest(Harness):

    def setUp(self):
        super().setUp()
        self.tpotce = os.path.join(self.home, "tpotce")
        os.makedirs(self.tpotce)
        with open(os.path.join(self.tpotce, ".env"), "w", encoding="utf-8") as out:
            out.write("TPOT_TYPE=HIVE\nNOW=1\n")
        self.archive = make_backup(self.home, {"MANIFEST": MANIFEST, "env": "TPOT_TYPE=HIVE\nBEFORE=1\n",
                                               "rollback.txt": "0123456789abcdef\n"})

    def restore(self, *args, env=None):
        return self.run_script(os.path.join(REPO, "restore.sh"), *args, env=env)

    def test_list_without_ui_sh(self):
        alone = os.path.join(self.home, "old")
        os.makedirs(alone)
        shutil.copy(os.path.join(REPO, "restore.sh"), alone)
        result = self.run_script(os.path.join(alone, "restore.sh"), "-l")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("command not found", result.stdout + result.stderr)
        self.assertIn(os.path.basename(self.archive), result.stdout)

    def test_groups_without_asking(self):
        result = self.restore("-f", self.archive, "-g", "config", env={"TPOT_MARKS": "1"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with open(os.path.join(self.tpotce, ".env"), encoding="utf-8") as handle:
            self.assertIn("BEFORE=1", handle.read())
        self.assertIn("@@tpot phase config", result.stdout)
        self.assertNotIn("@@tpot phase git", result.stdout)        # rollback.txt is there, not chosen

    def test_a_group_the_archive_does_not_have(self):
        result = self.restore("-f", self.archive, "-g", "data")
        self.assertEqual(result.returncode, 0)
        self.assertIn("data", result.stdout + result.stderr)
        with open(os.path.join(self.tpotce, ".env"), encoding="utf-8") as handle:
            self.assertIn("NOW=1", handle.read())

    def test_unknown_group(self):
        result = self.restore("-f", self.archive, "-g", "config,nonsense")
        self.assertEqual(result.returncode, 1)

    def test_a_failed_group_ends_with_1_after_the_others(self):
        archive = make_backup(self.home, {"MANIFEST": MANIFEST, "env": "TPOT_TYPE=HIVE\nBEFORE=1\n",
                                          "untracked/notes.txt": "x\n"}, name="20261005121212_tpot_backup.tar")
        os.chmod(os.path.join(self.tpotce, ".env"), 0o444)     # read only: cp of .env fails
        self.addCleanup(os.chmod, os.path.join(self.tpotce, ".env"), 0o644)
        result = self.restore("-f", archive, "-g", "config,untracked")
        self.assertEqual(result.returncode, 1, result.stdout[-400:] + result.stderr[-400:])
        self.assertTrue(os.path.exists(os.path.join(self.tpotce, "notes.txt")))     # the other group is back
        text = result.stdout + result.stderr
        self.assertTrue("Not restored: config." in text, text[-400:])
        self.assertTrue("The rest is back." in text, text[-400:])

    def test_everything_restored_ends_with_0(self):
        result = self.restore("-f", self.archive, "-g", "config")
        self.assertEqual(result.returncode, 0)
        self.assertFalse("Not restored" in result.stdout + result.stderr)

    def fake(self, name, text):
        path = os.path.join(self.bin, name)
        with open(path, "w", encoding="utf-8") as out:
            out.write(text)
        os.chmod(path, 0o755)

    def git_checkout_with_a_patch(self):
        """~/tpotce as a git repository with one tracked file and an archive whose patch changes it."""
        git = ["git", "-C", self.tpotce, "-c", "user.email=t@t", "-c", "user.name=t"]
        subprocess.run(["git", "init", "-q", self.tpotce], check=True)
        with open(os.path.join(self.tpotce, "a.txt"), "w", encoding="utf-8") as out:
            out.write("one\n")
        subprocess.run(git + ["add", "a.txt"], check=True)
        subprocess.run(git + ["commit", "-q", "-m", "one"], check=True)
        patch = "diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-one\n+two\n"
        return make_backup(self.home, {"MANIFEST": MANIFEST, "tracked.patch": patch},
                           name="20261005131313_tpot_backup.tar")

    def test_a_patch_that_does_not_apply_after_the_check_ends_with_1(self):
        archive = self.git_checkout_with_a_patch()
        real = shutil.which("git")
        self.fake("git", f'#!/bin/sh\nfor a in "$@"; do [ "$a" = "--check" ] && exec "{real}" "$@"; done\n'
                         f'for a in "$@"; do [ "$a" = "apply" ] && {{ echo "error: simulated" >&2; exit 1; }}; done\n'
                         f'exec "{real}" "$@"\n')
        result = self.restore("-f", archive, "-g", "patch")
        text = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, text[-400:])
        self.assertTrue("Not restored: patch." in text, text[-400:])
        kept = os.listdir(os.path.join(self.home, "tpot_backups"))
        self.assertTrue(any(name.endswith("_tracked.patch") for name in kept), kept)

    def test_an_unreadable_elastic_folder_ends_with_1(self):
        archive = make_backup(self.home, {"MANIFEST": MANIFEST, "elastic/kibana_export.ndjson": "{}\n"},
                              name="20261005141414_tpot_backup.tar")
        real = shutil.which("tar")
        self.fake("tar", f'#!/bin/sh\n[ "$1" = "xf" ] && [ "$5" = "elastic" ] && '
                         f'{{ echo "tar: simulated" >&2; exit 2; }}\nexec "{real}" "$@"\n')
        self.fake("curl", "#!/bin/sh\nexit 0\n")          # a Kibana that answers, nothing leaves the host
        result = self.restore("-f", archive, "-g", "elastic", env={"TPOT_KIBANA_TIMEOUT": "0"})
        text = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, text[-400:])
        self.assertTrue("Could not read elastic/" in text, text[-400:])
        self.assertTrue("Not restored: elastic." in text, text[-400:])
        self.assertFalse("The rest is back" in text, text[-400:])      # nothing else was chosen

    def test_an_unreadable_patch_is_not_called_empty(self):
        archive = self.git_checkout_with_a_patch()
        real = shutil.which("tar")
        self.fake("tar", f'#!/bin/sh\n[ "$1" = "xf" ] && [ "$5" = "tracked.patch" ] && exit 2\n'
                         f'exec "{real}" "$@"\n')
        result = self.restore("-f", archive, "-g", "patch")
        text = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, text[-400:])
        self.assertFalse("The patch is empty" in text, text[-400:])

    def test_an_unreadable_rollback_txt_is_not_called_empty(self):
        real = shutil.which("tar")
        self.fake("tar", f'#!/bin/sh\n[ "$1" = "xOf" ] && [ "$3" = "rollback.txt" ] && exit 2\n'
                         f'exec "{real}" "$@"\n')
        result = self.restore("-f", self.archive, "-g", "git")
        text = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, text[-400:])
        self.assertTrue("Could not read rollback.txt" in text, text[-400:])

    def two_commits(self):
        git = ["git", "-C", self.tpotce, "-c", "user.email=t@t", "-c", "user.name=t"]
        subprocess.run(["git", "init", "-q", self.tpotce], check=True)
        subprocess.run(git + ["commit", "-q", "--allow-empty", "-m", "one"], check=True)
        first = subprocess.run(git + ["rev-parse", "HEAD"], capture_output=True, universal_newlines=True).stdout
        subprocess.run(git + ["commit", "-q", "--allow-empty", "-m", "two"], check=True)
        return first.strip()

    def test_a_rollback_marks_the_changed_checkout(self):
        archive = make_backup(self.home, {"MANIFEST": MANIFEST, "rollback.txt": self.two_commits() + "\n"},
                              name="20261005151515_tpot_backup.tar")
        result = self.restore("-f", archive, "-g", "git", env={"TPOT_MARKS": "1"})
        self.assertEqual(result.returncode, 0, result.stdout[-400:])
        self.assertIn("@@tpot changed checkout", result.stdout)

    def test_an_applied_patch_marks_the_changed_checkout(self):
        archive = self.git_checkout_with_a_patch()
        result = self.restore("-f", archive, "-g", "patch", env={"TPOT_MARKS": "1"})
        self.assertEqual(result.returncode, 0, result.stdout[-400:])
        self.assertIn("@@tpot changed checkout", result.stdout)

    def test_a_failed_group_is_marked(self):
        self.two_commits()
        result = self.restore("-f", self.archive, "-g", "git", env={"TPOT_MARKS": "1"})   # 0123... is no commit
        self.assertEqual(result.returncode, 1)
        self.assertIn("@@tpot fail git", result.stdout)
        self.assertNotIn("@@tpot changed checkout", result.stdout)

    def test_become_file(self):
        text = read("restore.sh")
        self.assertIn("-B <file>", text)
        self.assertIn("-g <groups>", text)

    def test_no_raw_hash_lines_left(self):
        text = read("restore.sh")
        fallback = text[text.index("# >>> plain fallback"):text.index("# <<< plain fallback")]
        rest = text.replace(fallback, "")
        self.assertEqual(re.findall(r'.*echo -?n? ?"#{3}.*', rest), [])
        self.assertNotIn("$myBLUE", rest)
        self.assertNotIn("read -rp", rest)

    def test_group_texts_of_tpot_are_the_ones_of_restore_sh(self):
        from tpotctl import ops
        text = read("restore.sh")
        self.assertEqual(sorted(ops.GROUP_TEXT), sorted(["git", "patch", "config", "untracked", "data", "elastic"]))
        for group, question in ops.GROUP_TEXT.items():
            self.assertIn(question, text, group)


# curl for restore.sh: every call in HOME/curl_calls, a PUT of the ILM policy in HOME/puts, a GET of it
# answers with HOME/current_policy.json (none: 404, curl -f exits 22), everything else answers
CURL = ('#!/bin/sh\necho "curl $*" >> "$HOME/curl_calls"\nout=""; put=""; prev=""\n'
        'case "$*" in *64298*) [ -e "$HOME/es_down" ] && exit 7 ;; esac\n'
        'w=""\nfor a in "$@"; do\n  [ "$prev" = "-o" ] && out="$a"\n  [ "$prev" = "-w" ] && w=1\n'
        '  [ "$prev" = "-X" ] && [ "$a" = "PUT" ] && put=1\n  prev="$a"\ndone\n'
        'case "$*" in\n  *api/status*) [ -e "$HOME/kibana_down" ] && exit 7; exit 0 ;;\n  *_ilm/policy/tpot*)\n'
        '    if [ -n "$put" ]; then echo "PUT $*" >> "$HOME/puts"; exit 0; fi\n'
        '    code=200; [ -f "$HOME/current_policy.json" ] || code=404; [ -e "$HOME/policy_503" ] && code=503\n'
        '    [ "$code" = 200 ] && [ -n "$out" ] && cat "$HOME/current_policy.json" > "$out"\n'
        '    if [ -n "$w" ]; then printf "%s" "$code"; exit 0; fi\n'
        '    [ "$code" = 200 ] || exit 22; exit 0 ;;\nesac\nexit 0\n')


def ilm_policy(min_age):
    """An ILM policy tpot that deletes after min_age (None: no delete phase)."""
    phases = {"hot": {"min_age": "0ms", "actions": {}}}
    if min_age is not None:
        phases["delete"] = {"min_age": min_age, "actions": {"delete": {}}}
    return {"phases": phases}


class RestoreScriptHarness(Harness):
    """restore.sh sourced for its functions (it stops before its main section), the fuUI_* helpers
    replaced by lines that say what they were called with."""

    STUBS = textwrap.dedent("""
        fuUI_INFO () { echo "INFO: $*"; }; fuUI_OK () { echo "OK: $*"; }; fuUI_WARN () { echo "WARN: $*"; }
        fuUI_HINT () { echo "HINT: $*"; }; fuUI_ERROR () { echo "ERROR: $*"; }
        fuUI_CONFIRM () { echo "ASKED: $*"; [ -e "$HOME/yes" ]; }
        fuUI_SPIN () { shift 2; "$@"; }; fuMARK () { :; }; sleep () { :; }
    """)

    def setUp(self):
        super().setUp()
        self.tpotce = os.path.join(self.home, "tpotce")
        os.makedirs(self.tpotce)
        self.fake("curl", CURL)

    def fake(self, name, text):
        path = os.path.join(self.bin, name)
        with open(path, "w", encoding="utf-8") as out:
            out.write(text)
        os.chmod(path, 0o755)

    def write(self, path, text):
        with open(os.path.join(self.home, path), "w", encoding="utf-8") as out:
            out.write(text)

    def environment(self):
        environment = dict(os.environ, HOME=self.home, PATH=f"{self.bin}:{os.environ['PATH']}", TPOT_GUM="off")
        environment.pop("TPOT_MARKS", None)
        return environment

    def bash(self, script, terminal=False):
        """The script after restore.sh is sourced; terminal: stdin is a pty (the run may ask)."""
        text = f'source "{REPO}/restore.sh"\n' + self.STUBS + script
        if not terminal:
            return subprocess.run(["bash", "-c", text], cwd=REPO, capture_output=True, universal_newlines=True,
                                  env=self.environment(), stdin=subprocess.DEVNULL, timeout=60)
        import pty
        primary, secondary = pty.openpty()
        try:
            return subprocess.run(["bash", "-c", text], cwd=REPO, capture_output=True, universal_newlines=True,
                                  env=self.environment(), stdin=secondary, timeout=60)
        finally:
            os.close(primary)
            os.close(secondary)

    def puts(self):
        try:
            with open(os.path.join(self.home, "puts"), encoding="utf-8") as handle:
                return handle.read()
        except FileNotFoundError:
            return ""


class RestoreRetentionTest(RestoreScriptHarness):
    """A shorter TPOT_ES_RETENTION of the archive's .env waits (T-Pot never shortens on its own), a shorter
    ILM policy of the archive goes back only after a yes at a terminal or with --accept-data-loss."""

    def test_retention_shorter(self):
        cases = [("365", "30", 0), ("30", "365", 1), ("off", "30", 0), ("", "30", 0), ("30", "off", 1),
                 ("30", "", 1), ("30", "30", 1), ("090", "30", 0), ("30", "090", 1)]
        script = "".join(f'fuRETENTION_SHORTER "{old}" "{new}"; echo "{old}|{new}|$?"\n' for old, new, _ in cases)
        result = self.bash(script)
        lines = [line for line in result.stdout.splitlines() if line.count("|") == 2]
        self.assertEqual(lines, [f"{old}|{new}|{rc}" for old, new, rc in cases], result.stdout + result.stderr)

    def config(self, current, archived):
        with open(os.path.join(self.tpotce, ".env"), "w", encoding="utf-8") as out:
            out.write(f"TPOT_TYPE=HIVE\nTPOT_ES_RETENTION={current}\n")
        archive = make_backup(self.home, {"MANIFEST": MANIFEST, "env": f"TPOT_TYPE=HIVE\nTPOT_ES_RETENTION={archived}\n"})
        result = self.bash(f"myARCHIVE='{archive}' myDO_CONFIG=1\nfuTMPDIR; fuTOC\nfuDO_CONFIG\n")
        with open(os.path.join(self.tpotce, ".env"), encoding="utf-8") as handle:
            self.assertIn(f"TPOT_ES_RETENTION={archived}\n", handle.read())      # .env is restored as before
        return result.stdout + result.stderr

    def test_config_with_a_shorter_retention_says_it_waits(self):
        out = self.config("365", "30")
        hints = [line for line in out.splitlines() if line.startswith("HINT: ")]
        self.assertTrue(any("TPOT_ES_RETENTION=30" in line and "waits" in line
                            and "T-Pot does not shorten the ILM policy tpot on its own" in line for line in hints), out)
        self.assertIn("tpot elastic retention --force", out)
        self.assertIn("365", out)

    def test_config_from_off_to_days_says_it_waits(self):
        self.assertIn("waits", self.config("off", "30"))

    def test_config_from_no_value_says_what_t_pot_did(self):
        """An empty TPOT_ES_RETENTION leaves the policy as it was, which may delete at an age of its own."""
        out = " ".join(self.config("", "30").split())
        self.assertIn("than the value before (not set: T-Pot left the policy as it was (it may delete at its own "
                      "age)): T-Pot does not shorten", out)
        self.assertNotIn("forever", out)

    def test_config_with_a_longer_or_the_same_retention_says_nothing(self):
        for current, archived in (("30", "365"), ("30", "30"), ("30", "off")):
            with self.subTest(current=current, archived=archived):
                self.assertNotIn("waits", self.config(current, archived))

    def elastic(self, current, archived, accept=False, terminal=False, extra=""):
        """fuDO_ELASTIC with the current policy (its min_age, "none": no delete phase, None: there is no
        policy) and the one of the archive."""
        if current is not None:
            policy = ilm_policy(None if current == "none" else current)
            self.write("current_policy.json", json.dumps({"tpot": {"version": 3, "policy": policy}}))
        policy = archived if isinstance(archived, dict) else ilm_policy(archived)
        archive = make_backup(self.home, {"MANIFEST": MANIFEST, "elastic/ilm_policy_tpot.json":
                                          json.dumps({"policy": policy})})
        script = (f"myARCHIVE='{archive}' myDO_ELASTIC=1 myKIBANA_TIMEOUT=0 myACCEPT_LOSS={'1' if accept else ''}\n"
                  + extra + "fuTMPDIR; fuTOC\nfuDO_ELASTIC; echo \"rc=$?\"\necho \"failed=${myFAILED}\"\n"
                  'for myI in "${myWARNED[@]}"; do echo "warned=${myI}"; done\n')
        result = self.bash(script, terminal=terminal)
        return result.stdout + result.stderr

    def test_a_shorter_policy_without_terminal_is_not_put_back(self):
        out = self.elastic("365d", "30d")
        self.assertEqual(self.puts(), "", out)
        self.assertNotIn("ASKED", out)
        warns = [line for line in out.splitlines() if line.startswith("WARN: ")]
        self.assertTrue(any("30 days" in line and "365 days" in line for line in warns), out)
        self.assertIn("--accept-data-loss confirms it, -y does not", out)
        kept = os.path.join(self.home, "tpot_backups")
        manual = [line for line in out.splitlines() if "curl -X PUT" in line and "_ilm/policy/tpot" in line]
        self.assertTrue(manual, out)
        path = re.search(r"-d @(\S+)", manual[0]).group(1).rstrip("'")
        self.assertTrue(path.startswith(kept) and os.path.isfile(path), (path, out))    # survives the run
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["policy"]["phases"]["delete"]["min_age"], "30d")
        self.assertIn("failed=\n", out)                                   # a warning, no failure
        self.assertIn("warned=elastic:", out)

    def test_a_shorter_policy_with_accept_is_put_back(self):
        out = self.elastic("365d", "30d", accept=True)
        self.assertIn("PUT", self.puts(), out)
        self.assertIn("(confirmed with --accept-data-loss)", out)
        self.assertIn("OK: The ILM policy is back.", out)

    def test_a_shorter_policy_at_a_terminal_asks_with_no_as_default(self):
        out = self.elastic("365d", "30d", terminal=True)
        self.assertIn("ASKED: --default no", out)
        self.assertEqual(self.puts(), "", out)
        self.assertIn("warned=elastic:", out)
        self.write("yes", "")
        out = self.elastic("365d", "30d", terminal=True)
        self.assertIn("PUT", self.puts(), out)

    def test_a_policy_that_keeps_as_long_or_longer_goes_back_without_asking(self):
        for current, archived in (("30d", "365d"), ("30d", "30d"), ("30d", None)):
            with self.subTest(current=current, archived=archived):
                if os.path.exists(os.path.join(self.home, "puts")):
                    os.remove(os.path.join(self.home, "puts"))
                out = self.elastic(current, archived)
                self.assertIn("PUT", self.puts(), out)
                self.assertNotIn("WARN", out)

    def test_from_no_delete_phase_or_no_policy_to_days_is_shorter(self):
        for current in ("none", None):              # no delete phase: forever; no policy at all: forever
            with self.subTest(current=current):
                out = self.elastic(current, "30d")
                self.assertEqual(self.puts(), "", out)
                self.assertTrue(any(line.startswith("WARN: ") and "30 days" in line for line in out.splitlines()),
                                out)

    def test_a_policy_that_deletes_but_not_in_whole_days_asks(self):
        for min_age in ("36h", "0d", "12h", "1000000s"):
            with self.subTest(min_age=min_age):
                out = self.elastic("365d", min_age)
                self.assertEqual(self.puts(), "", out)
                self.assertIn("WARN: The ILM policy of the archive deletes events, but not after whole days "
                              f"(min_age {min_age}): restoring it may delete older events right away.", out)
                self.assertIn("--accept-data-loss confirms it, -y does not", out)
                self.assertIn("warned=elastic:", out)

    def test_a_delete_phase_without_min_age_says_it_is_not_set(self):
        policy = {"phases": {"hot": {"min_age": "0ms", "actions": {}}, "delete": {"actions": {"delete": {}}}}}
        out = self.elastic("365d", policy)
        self.assertIn("not after whole days (min_age not set): restoring it may delete older events right away.", out)
        self.assertNotIn("(min_age )", out)

    def test_config_without_a_current_retention_key_says_it_waits(self):
        """No TPOT_ES_RETENTION in the current .env: T-Pot left the policy as it was, 7 of the archive counts as
        shorter."""
        with open(os.path.join(self.tpotce, ".env"), "w", encoding="utf-8") as out:
            out.write("TPOT_TYPE=HIVE\n")
        archive = make_backup(self.home, {"MANIFEST": MANIFEST, "env": "TPOT_TYPE=HIVE\nTPOT_ES_RETENTION=7\n"})
        result = self.bash(f"myARCHIVE='{archive}' myDO_CONFIG=1\nfuTMPDIR; fuTOC\nfuDO_CONFIG\n")
        out = result.stdout + result.stderr
        self.assertTrue(any(line.startswith("HINT: ") and "TPOT_ES_RETENTION=7" in line and "waits" in line
                            and "T-Pot left the policy as it was" in line for line in out.splitlines()), out)
        # an archive without a shorter value, or with no key, says nothing
        for archived in ("TPOT_TYPE=HIVE\n", "TPOT_TYPE=HIVE\nTPOT_ES_RETENTION=off\n"):
            with self.subTest(archived=archived):
                archive = make_backup(self.home, {"MANIFEST": MANIFEST, "env": archived})
                result = self.bash(f"myARCHIVE='{archive}' myDO_CONFIG=1\nfuTMPDIR; fuTOC\nfuDO_CONFIG\n")
                self.assertNotIn("waits", result.stdout + result.stderr)

    def test_a_policy_that_deletes_but_not_in_whole_days_goes_back_with_accept(self):
        out = self.elastic("365d", "36h", accept=True)
        self.assertIn("PUT", self.puts(), out)
        self.assertIn("(confirmed with --accept-data-loss)", out)

    def test_a_policy_that_does_not_delete_goes_back_without_asking(self):
        no_action = {"phases": {"hot": {"min_age": "0ms", "actions": {}},
                                "delete": {"min_age": "1d", "actions": {}}}}
        for archived in (None, no_action):
            with self.subTest(archived=archived):
                if os.path.exists(os.path.join(self.home, "puts")):
                    os.remove(os.path.join(self.home, "puts"))
                out = self.elastic("365d", archived)
                self.assertIn("PUT", self.puts(), out)
                self.assertNotIn("WARN", out)

    def test_a_longer_policy_goes_back_without_asking(self):
        out = self.elastic("365d", "400d")
        self.assertIn("PUT", self.puts(), out)
        self.assertNotIn("WARN", out)

    def curl_calls(self):
        try:
            with open(os.path.join(self.home, "curl_calls"), encoding="utf-8") as handle:
                text = handle.read()
            os.remove(os.path.join(self.home, "curl_calls"))
            return text
        except FileNotFoundError:
            return ""

    def kept(self):
        """The kept folders *_elastic of the run: {name: (mode, files)}."""
        backups = os.path.join(self.home, "tpot_backups")
        if not os.path.isdir(backups):
            return {}
        return {name: (stat.S_IMODE(os.stat(os.path.join(backups, name)).st_mode),
                       sorted(os.listdir(os.path.join(backups, name))))
                for name in os.listdir(backups) if name.endswith("_elastic")}

    def test_an_ilm_only_archive_does_not_wait_for_kibana(self):
        """Kibana down and no Kibana objects in the archive: the ILM policy goes by its rules alone."""
        self.write("kibana_down", "")
        for archived, put in (("400d", True), ("30d", False)):
            with self.subTest(archived=archived):
                if os.path.exists(os.path.join(self.home, "puts")):
                    os.remove(os.path.join(self.home, "puts"))
                self.curl_calls()
                out = self.elastic("365d", archived)
                self.assertNotIn("api/status", self.curl_calls(), out)
                self.assertNotIn("Kibana", out.replace("The Kibana objects and the ILM policy", ""))
                self.assertEqual("PUT" in self.puts(), put, out)
                self.assertIn("failed=\n", out)
                if not put:
                    self.assertIn("WARN: The ILM policy of the archive was not put back: not confirmed.", out)
                    self.assertIn("warned=elastic:", out)
        self.write("yes", "")                            # at a terminal it asks, a yes puts it back
        out = self.elastic("365d", "30d", terminal=True)
        self.assertIn("ASKED: --default no", out)
        self.assertIn("PUT", self.puts(), out)

    def test_kibana_down_fails_the_kibana_part_and_the_ilm_part_still_runs(self):
        self.write("kibana_down", "")
        self.write("current_policy.json", json.dumps({"tpot": {"version": 3, "policy": ilm_policy("365d")}}))
        for archived, put in (("400d", True), ("30d", False)):
            with self.subTest(archived=archived):
                if os.path.exists(os.path.join(self.home, "puts")):
                    os.remove(os.path.join(self.home, "puts"))
                archive = make_backup(self.home, {"MANIFEST": MANIFEST, "elastic/kibana_export.ndjson": "{}\n",
                                                  "elastic/ilm_policy_tpot.json":
                                                  json.dumps({"policy": ilm_policy(archived)})})
                result = self.bash(f"myARCHIVE='{archive}' myDO_ELASTIC=1 myKIBANA_TIMEOUT=0\nfuTMPDIR; fuTOC\n"
                                   'fuDO_ELASTIC; echo "rc=$?"\necho "failed=${myFAILED}"\n')
                out = result.stdout + result.stderr
                self.assertIn("ERROR: Kibana does not answer, the Kibana objects are not imported.", out)
                self.assertIn("rc=1", out)
                self.assertIn("failed=elastic\n", out)
                # the hint names the kept file, and that file is there
                post = [line for line in out.splitlines() if "curl -X POST" in line and "_import" in line]
                self.assertTrue(post, out)
                path = re.search(r"file=@(\S+)", post[0]).group(1)
                self.assertTrue(os.path.isfile(path), (path, out))
                self.assertNotIn("ilm_policy_tpot.json", " ".join(post))
                # the ILM part ran: put back, or not confirmed with its own hint on its kept file
                self.assertEqual("PUT" in self.puts(), put, out)
                if not put:
                    manual = [line for line in out.splitlines() if "curl -X PUT" in line]
                    self.assertTrue(manual, out)
                    self.assertTrue(os.path.isfile(re.search(r"-d @(\S+)", manual[0]).group(1)), out)

    def test_elasticsearch_down_fails_the_ilm_part_with_its_own_hint(self):
        self.write("es_down", "")
        out = self.elastic(None, "30d")
        self.assertEqual(self.puts(), "", out)
        self.assertIn("ERROR: Elasticsearch does not answer, the ILM policy is not put back.", out)
        self.assertIn("failed=elastic\n", out)
        lines = out.splitlines()
        put = next(i for i, line in enumerate(lines) if "curl -X PUT" in line)
        self.assertTrue(os.path.isfile(re.search(r"-d @(\S+)", lines[put]).group(1)), out)
        loss = [i for i, line in enumerate(lines) if line.startswith("WARN: ") and "deletes" in line]
        self.assertTrue(loss and loss[0] < put, out)                     # the loss is named before the PUT
        self.assertNotIn("kibana_export", out)
        # a policy that deletes nothing names no loss
        out = self.elastic(None, None)
        self.assertFalse([line for line in out.splitlines() if line.startswith("WARN: ")], out)

    def test_the_kept_folder_is_0700_on_every_path(self):
        script_umask = "umask 022\n"
        self.write("kibana_down", "")                    # the Kibana part fails, its file is kept
        archive = make_backup(self.home, {"MANIFEST": MANIFEST, "elastic/kibana_export.ndjson": "{}\n"})
        self.bash(script_umask + f"myARCHIVE='{archive}' myDO_ELASTIC=1 myKIBANA_TIMEOUT=0 myDATE=1\n"
                  "fuTMPDIR; fuTOC\nfuDO_ELASTIC\n")
        os.remove(os.path.join(self.home, "kibana_down"))
        self.write("current_policy.json", json.dumps({"tpot": {"version": 3, "policy": ilm_policy("365d")}}))
        archive = make_backup(self.home, {"MANIFEST": MANIFEST, "elastic/ilm_policy_tpot.json":
                                          json.dumps({"policy": ilm_policy("30d")})})    # not confirmed
        self.bash(script_umask + f"myARCHIVE='{archive}' myDO_ELASTIC=1 myKIBANA_TIMEOUT=0 myDATE=2\n"
                  "fuTMPDIR; fuTOC\nfuDO_ELASTIC\n")
        self.write("es_down", "")                        # Elasticsearch down, the policy is kept
        self.bash(script_umask + f"myARCHIVE='{archive}' myDO_ELASTIC=1 myKIBANA_TIMEOUT=0 myDATE=3\n"
                  "fuTMPDIR; fuTOC\nfuDO_ELASTIC\n")
        self.assertEqual(self.kept(), {"1_elastic": (0o700, ["kibana_export.ndjson"]),
                                       "2_elastic": (0o700, ["ilm_policy_tpot.json"]),
                                       "3_elastic": (0o700, ["ilm_policy_tpot.json"])})

    def test_a_missing_backups_folder_is_made_0700_as_well(self):
        self.write("kibana_down", "")
        archive = make_backup(self.home, {"MANIFEST": MANIFEST, "elastic/kibana_export.ndjson": "{}\n"})
        moved = os.path.join(self.home, "elsewhere.tar")
        os.rename(archive, moved)                        # the archive lies outside, the folder is not there yet
        shutil.rmtree(os.path.join(self.home, "tpot_backups"), ignore_errors=True)
        self.bash(f"umask 022\nmyARCHIVE='{moved}' myDO_ELASTIC=1 myKIBANA_TIMEOUT=0 myDATE=1\n"
                  "fuTMPDIR; fuTOC\nfuDO_ELASTIC\n")
        backups = os.path.join(self.home, "tpot_backups")
        self.assertEqual(stat.S_IMODE(os.stat(backups).st_mode), 0o700)
        self.assertEqual(self.kept(), {"1_elastic": (0o700, ["kibana_export.ndjson"])})

    def test_a_current_policy_that_cannot_be_read_is_not_called_none(self):
        """A 503 for the policy (the cluster starts): the question says so, and it still asks."""
        self.write("policy_503", "")
        out = self.elastic("365d", "30d")
        self.assertEqual(self.puts(), "", out)
        self.assertIn("the current one could not be read (HTTP 503): putting it back deletes older events", out)
        self.assertNotIn("has none now", out)
        os.remove(os.path.join(self.home, "policy_503"))
        os.remove(os.path.join(self.home, "current_policy.json"))
        out = self.elastic(None, "30d")                  # a 404: there is none
        self.assertIn("Elasticsearch has none now", out)

    def test_the_waits_end_a_stalled_answer(self):
        text = read("restore.sh")
        wait = text[text.index("function fuWAIT_UP ()"):text.index("function fuKEEP_ELASTIC ()")]
        curls = [line for line in wait.splitlines() if "curl " in line]
        self.assertEqual(len(curls), 2, wait)
        self.assertTrue(all("--max-time 10" in line for line in curls), curls)
        body = text[text.index("function fuDO_ELASTIC ()"):text.index("function fuEND ()")]
        checks = [line for line in body.splitlines() if "curl -s -f -o /dev/null" in line]
        self.assertTrue(checks and all("--max-time 10" in line for line in checks), checks)

    def test_a_failed_wait_names_a_log_that_says_what_it_waited_for(self):
        self.write("kibana_down", "")
        self.write("current_policy.json", json.dumps({"tpot": {"version": 3, "policy": ilm_policy("365d")}}))
        archive = make_backup(self.home, {"MANIFEST": MANIFEST, "elastic/kibana_export.ndjson": "{}\n",
                                          "elastic/ilm_policy_tpot.json": json.dumps({"policy": ilm_policy("400d")})})
        result = self.run_script(os.path.join(REPO, "restore.sh"), "-f", archive, "-g", "elastic",
                                 env={"TPOT_KIBANA_TIMEOUT": "0"}, cwd=REPO)
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, out[-600:])
        log = os.path.join(self.home, "tpot_backups", "restore.log")
        self.assertIn(f"the end of {log}", out)
        with open(log, encoding="utf-8") as handle:
            self.assertIn("Kibana on http://127.0.0.1:64296/api/status did not answer within 0s.", handle.read())
        self.assertIn("PUT", self.puts(), out)                          # the ILM part ran all the same
        self.assertIn("Not restored: elastic.", out)

    def test_the_whole_run_ends_with_a_warning(self):
        self.write("current_policy.json", json.dumps({"tpot": {"version": 3, "policy": ilm_policy("365d")}}))
        archive = make_backup(self.home, {"MANIFEST": MANIFEST, "elastic/ilm_policy_tpot.json":
                                          json.dumps({"policy": ilm_policy("30d")})})
        result = self.run_script(os.path.join(REPO, "restore.sh"), "-f", archive, "-g", "elastic",
                                 env={"TPOT_KIBANA_TIMEOUT": "0"}, cwd=REPO)
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out[-600:])
        self.assertEqual(self.puts(), "", out)
        self.assertIn("[WARNING] - The Kibana objects and the ILM policy", out)
        self.assertNotIn("Not restored", out)

    def test_help_names_accept_data_loss(self):
        result = self.run_script(os.path.join(REPO, "restore.sh"), "-h")
        self.assertEqual(result.returncode, 0, result.stderr)
        text = " ".join(result.stdout.split())
        self.assertIn("--accept-data-loss", text)
        self.assertIn("It does not confirm a loss of data", text)
        self.assertIn("--accept-data-loss-file <file>", text)

    def test_a_file_with_a_data_line_only_does_not_put_a_shorter_policy_back(self):
        """The T-Pot Manager confirms the restore of data/ only: the ILM policy is a loss of its own."""
        self.write("confirmed", "@@tpot loss data Restoring data/ replaces the current files.\n")
        out = self.elastic("365d", "30d", extra=f"myACCEPT_FILE='{self.home}/confirmed'\n")
        self.assertEqual(self.puts(), "", out)
        self.assertIn("HINT: --accept-data-loss-file does not confirm it: it names no 'ilm' loss.", out)
        self.assertIn("warned=elastic:", out)

    def test_a_file_with_an_ilm_line_puts_it_back(self):
        self.write("confirmed", "@@tpot loss ilm The ILM policy of the archive keeps the events 30 days.\n")
        out = self.elastic("365d", "30d", extra=f"myACCEPT_FILE='{self.home}/confirmed'\n")
        self.assertIn("PUT", self.puts(), out)
        self.assertIn("(confirmed with --accept-data-loss-file)", out)

    def test_an_unknown_long_option(self):
        result = self.run_script(os.path.join(REPO, "restore.sh"), "--nonsense")
        self.assertEqual(result.returncode, 1)
        self.assertIn("--nonsense", result.stdout + result.stderr)


# tar for restore.sh: what extracts data/* is recorded in HOME/tar_calls and does nothing, the rest
# is the real tar
REAL_TAR = shutil.which("tar") or "/usr/bin/tar"
TAR = f'#!/bin/sh\ncase "$*" in\n  *"data/*"*) echo "tar $*" >> "$HOME/tar_calls"; exit 0 ;;\nesac\nexec {REAL_TAR} "$@"\n'
# du -sh: 12G for any path
DU_SH = "#!/bin/sh\nfor a; do f=\"$a\"; done\nprintf '12G\\t%s\\n' \"$f\"\n"


class RestoreDataTest(RestoreScriptHarness):
    """data/ replaces newer files and the Elasticsearch data: only after a yes at a terminal or with
    --accept-data-loss (-y does not), and into TPOT_DATA_PATH of the .env in effect."""

    def setUp(self):
        super().setUp()
        self.fake("tar", TAR)
        self.fake("du", DU_SH)
        self.archive = make_backup(self.home, {"MANIFEST": MANIFEST, "env": "TPOT_TYPE=HIVE\n",
                                               "data/uuid": "x\n", "data/elk/data/nodes/0": "old\n"})
        self.env("")

    def env(self, data_path):
        with open(os.path.join(self.tpotce, ".env"), "w", encoding="utf-8") as out:
            out.write("TPOT_TYPE=HIVE\n" + (f"TPOT_DATA_PATH={data_path}\n" if data_path else ""))

    def elk(self, root):
        """A current Elasticsearch data folder under root."""
        folder = os.path.join(root, "elk", "data")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "now"), "w", encoding="utf-8") as out:
            out.write("events since the backup\n")
        return folder

    def tar_calls(self):
        try:
            with open(os.path.join(self.home, "tar_calls"), encoding="utf-8") as handle:
                return handle.read()
        except FileNotFoundError:
            return ""

    def restore(self, *args):
        result = self.run_script(os.path.join(REPO, "restore.sh"), "-f", self.archive, *args)
        return result.returncode, result.stdout + result.stderr

    def test_without_confirmation_nothing_of_data_is_touched(self):
        for args in (("-g", "data,config"), ("-y",)):                       # -y does not confirm it
            with self.subTest(args=args):
                folder = self.elk(os.path.join(self.tpotce, "data"))
                code, out = self.restore(*args)
                self.assertEqual(code, 0, out[-800:])
                self.assertEqual(self.tar_calls(), "", out)
                self.assertTrue(os.path.exists(os.path.join(folder, "now")), out)
                self.assertIn("data/ was not restored: not confirmed", out)
                self.assertIn("--accept-data-loss confirms it, -y does not", out)
                self.assertIn("[WARNING] - The files from data/", out)            # a warning in the summary
                self.assertNotIn("Not restored", out)
                self.assertIn("[OK] - The configuration", out)                    # the other groups go on

    def calls(self):
        try:
            with open(os.path.join(self.home, "calls"), encoding="utf-8") as handle:
                return handle.read()
        except FileNotFoundError:
            return ""

    def test_a_refused_data_restore_does_not_stop_tpot(self):
        """Refused for sure (no terminal, no flag, no line of the file): T-Pot is not stopped for data/."""
        for args in (("-g", "data"), ("-y", "-g", "data")):
            with self.subTest(args=args):
                folder = self.elk(os.path.join(self.tpotce, "data"))
                if os.path.exists(os.path.join(self.home, "calls")):
                    os.remove(os.path.join(self.home, "calls"))
                code, out = self.restore(*args)
                self.assertEqual(code, 0, out[-800:])
                self.assertNotIn("systemctl stop", self.calls(), out)
                self.assertNotIn("Stopping T-Pot", out)
                self.assertEqual(self.tar_calls(), "", out)
                self.assertTrue(os.path.exists(os.path.join(folder, "now")), out)
                self.assertIn("data/ was not restored: not confirmed", out)
                self.assertIn("--accept-data-loss confirms it, -y does not", out)
                self.assertIn("[WARNING] - The files from data/", out)
                self.assertIn("not restored: not confirmed", out)
                self.assertNotIn("Not restored", out)                      # a warning, no failure
                self.assertNotIn("Start T-Pot with", out)                  # it never stopped

    def test_a_refused_data_restore_with_config_stops_for_the_config_only(self):
        self.elk(os.path.join(self.tpotce, "data"))
        code, out = self.restore("-g", "data,config")
        self.assertEqual(code, 0, out[-800:])
        self.assertEqual(self.calls().count("systemctl stop"), 1, out)   # the configuration needs it, data/ does not
        self.assertEqual(self.tar_calls(), "", out)
        self.assertIn("[WARNING] - The files from data/", out)
        self.assertIn("[OK] - The configuration", out)
        self.assertLess(out.index("data/ was not restored: not confirmed"), out.index("Stopping T-Pot"))

    def test_a_confirmed_data_restore_stops_tpot(self):
        self.write("confirmed", "@@tpot loss data Restoring data/ replaces the current files.\n")
        for args in (("--accept-data-loss",), ("--accept-data-loss-file", os.path.join(self.home, "confirmed"))):
            with self.subTest(args=args):
                if os.path.exists(os.path.join(self.home, "calls")):
                    os.remove(os.path.join(self.home, "calls"))
                code, out = self.restore("-g", "data", *args)
                self.assertEqual(code, 0, out[-800:])
                self.assertIn("systemctl stop", self.calls(), out)
                self.assertIn("tar xf", self.tar_calls())

    def test_a_file_without_a_data_line_does_not_confirm_it_early(self):
        self.write("confirmed", "@@tpot loss ilm The ILM policy.\n")
        self.elk(os.path.join(self.tpotce, "data"))
        code, out = self.restore("-g", "data", "--accept-data-loss-file", os.path.join(self.home, "confirmed"))
        self.assertEqual(code, 0, out[-800:])
        self.assertNotIn("systemctl stop", self.calls(), out)
        self.assertEqual(self.tar_calls(), "", out)
        self.assertIn("--accept-data-loss-file does not confirm it: it names no 'data' loss.", out)

    def test_the_question_names_what_goes(self):
        self.elk(os.path.join(self.tpotce, "data"))
        _code, out = self.restore("-g", "data", "--accept-data-loss")
        text = " ".join(out.split())
        self.assertIn(f"files under {self.tpotce}/data are replaced by those of the archive", text)
        self.assertIn("newer files and log lines since the backup are lost", text)
        self.assertIn("The current Elasticsearch data (12G) is deleted and replaced, events since "
                      "2026-10-05 10:10 are lost", text)

    def test_without_elasticsearch_data_in_the_archive_it_is_not_named(self):
        self.archive = make_backup(self.home, {"MANIFEST": MANIFEST, "data/uuid": "x\n"},
                                   name="20261006101010_tpot_backup.tar")
        _code, out = self.restore("-g", "data", "--accept-data-loss")
        self.assertIn("files under", out)
        self.assertNotIn("Elasticsearch data", out)

    def test_accept_restores_into_the_default_path_as_before(self):
        folder = self.elk(os.path.join(self.tpotce, "data"))
        code, out = self.restore("-g", "data", "--accept-data-loss")
        self.assertEqual(code, 0, out[-800:])
        self.assertIn("(confirmed with --accept-data-loss)", out)
        self.assertIn(f"tar xf {self.archive} -C {self.tpotce} -p --numeric-owner --wildcards data/*",
                      self.tar_calls())
        self.assertNotIn("--strip-components", self.tar_calls())
        self.assertFalse(os.path.exists(folder), out)
        self.assertIn("[OK] - The files from data/", out)

    def test_accept_restores_into_a_custom_data_path(self):
        custom = os.path.join(self.home, "custom")
        os.makedirs(custom)
        default, mine = self.elk(os.path.join(self.tpotce, "data")), self.elk(custom)
        self.env(custom + "/")
        code, out = self.restore("-g", "data", "--accept-data-loss")
        self.assertEqual(code, 0, out[-800:])
        self.assertIn(f"tar xf {self.archive} -C {custom} --strip-components=1 -p --numeric-owner --wildcards "
                      "data/*", self.tar_calls())
        self.assertFalse(os.path.exists(mine), out)
        self.assertTrue(os.path.exists(default), out)
        self.assertIn(f"files under {custom} are replaced", " ".join(out.split()))

    def test_a_relative_data_path_is_under_the_checkout(self):
        self.env("./mydata")
        os.makedirs(os.path.join(self.tpotce, "mydata"))
        code, out = self.restore("-g", "data", "--accept-data-loss")
        self.assertEqual(code, 0, out[-800:])
        self.assertIn(f"-C {self.tpotce}/mydata --strip-components=1", self.tar_calls())

    def test_the_data_path_of_the_restored_env_counts(self):
        custom = os.path.join(self.home, "fromarchive")
        os.makedirs(custom)
        self.archive = make_backup(self.home, {"MANIFEST": MANIFEST, "env": f"TPOT_DATA_PATH={custom}\n",
                                               "data/uuid": "x\n"}, name="20261007101010_tpot_backup.tar")
        code, out = self.restore("-g", "config,data", "--accept-data-loss")
        self.assertEqual(code, 0, out[-800:])
        self.assertIn(f"-C {custom} --strip-components=1", self.tar_calls())

    def test_the_file_confirms_data_only_with_a_data_line(self):
        confirmed = os.path.join(self.home, "confirmed")
        for line, restored in (("@@tpot loss ilm The ILM policy goes back.", False),
                               ("@@tpot loss data Restoring data/ replaces the current files.", True)):
            with self.subTest(line=line):
                with open(confirmed, "w", encoding="utf-8") as out:
                    out.write(line + "\n")
                folder = self.elk(os.path.join(self.tpotce, "data"))
                code, out = self.restore("-g", "data", "--accept-data-loss-file", confirmed)
                self.assertEqual(code, 0, out[-800:])
                self.assertEqual("data/*" in self.tar_calls(), restored, out)
                self.assertEqual(os.path.exists(folder), not restored, out)

    def test_the_file_must_be_readable(self):
        code, out = self.restore("-g", "data", "--accept-data-loss-file", os.path.join(self.home, "none"))
        self.assertEqual(code, 1, out)
        self.assertEqual(self.tar_calls(), "")

    def test_a_yes_at_a_terminal_confirms(self):
        folder = self.elk(os.path.join(self.tpotce, "data"))
        script = f"myARCHIVE='{self.archive}' myDO_DATA=1\nfuTMPDIR; fuTOC\nfuDO_DATA\n"
        out = self.bash(script, terminal=True).stdout
        self.assertIn("ASKED: --default no", out)
        self.assertEqual(self.tar_calls(), "")
        self.assertTrue(os.path.exists(folder))
        self.write("yes", "")
        out = self.bash(script, terminal=True).stdout
        self.assertIn("data/*", self.tar_calls(), out)
        self.assertFalse(os.path.exists(folder))

    def test_at_a_terminal_the_early_check_leaves_data_to_the_question(self):
        """restore.sh at a terminal (a pty, plain text): fuDATA_REFUSED does not drop data/ before the
        stop, the question comes in fuDO_DATA; no restores nothing of it, yes restores it. -y does not
        answer it."""
        environment = dict(os.environ, HOME=self.home, PATH=f"{self.bin}:{os.environ['PATH']}", TPOT_GUM="off",
                           TPOT_LOGO_SHOWN="1")
        environment.pop("TPOT_MARKS", None)
        for answer, restored in ((b"n\r", False), (b"y\r", True)):
            with self.subTest(answer=answer):
                folder = self.elk(os.path.join(self.tpotce, "data"))
                for name in ("calls", "tar_calls"):
                    if os.path.exists(os.path.join(self.home, name)):
                        os.remove(os.path.join(self.home, name))
                code, out = at_terminal(["bash", os.path.join(REPO, "restore.sh"), "-f", self.archive, "-y",
                                         "-g", "data"], environment, self.home,
                                        [("Accept this loss of data? (y/N)", answer)])
                self.assertEqual(code, 0, out[-1500:])
                self.assertNotIn("Not confirmed: no terminal", out)
                self.assertNotIn("data/ was not restored: not confirmed. Restoring it replaces", out)  # the early one
                self.assertIn("### Accept this loss of data? (y/N) ", out)
                self.assertIn("The current Elasticsearch data (12G) is deleted and replaced", out)
                self.assertIn("systemctl stop", self.calls(), out)             # data/ was still chosen
                self.assertLess(out.index("Stopping T-Pot"), out.index("Accept this loss of data?"))
                if restored:
                    self.assertIn("tar xf", self.tar_calls(), out)
                    self.assertFalse(os.path.exists(os.path.join(folder, "now")))
                    self.assertNotIn("not restored", out)
                else:
                    self.assertEqual(self.tar_calls(), "", out)
                    self.assertTrue(os.path.exists(os.path.join(folder, "now")))
                    self.assertIn("data/ was not restored: not confirmed.", out)
                    self.assertIn("not restored: not confirmed, see above", out)


@unittest.skipUnless(shutil.which("bash"), "no bash")
class LossConfirmCommentTest(unittest.TestCase):
    """fuLOSS_CONFIRM of update.sh and restore.sh decide the same way; the comments name what differs: the
    signature and the hint for a file that does not confirm the loss, each in the terms of its file lines."""

    @staticmethod
    def block(path):
        text = read(path)
        end = text.index("function fuLOSS_CONFIRM () {")
        comment = text[text.rindex("\n\n", 0, end):end]
        return comment, text[end:text.index("\n}\n", end)]

    def test_the_comments_name_the_differences(self):
        for path, hint in (("update.sh", "it was not shown like this before the run."),
                           ("restore.sh", "it names no '$1' loss.")):
            with self.subTest(path=path):
                comment, body = self.block(path)
                flat = " ".join(comment.replace("#", " ").split())
                self.assertNotIn("with the same texts", flat)
                self.assertIn("The hints for a file that does not confirm the loss differ", flat)
                self.assertIn("what they decide is the same", flat)
                self.assertIn(f'fuUI_HINT "--accept-data-loss-file does not confirm it: {hint}"', body)


class LossConfirmTerminalTest(unittest.TestCase):
    """fuLOSS_CONFIRM of update.sh and of restore.sh at a real terminal (a pty), with installer/lib/ui.sh
    and with the plain fallback block of the script (TPOT_GUM=off): enter is no, y and yes are yes, n is
    no. The question is asked, the hint for a run without a terminal is not given."""

    # the arguments after the text: update.sh takes the names of what goes, restore.sh the question
    CALLS = {"update.sh": 'fuLOSS_CONFIRM backups "Deleting 1 backup makes room." a_tpot_backup.tar',
             "restore.sh": 'fuLOSS_CONFIRM data "Restoring data/ replaces the current files."'}
    ANSWERS = ((b"\r", 1), (b"y\r", 0), (b"yes\r", 0), (b"n\r", 1))

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="tpot-loss-")
        self.addCleanup(shutil.rmtree, self.home)

    def sources(self, script):
        """installer/lib/ui.sh and the plain fallback block of the script, as files to source."""
        text = read(script)
        start, end = text.index("# >>> plain fallback"), text.index("# <<< plain fallback")
        fallback = os.path.join(self.home, f"fallback-{script}")
        with open(fallback, "w", encoding="utf-8") as out:
            out.write(text[start:end])
        return (os.path.join(REPO, "installer", "lib", "ui.sh"), fallback)

    def test_the_helper_reads_the_last_output(self):
        """at_terminal itself: a child that prints and ends at once loses nothing of it (20 runs, and once
        with the race forced: the output comes after the last wait, the exit before the poll); a child past
        the deadline is killed, none stays."""
        import select
        from unittest import mock
        for run in range(20):
            with self.subTest(run=run):
                self.assertEqual(at_terminal(["printf", "x\n"], dict(os.environ), self.home), (0, "x\n"))
        # the race made sure: the first wait of the helper times out, the child writes and ends meanwhile.
        # Linux has that window; macOS keeps the child until its output is read (a pty drains on close),
        # so from that idle wait on every poll says "gone" here as it would there, before it every poll
        # says what is true: however often and in which order the helper asks
        real_select, real_poll = select.select, subprocess.Popen.poll
        state = {"idle": False, "polls_after": 0}

        def late(*args):
            if not state["idle"]:
                state["idle"] = True
                time.sleep(0.3)
                return [], [], []
            return real_select(*args)

        def gone(proc):
            code = real_poll(proc)
            if not state["idle"]:
                return code
            state["polls_after"] += 1
            return 0 if code is None else code

        with mock.patch("select.select", side_effect=late), \
                mock.patch.object(subprocess.Popen, "poll", autospec=True, side_effect=gone):
            self.assertEqual(at_terminal(["printf", "x\n"], dict(os.environ), self.home), (0, "x\n"))
        self.assertTrue(state["idle"])
        self.assertGreater(state["polls_after"], 0)
        started = time.time()
        code, out = at_terminal(["bash", "-c", 'echo "before"; exec sleep 30'], dict(os.environ), self.home,
                                timeout=1)
        self.assertLess(time.time() - started, 15)
        self.assertNotEqual(code, 0)
        self.assertEqual(out, "before\n")

    def test_the_helper_ends_while_a_child_of_the_child_keeps_writing(self):
        """The child is gone, but what it started keeps the pty open and writes on: the helper reads it until
        its deadline (+2 s) only, then returns what it has."""
        import signal
        import threading
        pidfile = os.path.join(self.home, "writer.pid")
        script = (f'(sleep 0.15; while :; do echo more; sleep 0.05; done) & echo $! > "{pidfile}"\n'
                  'echo first\nexit 0\n')

        def stop_writer():
            try:
                with open(pidfile, encoding="utf-8") as handle:
                    os.kill(int(handle.read().strip()), signal.SIGKILL)
            except (OSError, ValueError):
                pass

        self.addCleanup(stop_writer)
        result = []
        started = time.time()
        reader = threading.Thread(target=lambda: result.append(at_terminal(["bash", "-c", script], dict(os.environ),
                                                                           self.home, timeout=1)), daemon=True)
        reader.start()
        reader.join(20)
        self.assertFalse(reader.is_alive(), "the helper kept reading past its deadline")
        self.assertLess(time.time() - started, 8)
        code, out = result[0]
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("first\n"), out)

    def test_the_answer_at_a_terminal(self):
        environment = dict(os.environ, HOME=self.home, TPOT_GUM="off")
        for name in ("TPOT_MARKS", "NO_COLOR"):
            environment.pop(name, None)
        for script, call in self.CALLS.items():
            for source in self.sources(script):
                for keys, rc in self.ANSWERS:
                    with self.subTest(script=script, source=os.path.basename(source), keys=keys):
                        text = (f'source "{source}"\nfuUI_INIT\nmyACCEPT_LOSS="" myACCEPT_FILE=""\n'
                                f"eval \"$(sed -n '/^function fuLOSS_IN_FILE ()/,/^}}/p; "
                                f"/^function fuLOSS_CONFIRM ()/,/^}}/p' {script})\"\n"
                                f'{call}; echo "rc=$?"\n')
                        code, out = at_terminal(["bash", "-c", text], environment, REPO,
                                                [("Accept this loss of data? (y/N) ", keys)], timeout=30)
                        self.assertEqual(code, 0, out)
                        self.assertIn("Accept this loss of data? (y/N) ", out)
                        self.assertNotIn("Not confirmed: no terminal", out)
                        self.assertNotIn("confirmed with", out)
                        self.assertTrue(out.rstrip().endswith(f"rc={rc}"), repr(out[-300:]))


class FallbackTest(Harness):
    """genuser.sh and deploy.sh without the Python packages of tpot: the old steps, the new look."""

    def setUp(self):
        super().setUp()
        tpotce = os.path.join(self.home, "tpotce")
        os.makedirs(os.path.join(tpotce, "installer", "lib"))
        shutil.copy(os.path.join(REPO, "installer", "lib", "ui.sh"), os.path.join(tpotce, "installer", "lib"))
        with open(os.path.join(tpotce, ".env"), "w", encoding="utf-8") as out:
            out.write("TPOT_TYPE=HIVE\nTPOT_REPO=ghcr.io/telekom-security\nTPOT_VERSION=24.04.2\n")

    def stub(self, name, text):
        path = os.path.join(self.bin, name)
        with open(path, "w", encoding="utf-8") as out:
            out.write(text)
        os.chmod(path, 0o755)

    def without_ui_sh(self):
        os.remove(os.path.join(self.home, "tpotce", "installer", "lib", "ui.sh"))

    def test_genuser_fallback(self):
        result = self.run_script(os.path.join(REPO, "genuser.sh"))
        self.assertIn("### [WARNING] - tpot is not available, using the tpotinit container.", result.stdout)
        with open(os.path.join(self.home, "calls"), encoding="utf-8") as handle:
            self.assertIn("tpotinit:24.04.2", handle.read())
        self.assertEqual(result.returncode, 0)
        summary = result.stdout[result.stdout.index("### T-Pot web user"):]
        self.assertIn("### [NEXT] - Restart T-Pot", summary)
        self.assertIn("sudo systemctl restart tpot", summary)

    def test_genuser_fallback_without_ui_sh(self):
        self.without_ui_sh()
        result = self.run_script(os.path.join(REPO, "genuser.sh"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("### [WARNING] - tpot is not available, using the tpotinit container.", result.stdout)
        self.assertIn("### [NEXT] - Restart T-Pot", result.stdout)

    def test_genuser_fallback_failure_ends_with_a_summary(self):
        self.stub("docker", "#!/bin/sh\nexit 125\n")
        result = self.run_script(os.path.join(REPO, "genuser.sh"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("### [FAILED] - The tpotinit container did not add a web user", result.stdout)

    def deploy(self):
        """deploy.sh without tpot, its answers on stdin, a playbook that fails: nothing changes."""
        awk = shutil.which("awk") or "/usr/bin/awk"
        self.stub("awk", "#!/bin/sh\nn=$#\nwhile [ \"$n\" -gt 0 ]; do\n  a=$1; shift; n=$((n - 1))\n"
                         "  [ \"$a\" = /etc/os-release ] && a=\"$HOME/os-release\"\n  set -- \"$@\" \"$a\"\ndone\n"
                         f"exec {awk} \"$@\"\n")
        with open(os.path.join(self.home, "os-release"), "w", encoding="utf-8") as out:
            out.write('NAME="Debian GNU/Linux"\nVERSION_ID="13"\n')
        self.stub("shuf", "#!/bin/sh\necho honey\n")
        self.stub("htpasswd", "#!/bin/sh\nread -r pw\necho \"$4:\\$2y\\$05\\$hash\"\n")
        self.stub("ansible-playbook", "#!/bin/sh\necho \"ansible-playbook $*\" >> \"$HOME/calls\"\nexit 2\n")
        environment = dict(os.environ, HOME=self.home, PATH=f"{self.bin}:{os.environ['PATH']}", TPOT_GUM="off")
        environment.pop("TPOT_MARKS", None)
        return subprocess.run(["bash", os.path.join(REPO, "deploy.sh")], input="y\nadmin\n10.0.0.2\ny\n10.0.0.1\n",
                              capture_output=True, universal_newlines=True, env=environment, cwd=self.home,
                              timeout=60)

    def test_deploy_fallback_ends_with_a_summary(self):
        for ui_sh in (True, False):
            with self.subTest(ui_sh=ui_sh):
                if not ui_sh:
                    self.without_ui_sh()
                result = self.deploy()
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("### T-Pot Sensor deploy", result.stdout)
                summary = result.stdout[result.stdout.index("### The SENSOR is not deployed"):]
                self.assertIn("### [FAILED] - The deployment playbook failed", summary)
                self.assertIn("deploy_sensor.log", summary)
                with open(os.path.join(self.home, "tpotce", ".env"), encoding="utf-8") as handle:
                    self.assertNotIn("LS_WEB_USER", handle.read())

    def test_deploy_fallback_speaks_like_ui_sh(self):
        text = read("deploy.sh")
        text = text.replace(text[text.index("# >>> plain fallback"):text.index("# <<< plain fallback")], "")
        self.assertEqual(re.findall(r'.*(?:echo "#|read -r?p).*', text), [])
        self.assertIn("fuUI_BANNER", text)
        self.assertIn("fuUI_SUMMARY", text)

    def test_deploy_fallback_keeps_the_password_out_of_argv(self):
        self.assertNotRegex(read("deploy.sh"), r"htpasswd [^|]*-b")


class MytopipsTest(Harness):

    def test_is_tpot_attackers(self):
        tpotce = os.path.join(self.home, "tpotce")
        os.makedirs(tpotce)
        fake = os.path.join(tpotce, "tpot")
        with open(fake, "w", encoding="utf-8") as out:
            out.write('#!/bin/sh\necho "tpot $*" >> "$HOME/calls"\n')
        os.chmod(fake, 0o755)
        self.run_script(os.path.join(REPO, "docker", "tpotinit", "dist", "bin", "mytopips.sh"))
        with open(os.path.join(self.home, "calls"), encoding="utf-8") as handle:
            self.assertIn("tpot attackers --count 100 --plain", handle.read())

    def test_backup_es_folders_is_gone(self):
        self.assertFalse(os.path.exists(os.path.join(REPO, "docker", "tpotinit", "dist", "bin",
                                                     "backup_es_folders.sh")))


class BackupInfoTest(unittest.TestCase):

    def test_info_of_an_archive(self):
        from tpotctl import ops
        home = tempfile.mkdtemp(prefix="tpot-backup-info-")
        self.addCleanup(shutil.rmtree, home)
        path = make_backup(home, {"MANIFEST": MANIFEST, "env": "A=1\n", "rollback.txt": "abc\n",
                                  "data/uuid": "x\n", "untracked/notes.txt": "y\n"},
                           name="20261005101010_tpot_backup_full.tar")
        info = ops.backup_info(path)
        self.assertEqual(info.name, "20261005101010_tpot_backup_full.tar")
        self.assertEqual(info.kind, "full")
        self.assertEqual(info.groups, ["git", "config", "untracked", "data"])
        self.assertIn("version: 24.04.2", info.manifest)
        self.assertFalse(info.es_data)
        self.assertEqual(info.es_size, 0)
        path = make_backup(home, {"MANIFEST": MANIFEST, "data/elk/data/nodes/0": "x\n",
                                  "data/elk/data/nodes/1": "abcd\n", "data/elk/database": "not it\n",
                                  "data/uuid": "y\n"},
                           name="20261006101010_tpot_backup_full.tar")
        info = ops.backup_info(path)
        self.assertTrue(info.es_data)                           # the restore of data/ replaces it
        self.assertEqual(info.es_size, 7)                       # the files under data/elk/data/ only

    def test_a_hard_link_to_a_file_outside_counts_with_that_file(self):
        """tar stores the second name of a file as a link to the first one: a file under data/elk/data/ whose
        first name is outside counts with the size of that member, once; one whose first name is inside counts
        once (as that file); a link to a member the archive does not hold counts nothing."""
        from tpotctl import ops
        home = tempfile.mkdtemp(prefix="tpot-backup-info-")
        self.addCleanup(shutil.rmtree, home)
        stage = os.path.join(home, "stage")
        os.makedirs(os.path.join(stage, "data", "elk", "data"))
        for name, text in (("data/outside", "0123456789"), ("data/elk/data/inside", "abc")):
            with open(os.path.join(stage, name), "w", encoding="utf-8") as out:
                out.write(text)
        os.link(os.path.join(stage, "data/outside"), os.path.join(stage, "data/elk/data/a"))
        os.link(os.path.join(stage, "data/outside"), os.path.join(stage, "data/elk/data/b"))
        os.link(os.path.join(stage, "data/elk/data/inside"), os.path.join(stage, "data/elk/data/c"))
        path = os.path.join(home, "20261007101010_tpot_backup_full.tar")
        with tarfile.open(path, "w") as archive:
            for name in ("data/outside", "data/elk/data/inside", "data/elk/data/a", "data/elk/data/b",
                         "data/elk/data/c"):
                archive.add(os.path.join(stage, name), arcname=name)
            missing = tarfile.TarInfo("data/elk/data/d")
            missing.type, missing.linkname = tarfile.LNKTYPE, "data/gone"
            archive.addfile(missing)
        with tarfile.open(path) as archive:
            kinds = {entry.name: entry.islnk() for entry in archive.getmembers()}
        self.assertEqual([name for name, link in kinds.items() if link],
                         ["data/elk/data/a", "data/elk/data/b", "data/elk/data/c", "data/elk/data/d"])
        self.assertEqual(ops.backup_info(path).es_size, 10 + 3)

    def test_truncated_compressed_archive(self):
        """tarfile raises EOFError for it, the restore page must show it, not end tpot."""
        import gzip
        from tpotctl import ops
        home = tempfile.mkdtemp(prefix="tpot-backup-info-")
        self.addCleanup(shutil.rmtree, home)
        whole = make_backup(home, {"MANIFEST": MANIFEST, "env": "A=1\n" * 2000})
        with open(whole, "rb") as handle:
            packed = gzip.compress(handle.read())
        path = os.path.join(home, "cut_tpot_backup.tar")
        with open(path, "wb") as out:
            out.write(packed[:len(packed) // 2])
        info = ops.backup_info(path)
        self.assertEqual(info.groups, [])
        self.assertTrue(info.problem)

    def test_broken_archive(self):
        from tpotctl import ops
        home = tempfile.mkdtemp(prefix="tpot-backup-info-")
        self.addCleanup(shutil.rmtree, home)
        path = os.path.join(home, "broken_tpot_backup.tar")
        with open(path, "w") as out:
            out.write("not a tar")
        info = ops.backup_info(path)
        self.assertEqual(info.groups, [])
        self.assertTrue(info.problem)



class BuilderTest(Harness):
    """docker/_builder/: a tool for building releases, not part of the T-Pot Manager, in the look of
    the T-Pot scripts all the same. The options, the menu and the runs: test_scripts_builder.py."""

    SCRIPTS = ("docker/_builder/builder.sh",)

    def setUp(self):
        super().setUp()
        # a Linux host whatever runs the tests (the builder runs on Linux only), its settings elsewhere
        path = os.path.join(self.bin, "uname")
        with open(path, "w", encoding="utf-8") as out:
            out.write('#!/bin/sh\ncase "$1" in -s) echo Linux ;; *) echo x86_64 ;; esac\n')
        os.chmod(path, 0o755)
        # the settings and the sudo of the one who runs the tests stay out (as in test_scripts_builder.py)
        self.settings = {key: "" for key in ("TPOT_VERSION", "TPOT_DOCKER_REPO", "TPOT_GHCR_REPO", "TPOT_BUILDER_ARCH",
                                             "TPOT_BUILDER_JOBS", "TPOT_BUILDER_LIMIT", "TPOT_BUILDER_LOG_DIR",
                                             "TPOT_BUILDER_TESTS_DIR", "TPOT_BINFMT_DIR", "SUDO_UID", "SUDO_GID",
                                             "SUDO_USER", "myUI_VERSION", "DOCKER_HOST", "XDG_RUNTIME_DIR")}
        self.settings["TPOT_BUILDER_ENV_LOCAL"] = os.path.join(self.home, "env.local")

    def test_builder_scripts_speak_like_ui_sh(self):
        text = read(self.SCRIPTS[0])
        self.assertTrue("installer/lib/ui.sh" in text)
        rest = text.replace(text[text.index("# >>> plain fallback"):text.index("# <<< plain fallback")], "")
        self.assertEqual(re.findall(r'.*echo -?[ne]* ?"#.*', rest), [])
        for raw in ("$RED", "$GREEN", "$BLUE", "${RED}", "${GREEN}", "${BLUE}", "[OK]", "[FAIL]",
                    "Removiong", "Remeber", "read -p", "read -rp"):
            self.assertFalse(raw in rest, raw)
        self.assertTrue("fuUI_BANNER" in rest)

    def test_the_setup_is_part_of_builder_sh(self):
        # setup_builder.sh is gone: builder.sh --setup / --uninstall, no script of its own
        self.assertFalse(os.path.exists(os.path.join(REPO, "docker", "_builder", "setup_builder.sh")))
        text = read(self.SCRIPTS[0])
        self.assertTrue("--setup) fuACTION setup" in text and "--uninstall) fuACTION uninstall" in text)

    def test_builder_is_not_in_the_manager(self):
        folder = os.path.join(REPO, "tpotctl")
        for root, _dirs, files in os.walk(folder):
            if os.sep + "tests" in root:
                continue
            for name in files:
                # release.py is a release tool like the builder, not the Manager (test_release checks it):
                # it names the builder's .env as a place of the version
                if name.endswith(".py") and not (root == folder and name == "release.py"):
                    with open(os.path.join(root, name), encoding="utf-8") as handle:
                        self.assertFalse("_builder" in handle.read(), name)
        readme = read("README.md")
        manager = readme[readme.index("## The T-Pot Manager (tpot)"):]
        manager = manager[:manager.index("\n## ", 1)]
        self.assertFalse("builder" in manager.lower())

    def test_help_without_root(self):
        result = self.run_script(os.path.join(REPO, self.SCRIPTS[0]), "-h")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue("T-Pot Image Builder" in result.stdout, result.stdout)
        self.assertTrue("Usage:" in result.stdout and "Options:" in result.stdout, result.stdout)
        self.assertTrue("-p" in result.stdout and "-n" in result.stdout, result.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.home, "calls")))

    @unittest.skipIf(os.geteuid() == 0, "runs as root")
    def test_without_root_docker_decides(self):
        """Neither root nor the docker group is no reason to stop (rootless Docker): a docker that does
        not answer is, rc 3 (the environment) with what to do, before anything is built."""
        path = os.path.join(self.bin, "id")
        with open(path, "w", encoding="utf-8") as out:
            out.write('#!/bin/sh\ncase "$1" in -u|-g) echo 1000 ;; -un|-gn) echo tester ;; *) echo staff ;; esac\n')
        os.chmod(path, 0o755)
        path = os.path.join(self.bin, "docker")
        with open(path, "w", encoding="utf-8") as out:
            out.write('#!/bin/sh\necho "docker $*" >> "$HOME/calls"\n[ "$1" = info ] && exit 1\nexit 0\n')
        os.chmod(path, 0o755)
        result = self.run_script(os.path.join(REPO, self.SCRIPTS[0]),
                                 env=dict(self.settings, TPOT_BUILDER_LOG_DIR=os.path.join(self.home, "log")))
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertTrue("### [ERROR] - Docker does not answer" in result.stderr, result.stderr)
        for hint in ("sudo", "usermod -aG docker tester", "rootless Docker"):
            self.assertTrue(hint in result.stderr, hint)
        # only docker info asks the daemon (docker context reads the files of this user)
        with open(os.path.join(self.home, "calls"), encoding="utf-8") as handle:
            self.assertEqual([line for line in handle.read().splitlines() if not line.startswith("docker context ")],
                             ["docker info"])

    def test_a_build_run_reports_each_image(self):
        stubs = {
            "ip": "#!/bin/sh\necho 'default via 10.0.0.1 dev eth0'\n",
            "tc": "#!/bin/sh\nexit 0\n",
            "docker": "#!/bin/sh\necho \"docker $*\" >> \"$HOME/calls\"\n"
                      "case \"$*\" in\n"
                      "  *'buildx inspect'*) echo 'Platforms: linux/amd64, linux/arm64' ;;\n"
                      "  *'config --services'*) printf 'cowrie\\nbroken\\n' ;;\n"
                      "  *'build broken'*) echo 'no such file' ; exit 1 ;;\n"
                      "esac\nexit 0\n",
        }
        for name, text in stubs.items():
            path = os.path.join(self.bin, name)
            with open(path, "w", encoding="utf-8") as out:
                out.write(text)
            os.chmod(path, 0o755)
        work = os.path.join(self.home, "work")
        os.makedirs(work)
        log = os.path.join(work, "log")
        # no options, no terminal: every image, as before; the logs go to TPOT_BUILDER_LOG_DIR
        result = self.run_script(os.path.join(REPO, self.SCRIPTS[0]), cwd=work,
                                 env=dict(self.settings, TPOT_BUILDER_LOG_DIR=log,
                                          TPOT_BINFMT_DIR=os.path.join(self.home, "no-binfmt")))
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, out)
        self.assertTrue("### [OK] - Image cowrie" in out, out)
        self.assertTrue(f"### [ERROR] - Image broken, see {log}/broken.log" in out, out)
        self.assertTrue("### [OK] - Checking the buildx builder 'mybuilder'" in out, out)
        self.assertTrue("Remember to push the images with -p." in out, out)
        with open(os.path.join(log, "broken.log"), encoding="utf-8") as handle:
            self.assertTrue("no such file" in handle.read())


class MigrateRetentionTest(unittest.TestCase):
    """update.sh fuMIGRATE_RETENTION: the retention of the existing ILM policy goes into .env once."""

    def run_migration(self, old_env, policy, read_only=False):
        with tempfile.TemporaryDirectory() as tmp:
            env, old, export = (os.path.join(tmp, n) for n in (".env", "old.env", "ilm_policy_tpot.json"))
            with open(env, "w") as h:
                h.write("TPOT_PERSISTENCE_CYCLES=30\nTPOT_ES_RETENTION=30\nTPOT_TYPE=HIVE\n")
            if read_only:
                os.chmod(env, 0o444)
            if old_env is not None:                                    # None: the archive has no .env
                with open(old, "w") as h:
                    h.write(old_env)
            if isinstance(policy, str):
                with open(export, "w") as h:
                    h.write(policy)
            elif policy is not None:
                with open(export, "w") as h:
                    json.dump({"policy": policy}, h)
            script = textwrap.dedent(f"""
                fuUI_OK() {{ echo "OK: $*"; }}; fuUI_WARN() {{ echo "WARN: $*"; }}; fuUI_HINT() {{ echo "HINT: $*"; }}
                eval "$(sed -n '/^function fuENV_VALUE ()/,/^}}/p; /^function fuMIGRATE_RETENTION ()/,/^}}/p' update.sh)"
                fuMIGRATE_RETENTION '{env}' '{old}' '{export}'
            """)
            out = subprocess.run(["bash", "-c", script], cwd=REPO, capture_output=True, text=True).stdout
            with open(env) as h:
                return h.read(), out

    def policy(self, min_age):
        return {"phases": {"hot": {"min_age": "0ms", "actions": {}},
                           "delete": {"min_age": min_age, "actions": {"delete": {}}}}}

    def test_takes_the_days_of_the_policy(self):
        env, out = self.run_migration("TPOT_PERSISTENCE_CYCLES=30\n", self.policy("365d"))
        self.assertIn("TPOT_ES_RETENTION=365\n", env)
        self.assertIn("365", out)

    def test_no_delete_phase_is_off(self):
        env, _ = self.run_migration("TPOT_TYPE=HIVE\n", {"phases": {"hot": {"min_age": "0ms", "actions": {}}}})
        self.assertIn("TPOT_ES_RETENTION=off\n", env)

    def test_keeps_a_value_the_old_env_already_had(self):
        env, out = self.run_migration("TPOT_ES_RETENTION=30\n", self.policy("365d"))
        self.assertIn("TPOT_ES_RETENTION=30\n", env)
        self.assertEqual(out.strip(), "")

    def test_without_export_it_is_left_empty_and_it_says_why(self):
        # empty: T-Pot leaves the unknown policy alone, nothing can shorten it by one click
        env, out = self.run_migration("TPOT_TYPE=HIVE\n", None)
        self.assertIn("TPOT_ES_RETENTION=\n", env)
        self.assertIn("WARN: TPOT_ES_RETENTION is left empty, T-Pot leaves the ILM policy tpot as it is; "
                      "tpot elastic retention shows it, --adopt lets T-Pot manage it.", out)
        self.assertNotIn("OK:", out)

    def test_not_derivable_is_left_empty(self):
        env, out = self.run_migration("TPOT_TYPE=HIVE\n", self.policy("36h"))
        self.assertIn("TPOT_ES_RETENTION=\n", env)
        self.assertIn("WARN: TPOT_ES_RETENTION is left empty", out)

    def test_an_unreadable_export_is_left_empty(self):
        env, out = self.run_migration("TPOT_TYPE=HIVE\n", "not json")
        self.assertIn("TPOT_ES_RETENTION=\n", env)
        self.assertIn("WARN: TPOT_ES_RETENTION is left empty", out)

    def test_ok_only_when_the_value_was_written(self):
        env, out = self.run_migration("TPOT_TYPE=HIVE\n", self.policy("365d"), read_only=True)
        self.assertIn("TPOT_ES_RETENTION=30\n", env)
        self.assertNotIn("OK:", out)
        self.assertIn("WARN", out)

    def test_without_the_old_env_nothing_is_written(self):
        # whether the release before had the key is unknown, so the function leaves .env alone
        env, out = self.run_migration(None, self.policy("365d"))
        self.assertIn("TPOT_ES_RETENTION=30\n", env)
        self.assertEqual(out.strip(), "")

    def test_a_failed_write_of_the_same_value_is_no_ok_either(self):
        env, out = self.run_migration("TPOT_TYPE=HIVE\n", self.policy("30d"), read_only=True)
        self.assertIn("TPOT_ES_RETENTION=30\n", env)
        self.assertNotIn("OK:", out)
        self.assertIn("WARN: TPOT_ES_RETENTION could not be written", out)

    def export_of(self, stage: bool, archive: bool):
        """fuRETENTION_EXPORT with a stage file and/or an archive that has elastic/ilm_policy_tpot.json."""
        with tempfile.TemporaryDirectory() as tmp:
            work, src = os.path.join(tmp, "work"), os.path.join(tmp, "src")
            os.makedirs(os.path.join(src, "elastic"))
            os.makedirs(work)
            with open(os.path.join(src, "elastic", "ilm_policy_tpot.json"), "w") as h:
                json.dump({"policy": self.policy("365d")}, h)
            with open(os.path.join(src, "env"), "w") as h:
                h.write("TPOT_TYPE=HIVE\n")
            tarball = os.path.join(tmp, "backup.tgz")
            subprocess.run(["tar", "czf", tarball, "-C", src, "env"] + (["elastic"] if archive else []), check=True)
            staged = os.path.join(work, "stage", "elastic", "ilm_policy_tpot.json")
            if stage:
                os.makedirs(os.path.dirname(staged))
                with open(staged, "w") as h:
                    json.dump({"policy": self.policy("90d")}, h)
            script = textwrap.dedent(f"""
                myTMPDIR='{work}'
                eval "$(sed -n '/^function fuRETENTION_EXPORT ()/,/^}}/p' update.sh)"
                fuRETENTION_EXPORT '{staged}' '{tarball}'
            """)
            result = subprocess.run(["bash", "-c", script], cwd=REPO, capture_output=True, text=True)
            path = result.stdout.strip()
            content = ""
            if path:
                with open(path) as h:
                    content = json.load(h)["policy"]["phases"]["delete"]["min_age"]
            return path, content, work

    def test_export_from_the_stage(self):
        path, content, work = self.export_of(stage=True, archive=True)
        self.assertEqual(path, os.path.join(work, "stage", "elastic", "ilm_policy_tpot.json"))
        self.assertEqual(content, "90d")

    def test_export_from_the_archive_when_the_stage_has_none(self):
        # a restarted run (fuSELFUPDATE) has a new myTMPDIR without the stage
        path, content, work = self.export_of(stage=False, archive=True)
        self.assertEqual(path, os.path.join(work, "elastic", "ilm_policy_tpot.json"))
        self.assertEqual(content, "365d")

    def test_no_export_anywhere(self):
        path, _content, _work = self.export_of(stage=False, archive=False)
        self.assertEqual(path, "")

    def test_restore_uses_the_export_function(self):
        with open(os.path.join(REPO, "update.sh")) as h:
            text = h.read()
        body = text[text.index("function fuRESTORE ()"):]
        body = body[:body.index("\n}\n")]
        self.assertIn("fuRETENTION_EXPORT", body)
        self.assertNotIn('-C "${myTMPDIR}" elastic/', body)


# A df of GNU coreutils (-B1 --output=size|avail|pcent): the sizes in the files df_size and df_avail
# of HOME (plus what the archives of HOME/sizes that are gone took, unless HOME/df_frozen), the
# use in df_pcent. macOS has no -B1, the T-Pot host runs GNU
DF = ('#!/bin/sh\ncase "$*" in\n  *size*) echo "1B-blocks"; cat "$HOME/df_size" ;;\n'
      '  *avail*) echo "Avail"; a=$(cat "$HOME/df_avail")\n'
      '    if [ -f "$HOME/sizes" ] && [ ! -e "$HOME/df_frozen" ]; then\n'
      '      while read -r n k; do [ -e "$HOME/tpot_backups/$n" ] || a=$((a + k * 1024)); done < "$HOME/sizes"\n'
      '    fi\n    echo "$a" ;;\n'
      '  *pcent*) echo "Use%"; cat "$HOME/df_pcent" 2>/dev/null || echo " 40%" ;;\nesac\n')
# du -k <file>: the KiB of the file from HOME/sizes ("<name> <kib>" per line); du -scb (fuBACKUP_SIZE)
# says what HOME/need holds; du -sb (fuES_DATA_FITS, the Elasticsearch data) what HOME/es_need holds,
# without it the size cannot be read. It answers for any path, also one that does not exist
DU = ('#!/bin/sh\nfor a; do f="$a"; done\n'
      'case "$1" in -sb) [ -f "$HOME/es_need" ] || exit 1; printf "%s\\t%s\\n" "$(cat "$HOME/es_need")" "$f"; exit 0 ;; esac\n'
      'case "$1" in -s*b) echo "$(cat "$HOME/need" 2>/dev/null || echo 1)\ttotal"; exit 0 ;; esac\n'
      'k=$(sed -n "s|^${f##*/} ||p" "$HOME/sizes" 2>/dev/null)\necho "${k:-1}\t$f"\n')


# The functions of update.sh that put the Elasticsearch data into the archive of the run (fuCHECK_ELASTIC)
# and start T-Pot again after a refusal
ES_APPEND = ("fuSAME_DIR", "fuSAME_FS", "fuES_DATA_FITS", "fuARCHIVE_HAS_ES_DATA", "fuARCHIVE_NAME",
             "fuARCHIVE_LIST", "fuROTATE", "fuARCHIVE_RENAMED", "fuAPPEND_ES_DATA", "fuRESTART_AFTER_REFUSAL",
             "fuES_ROOM_TEXT", "fuES_ACCEPT_TEXT", "fuES_STEP_REFUSED_ITEMS", "fuES_STEP_RESTORE_ITEMS",
             # the step of the data of an Elasticsearch before 8.19 through 8.19
             "fuES_NEEDS_STEP", "fuES_STEP_WHAT", "fuES_STEP_RESTORE_TEXT", "fuES_STEP_TEXT", "fuES_STEP_COMMANDS",
             "fuES_STEP_LINE", "fuES_STEP_MANUAL",
             "fuES_STEP_REFUSED", "fuES_STEP_ROOM", "fuES_STEP_WAIT", "fuES_STEP_CLEANUP", "fuES_STEP_819")


class DataLossTest(Harness):
    """update.sh loses no data the backup of the run cannot bring back without a confirmation: a yes at
    a terminal or --accept-data-loss, never -y. Without either the step is refused (exit 5 where the
    run stops), --check-data-loss lists them and changes nothing."""

    HELPERS = textwrap.dedent("""
        fuUI_INFO () { echo "INFO: $*"; }; fuUI_OK () { echo "OK: $*"; }; fuUI_WARN () { echo "WARN: $*"; }
        fuUI_HINT () { echo "HINT: $*"; }; fuUI_ERROR () { echo "ERROR: $*"; }
        fuUI_CONFIRM () { echo "ASKED: $*"; return 1; }
        fuUI_SPIN () { shift 2; "$@"; }; timeout () { shift; "$@"; }
        fuDID () { echo "DID: $1: $2"; }; fuMARK () { :; }
        myLOG=/dev/null myCONFIRMED=y myREPOS="ghcr.io/telekom-security" myBACKUP_RESERVE_PERCENT=10
        myBACKUPDIR="$HOME/tpot_backups" myLOSSES=0
    """)

    def setUp(self):
        super().setUp()
        for name, text in (("df", DF), ("du", DU)):
            path = os.path.join(self.bin, name)
            with open(path, "w", encoding="utf-8") as out:
                out.write(text)
            os.chmod(path, 0o755)
        self.tpotce = os.path.join(self.home, "tpotce")
        os.makedirs(self.tpotce)
        self.backups = os.path.join(self.home, "tpot_backups")

    @staticmethod
    def functions(*names):
        """The functions of update.sh, as the tests of fuMIGRATE_RETENTION take them."""
        return "eval \"$(sed -n '" + "; ".join(f"/^function {n} ()/,/^}}/p" for n in names) + "' update.sh)\"\n"

    def write(self, name, text):
        with open(os.path.join(self.home, name), "w", encoding="utf-8") as out:
            out.write(text)

    def environment(self):
        environment = dict(os.environ, HOME=self.home, PATH=f"{self.bin}:{os.environ['PATH']}", TPOT_GUM="off")
        environment.pop("TPOT_MARKS", None)
        return environment

    def bash(self, script):
        return subprocess.run(["bash", "-c", self.HELPERS + script], cwd=REPO, capture_output=True,
                              universal_newlines=True, env=self.environment(), stdin=subprocess.DEVNULL,
                              timeout=60)

    # --- the backups that make room ---------------------------------------------------------------

    def archives(self, sizes_kib):
        """Archives oldest first with the given KiB (du -k); 1 GB disk, 10 % reserve, the backup needs
        50 MB and 4 MB allowance, 140 MB are free: 14 MB short."""
        os.makedirs(self.backups, exist_ok=True)
        names = []
        for number, kib in enumerate(sizes_kib):
            name = f"2026100{number + 1}101010_tpot_backup.tar"
            path = os.path.join(self.backups, name)
            with open(path, "w", encoding="utf-8") as out:
                out.write("x")
            os.utime(path, (1_700_000_000 + number * 100, 1_700_000_000 + number * 100))
            names.append(name)
        self.write("sizes", "".join(f"{n} {k}\n" for n, k in zip(names, sizes_kib)))
        self.write("df_size", "1000000000\n")
        self.write("df_avail", "140000000\n")
        self.write("need", "50000000\n")
        with open(os.path.join(self.tpotce, ".env"), "w") as out:        # fuBACKUP_SIZE measures it (du)
            out.write("TPOT_TYPE=HIVE\n")
        return names

    def left(self):
        return sorted(os.listdir(self.backups))

    SPACE = ("fuLOSS_ADD", "fuLOSS_IN_FILE", "fuLOSS_CONFIRM", "fuDROP_OLDEST", "fuBACKUP_SIZE", "fuNO_ROOM", "fuCHECK_BACKUP_SPACE")

    def test_low_space_without_confirmation_deletes_nothing(self):
        names = self.archives([20000, 20000, 20000])
        result = self.bash(self.functions(*self.SPACE) + 'fuCHECK_BACKUP_SPACE\necho "went on"\n')
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 5, out)
        self.assertEqual(self.left(), names)
        self.assertNotIn("went on", out)
        self.assertNotIn("ASKED", out)                              # no terminal: no question
        self.assertIn("HINT: Not confirmed: no terminal. --accept-data-loss confirms it, -y does not.", out)
        self.assertIn("DID: fail: Not updated: making room would delete older backups, which was not confirmed.",
                      out)
        self.assertIn(names[0], out)                                # the text names what would go
        self.assertNotIn(names[1], out)

    def test_low_space_with_accept_deletes_only_what_is_needed(self):
        names = self.archives([20000, 20000, 20000])
        result = self.bash('myACCEPT_LOSS=1\n' + self.functions(*self.SPACE)
                           + 'fuCHECK_BACKUP_SPACE\necho "went on"\n')
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        self.assertEqual(self.left(), names[1:])                    # the oldest one, no more
        self.assertIn("went on", out)
        self.assertIn("(confirmed with --accept-data-loss)", out)

    def test_not_enough_even_after_dropping_all_deletes_nothing(self):
        names = self.archives([1000, 1000, 50000])                  # the newest always stays
        for accept in ("", "myACCEPT_LOSS=1\n"):
            with self.subTest(accept=bool(accept)):
                result = self.bash(accept + self.functions(*self.SPACE) + 'fuCHECK_BACKUP_SPACE\n')
                out = result.stdout + result.stderr
                self.assertEqual(result.returncode, 1, out)
                self.assertEqual(self.left(), names)
                self.assertIn("Not enough room for a backup", out)

    def test_enough_room_asks_nothing(self):
        names = self.archives([20000, 20000])
        self.write("df_avail", "900000000\n")
        result = self.bash(self.functions(*self.SPACE) + 'fuCHECK_BACKUP_SPACE\necho "went on"\n')
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.left(), names)
        self.assertNotIn("WARN", result.stdout)

    def test_check_mode_lists_backups_and_exits_3(self):
        names = self.archives([20000, 20000, 20000])
        script = ('myCHECK_LOSS=1 myBACKUP_ONLY=1\n'
                  + self.functions(*self.SPACE, "fuCHECK_DATA_LOSS") + 'fuCHECK_DATA_LOSS\necho "rc=$?"\n')
        result = self.bash(script)
        out = result.stdout + result.stderr
        self.assertIn("rc=3", out)
        self.assertEqual(self.left(), names)
        lines = [line for line in out.splitlines() if line.startswith("@@tpot loss ")]
        self.assertEqual(len(lines), 1, out)
        self.assertTrue(lines[0].startswith("@@tpot loss backups ") and names[0] in lines[0], lines)
        self.assertNotIn(names[1], lines[0])

    def test_check_mode_without_a_backup_folder_creates_none(self):
        self.write("df_size", "1000000000\n")
        self.write("df_avail", "900000000\n")
        script = ('myCHECK_LOSS=1 myBACKUP_ONLY=1\n'
                  + self.functions(*self.SPACE, "fuCHECK_DATA_LOSS") + 'fuCHECK_DATA_LOSS\necho "rc=$?"\n')
        result = self.bash(script)
        self.assertIn("rc=0", result.stdout, result.stdout + result.stderr)
        self.assertFalse(os.path.exists(self.backups))
        self.assertNotIn("@@tpot loss", result.stdout)

    # --- the Elasticsearch upgrade -----------------------------------------------------------------

    def elastic(self, archive_has_data, extra="", stub_confirm="", env=""):
        os.makedirs(os.path.join(self.tpotce, "docker", "elk", "elasticsearch"), exist_ok=True)
        with open(os.path.join(self.tpotce, "docker", "elk", "elasticsearch", "Dockerfile"), "w") as out:
            out.write("FROM alpine\nARG ES_VER=9.1.0\n")
        with open(os.path.join(self.tpotce, "docker-compose.yml"), "w") as out:
            out.write("services:\n  elasticsearch:\n    image: x\n")
        with open(os.path.join(self.tpotce, ".env"), "w") as out:
            out.write("TPOT_REPO=ghcr.io/telekom-security\nTPOT_VERSION=24.04.2\n" + env)
        return textwrap.dedent(f"""
            myOLDVERSION=24.04.1 myARCHIVE="$HOME/tpot_backups/x.tar"
            docker () {{ [ "$1 $2" = "image inspect" ] && printf 'PATH=/bin\\nES_VER=8.19.0\\n'; }}
            tar () {{ {"return 0" if archive_has_data else "return 1"}; }}
            fuROLLBACK_CHECKOUT () {{ echo "ROLLBACK"; return 0; }}
            fuCAN_ROLLBACK () {{ return 0; }}
            fuSTART_TPOT () {{ echo "STARTED"; return 0; }}
            trap '[ -z "${{myTMPDIR}}" ] || rm -rf "${{myTMPDIR}}"' EXIT
        """) + extra + self.functions("fuLOSS_IN_FILE", "fuLOSS_CONFIRM", "fuENV_VALUE", "fuDATA_PATH", "fuCOMPOSE_HAS",
                                      "fuES_IMAGE_VERSION", "fuOLD_ES_VERSION", "fuELASTIC_REFUSED", "fuOLD_RELEASE_BACKUP",
                                      "fuTMPDIR", "fuOLD_UPDATER_ARCHIVE", "fuOLD_UPDATER_FRESH", "fuOLD_UPDATER_READ",
                                      "fuOLD_UPDATER_FILE", "fuARCHIVE_MEMBER", "fuARCHIVE_MEMBER_NAME", "fuARCHIVE_ENV",
                                      *ES_APPEND, "fuCHECK_ELASTIC") \
            + stub_confirm + 'fuCHECK_ELASTIC\necho "went on"\n'

    def test_elastic_upgrade_without_full_refused_rolls_back(self):
        result = self.bash(self.elastic(archive_has_data=False))
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 5, out)
        self.assertIn("ROLLBACK", out)
        self.assertNotIn("went on", out)
        self.assertNotIn("ASKED", out)
        self.assertIn("WARN: Elasticsearch 8.19.0 -> 9.1.0 upgrades the data in "
                      f"{self.tpotce}/data/elk on the next start. This cannot be undone and the backup "
                      "of this run does not hold it.", out)
        self.assertIn("DID: fail: Not updated: the Elasticsearch upgrade was not confirmed, the checkout "
                      "and the configuration are back.", out)
        self.assertLess(out.index("sudo cp -a"), out.index("WARN: Elasticsearch 8.19.0"))   # the copy hint first

    def test_elastic_upgrade_with_accept_goes_on(self):
        result = self.bash(self.elastic(archive_has_data=False, extra="myACCEPT_LOSS=1\n"))
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        self.assertIn("went on", out)
        self.assertNotIn("ROLLBACK", out)
        self.assertIn("(confirmed with --accept-data-loss)", out)

    def test_elastic_upgrade_with_full_archive_needs_no_confirmation(self):
        result = self.bash(self.elastic(archive_has_data=True))
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        self.assertIn("went on", out)
        self.assertNotIn("ASKED", out)
        self.assertNotIn("ROLLBACK", out)
        self.assertNotIn("Not confirmed", out)

    def test_ctrl_c_at_the_elastic_question_is_a_no(self):
        """Ctrl+C reaches bash and the question at once: the checkout goes back as for a no (the plain
        question of ui.sh reads from a pipe here, the signal needs no terminal)."""
        import signal
        import time
        asked = os.path.join(self.home, "asked")
        stub = (f'source "{REPO}/installer/lib/ui.sh"; fuUI_INIT\n'
                + self.HELPERS.replace('fuUI_CONFIRM () { echo "ASKED: $*"; return 1; }', "")
                + f'fuLOSS_CONFIRM () {{ : > "{asked}"; fuUI_CONFIRM --default no "Accept?" < <(sleep 30 2>/dev/null); }}\n')
        script = self.elastic(archive_has_data=False, stub_confirm=stub)
        for attempt in range(5):
            if os.path.exists(asked):
                os.remove(asked)
            proc = subprocess.Popen(["bash", "-c", self.HELPERS + script], cwd=REPO, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, universal_newlines=True, start_new_session=True,
                                    stdin=subprocess.DEVNULL, env=self.environment())
            deadline = time.time() + 30
            while not os.path.exists(asked) and proc.poll() is None and time.time() < deadline:
                time.sleep(0.01)
            self.assertTrue(os.path.exists(asked), f"run {attempt}: the question did not come")
            time.sleep(0.2)
            os.killpg(proc.pid, signal.SIGINT)
            out, _err = proc.communicate(timeout=20)
            self.assertEqual(proc.returncode, 5, f"run {attempt}: {out}")
            self.assertIn("ROLLBACK", out)
            self.assertNotIn("went on", out)

    # --- the upgrade from 24.04.1, a refusal with -s, a custom data path with --full -------------

    # tar: the archive of this run has no Elasticsearch data, a .tgz in HOME is read for real
    REAL_TGZ = 'tar () { case "$*" in *.tgz*) command tar "$@" ;; *) return 1 ;; esac; }\n'

    def old_release_backup(self, member="data/elk/data/nodes/0", age=0, env="TPOT_VERSION=24.04.1\n", prefix=""):
        """What the update.sh of 24.04.1 wrote: tar cvf of * and .env of ~/tpotce (data included) to
        HOME/*.tgz; env=None leaves the .env out, prefix="./" names the members ./data, ./.env."""
        path = os.path.join(self.home, "20261011090000_tpot_backup.tgz")
        source = os.path.join(self.home, "src")
        shutil.rmtree(source, ignore_errors=True)
        os.makedirs(os.path.join(source, os.path.dirname(member)), exist_ok=True)
        with open(os.path.join(source, member), "w", encoding="utf-8") as out:
            out.write("x\n")
        with tarfile.open(path, "w") as archive:           # plain tar despite the name, as 24.04.1 did
            archive.add(os.path.join(source, member.split("/")[0]), arcname=prefix + member.split("/")[0])
            if env is not None:
                with open(os.path.join(source, ".env"), "w", encoding="utf-8", newline="") as out:
                    out.write(env)
                archive.add(os.path.join(source, ".env"), arcname=prefix + ".env")
        if age:
            os.utime(path, (time.time() - age, time.time() - age))
        return path

    def first_update(self, env_here=None, **archive):
        """The run the update.sh of 24.04.1 restarted into: ~/tpotce is the release after its reset (.env of
        24.04.2, committed), the archive holds the .env of 24.04.1 and the data. As in the run,
        fuOLD_UPDATER_CONFIG comes first, then myOLDVERSION as fuRESTORE reads it, then the check.
        env_here: a .env of the user's own after the reset (the second update)."""
        script = self.elastic(archive_has_data=False, extra=self.REAL_TGZ)
        with open(os.path.join(self.tpotce, "version"), "w") as out:
            out.write("24.04.2\n")
        git = ["git", "-C", self.tpotce, "-c", "user.email=t@t", "-c", "user.name=t"]
        shutil.rmtree(os.path.join(self.tpotce, ".git"), ignore_errors=True)
        subprocess.run(["git", "init", "-q", self.tpotce], check=True)
        subprocess.run(git + ["add", "-A"], check=True)
        subprocess.run(git + ["commit", "-q", "-m", "release"], check=True)
        if env_here is not None:
            with open(os.path.join(self.tpotce, ".env"), "w") as out:
                out.write(env_here)
        path = self.old_release_backup(**archive)
        restore = read("update.sh").split("function fuRESTORE () {", 1)[1]
        start = restore.index("\tmyOLDVERSION=$(grep")
        oldversion = restore[start:restore.index("esac\n", start) + 5]
        chain = (f'eval "$(sed -n \'/^fuUI_VERSION_GE () {{/,/^}}/p\' {REPO}/installer/lib/ui.sh)"\n'
                 + self.functions("fuTMPDIR", "fuOLD_UPDATER_NEWEST", "fuOLD_UPDATER_UNREADABLE", "fuOLD_UPDATER_CONFIG")
                 + 'trap \'[ -z "${myTMPDIR}" ] || rm -rf "${myTMPDIR}"\' EXIT\n'
                 + 'myPREPARED=""\nfuOLD_UPDATER_CONFIG\n' + oldversion + 'echo "OLDVERSION=${myOLDVERSION}"\n')
        tail = 'fuCHECK_ELASTIC\necho "went on"\n'
        self.assertTrue(script.endswith(tail))
        return path, script[:-len(tail)] + chain + tail

    def test_the_first_update_from_24_04_1_takes_its_archive(self):
        for name, kwargs in (("as 24.04.1 wrote it", {}),
                             ("./ members and a quoted version", {"prefix": "./", "env": 'TPOT_VERSION="24.04.1"\n'}),
                             ("a CR", {"env": "TPOT_VERSION=24.04.1\r\n"})):
            with self.subTest(name):
                archive, script = self.first_update(**kwargs)
                result = self.bash(script)
                out = result.stdout + result.stderr
                self.assertEqual(result.returncode, 0, out)
                self.assertIn(f"Your configuration of 24.04.1 is back from {archive}", out)
                self.assertIn("OLDVERSION=24.04.1\n", out)
                self.assertIn("went on", out)
                self.assertIn(f"OK: The Elasticsearch data is in {archive}", out)
                self.assertNotIn("ASKED", out)
                self.assertNotIn("Not confirmed", out)

    def test_the_second_update_after_24_04_1_does_not_take_it(self):
        """The update after the one from 24.04.1, within a day: .env is the user's of 24.04.2."""
        _archive, script = self.first_update(env_here="TPOT_REPO=ghcr.io/telekom-security\nTPOT_VERSION=24.04.2\n"
                                                      "WEB_USER=bWluZQ==\n")
        result = self.bash(script)
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 5, out)
        self.assertNotIn("is back from", out)
        self.assertIn("OLDVERSION=24.04.2\n", out)
        self.assertNotIn("The Elasticsearch data is in", out)
        self.assertIn("ROLLBACK", out)

    def test_a_backup_of_24_04_1_counts_only_fresh_with_the_data_and_the_data_path(self):
        for name, kwargs, env in (("old", {"age": 25 * 3600}, ""),
                                  ("no data", {"member": "data/uuid"}, ""),
                                  ("another data path", {}, f"TPOT_DATA_PATH={self.home}/elsewhere\n")):
            with self.subTest(name):
                for left in os.listdir(self.home):
                    if left.endswith(".tgz"):
                        os.remove(os.path.join(self.home, left))
                self.old_release_backup(**kwargs)
                result = self.bash(self.elastic(archive_has_data=False, extra=self.REAL_TGZ, env=env))
                self.assertEqual(result.returncode, 5, result.stdout + result.stderr)
                self.assertNotIn("The Elasticsearch data is in", result.stdout)

    def test_a_backup_of_24_04_1_counts_only_for_the_version_that_ran_before(self):
        for name, kwargs, old in (("the second update after 24.04.1", {}, "24.04.2"),
                                  ("an .env of another version", {"env": "TPOT_VERSION=24.04.0\n"}, "24.04.1"),
                                  ("an .env without the version", {"env": "TPOT_REPO=x\n"}, "24.04.1"),
                                  ("no .env", {"env": None}, "24.04.1")):
            with self.subTest(name):
                for left in os.listdir(self.home):
                    if left.endswith(".tgz"):
                        os.remove(os.path.join(self.home, left))
                self.old_release_backup(**kwargs)
                script = self.elastic(archive_has_data=False, extra=self.REAL_TGZ).replace(
                    "myOLDVERSION=24.04.1", f"myOLDVERSION={old}", 1)
                result = self.bash(script)
                self.assertEqual(result.returncode, 5, result.stdout + result.stderr)
                self.assertNotIn("The Elasticsearch data is in", result.stdout)

    def test_no_rollback_says_a_no_cannot_stop_the_upgrade(self):
        result = self.bash(self.elastic(archive_has_data=False, stub_confirm="fuCAN_ROLLBACK () { return 1; }\n"))
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 5, out)
        flat = " ".join(out.split())
        self.assertIn("A no cannot stop the next start of T-Pot from upgrading the data: the checkout is already "
                      f"the new release. Copy {self.tpotce}/data/elk/data first (sudo cp -a {self.tpotce}/data/elk/data "
                      f"{self.backups}/elk_data_8.19.0), then run the update again.", flat)
        self.assertNotIn("answer no, copy it", out)                   # a no does not keep the data as it is
        self.assertNotIn("ROLLBACK", out)
        self.assertNotIn("STARTED", out)
        self.assertIn("DID: fail: Not updated: the Elasticsearch upgrade was not confirmed. The checkout is already "
                      "the new release, the next start of T-Pot upgrades the data: T-Pot stays stopped.", out)

    def test_can_rollback_is_the_check_of_the_rollback(self):
        function = re.search(r"function fuCAN_ROLLBACK \(\) \{.*?\n\}", read("update.sh"), re.S).group(0)
        subprocess.run(["git", "init", "-q", self.tpotce], check=True)
        subprocess.run(["git", "-C", self.tpotce, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
                        "--allow-empty", "-m", "now"], check=True)
        head = subprocess.run(["git", "-C", self.tpotce, "rev-parse", "HEAD"], capture_output=True,
                              universal_newlines=True).stdout.strip()
        restore = os.path.join(self.tpotce, "restore.sh")
        with open(restore, "w", encoding="utf-8") as out:
            out.write("#!/bin/sh\n")
        os.chmod(restore, 0o755)
        for commit, expected in (("0123456789abcdef", "0"), (head, "1"), ("", "1")):
            with self.subTest(commit=commit):
                archive = make_backup(self.home, {"rollback.txt": commit + "\n"})
                result = self.bash(f'myARCHIVE="{archive}"\n{function}\nfuCAN_ROLLBACK; echo "rc=$?"\n')
                self.assertIn(f"rc={expected}", result.stdout, result.stdout + result.stderr)
        self.assertIn("if ! fuCAN_ROLLBACK;", re.search(r"function fuROLLBACK_CHECKOUT \(\) \{.*?\n\}",
                                                     read("update.sh"), re.S).group(0))

    def test_a_refusal_with_start_starts_the_old_release(self):
        result = self.bash(self.elastic(archive_has_data=False, extra="mySTART=1\n"))
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 5, out)
        self.assertLess(out.index("ROLLBACK"), out.index("STARTED"))
        self.assertIn("DID: ok: T-Pot runs again, on the release from before this update.", out)

    def test_a_refusal_without_start_keeps_it_stopped(self):
        result = self.bash(self.elastic(archive_has_data=False))
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 5, out)
        self.assertNotIn("STARTED", out)
        self.assertIn("DID: next: Copy ", out)

    def test_a_refusal_with_start_but_no_rollback_does_not_start(self):
        result = self.bash(self.elastic(archive_has_data=False, extra="mySTART=1\n",
                                        stub_confirm='fuROLLBACK_CHECKOUT () { echo "ROLLBACK"; return 1; }\n'))
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 5, out)
        self.assertNotIn("STARTED", out)

    def test_full_with_a_custom_data_path_needs_no_question(self):
        """The --full archive this run wrote (no handover) holds the data folder that runs, wherever it is."""
        full = 'myFULL=1 myARCHIVE="$HOME/tpot_backups/20261014101010_tpot_backup_full.tar"\n'
        result = self.bash(self.elastic(archive_has_data=True, env=f"TPOT_DATA_PATH={self.home}/custom\n",
                                        extra=full))
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        self.assertIn("went on", out)
        self.assertNotIn("Not confirmed", out)
        self.assertIn("OK: The Elasticsearch data is in", out)

    def test_no_pause_left(self):
        text = read("update.sh")
        self.assertNotIn("sleep 15", text)
        self.assertNotIn("fuELASTIC_STOPPED", text)

    # --- images that cannot be pulled again --------------------------------------------------------

    def images(self, extra=""):
        with open(os.path.join(self.tpotce, ".env"), "w") as out:
            out.write("TPOT_VERSION=24.04.2\n")
        self.write("removed", "")
        script = textwrap.dedent("""
            docker () {
              local myA="" myI=""
              case "$1 $2" in
                "images --format")
                  for myI in ghcr.io/telekom-security/cowrie:24.04.1 ghcr.io/telekom-security/mine:24.04.1 \\
                             ghcr.io/telekom-security/cowrie:24.04.2; do
                    grep -qxF "${myI}" "$HOME/removed" || echo "${myI}"
                  done ;;
                "image inspect")
                  for myA; do :; done
                  case "${myA}" in *cowrie*) echo "ghcr.io/telekom-security/cowrie@sha256:aaa" ;; esac ;;
                "buildx imagetools") echo "sha256:aaa" ;;
              esac
            }
            fuRMI () { echo "RMI: $*"; printf '%s\\n' "$@" >> "$HOME/removed"; }
        """) + extra + self.functions("fuLOSS_IN_FILE", "fuLOSS_CONFIRM", "fuIMAGE_PULLABLE", "fuSORT_IMAGES", "fuLOCAL_IMAGES_TEXT", "fuUNSURE_IMAGES_TEXT", "fuIMAGES",
                                                    "fuREMOVEOLDIMAGES") \
            + 'fuREMOVEOLDIMAGES\necho "went on"\n'
        return self.bash(script)

    def test_local_images_are_kept_without_confirmation(self):
        result = self.images()
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        self.assertIn("went on", out)
        self.assertEqual([line for line in out.splitlines() if line.startswith("RMI: ")],
                         ["RMI: ghcr.io/telekom-security/cowrie:24.04.1"])
        self.assertIn("WARN: 1 image(s) built here cannot be pulled again: ghcr.io/telekom-security/mine:24.04.1",
                      out)
        self.assertNotIn("still in use", out)

    def test_local_images_go_with_accept(self):
        result = self.images(extra="myACCEPT_LOSS=1\n")
        out = result.stdout + result.stderr
        rmi = [line for line in out.splitlines() if line.startswith("RMI: ")]
        self.assertEqual(len(rmi), 1, out)
        self.assertIn("ghcr.io/telekom-security/mine:24.04.1", rmi[0])
        self.assertIn("ghcr.io/telekom-security/cowrie:24.04.1", rmi[0])

    # --- the check before an update ----------------------------------------------------------------

    def target(self):
        """An origin one commit ahead of ~/tpotce: Elasticsearch 9.1.0, the images :24.04.3."""
        git = ["-c", "user.email=t@t", "-c", "user.name=t"]
        origin = os.path.join(self.home, "origin")
        os.makedirs(os.path.join(origin, "docker", "elk", "elasticsearch"))
        subprocess.run(["git", "init", "-q", origin], check=True)
        subprocess.run(["git", "-C", origin, "checkout", "-q", "-b", "master"], check=True)
        dockerfile = os.path.join(origin, "docker", "elk", "elasticsearch", "Dockerfile")

        def commit(es, tag):
            with open(dockerfile, "w") as out:
                out.write(f"ARG ES_VER={es}\n")
            with open(os.path.join(origin, "env.example"), "w") as out:
                out.write(f"TPOT_REPO=ghcr.io/telekom-security\nTPOT_VERSION={tag}\n")
            subprocess.run(["git", "-C", origin, "add", "-A"], check=True)
            subprocess.run(["git", "-C", origin] + git + ["commit", "-q", "-m", tag], check=True)
        commit("8.19.0", "24.04.2")
        shutil.rmtree(self.tpotce)
        subprocess.run(["git", "clone", "-q", origin, self.tpotce], check=True)
        commit("9.1.0", "24.04.3")
        with open(os.path.join(self.tpotce, ".env"), "w") as out:
            out.write("TPOT_REPO=ghcr.io/telekom-security\nTPOT_VERSION=24.04.2\n")
        with open(os.path.join(self.tpotce, "docker-compose.yml"), "w") as out:
            out.write("services:\n  elasticsearch:\n    image: x\n")
        self.write("df_size", "1000000000\n")
        self.write("df_avail", "900000000\n")
        return origin

    CHECK = textwrap.dedent("""
        docker () {
          local myA=""
          case "$1 $2" in
            "images --format")
              printf '%s\\n' ghcr.io/telekom-security/elasticsearch:24.04.2 ghcr.io/telekom-security/mine:24.04.2 ;;
            "image inspect")
              for myA; do :; done
              case "$4" in
                *Env*) printf 'ES_VER=8.19.0\\n' ;;
                *) case "${myA}" in *mine*) ;; *) echo "${myA%:*}@sha256:aaa" ;; esac ;;
              esac ;;
            "buildx imagetools") echo "sha256:aaa" ;;
          esac
        }
    """)
    CHECK_FUNCTIONS = ("fuLOSS_ADD", "fuLOSS_IN_FILE", "fuLOSS_CONFIRM", "fuDROP_OLDEST", "fuBACKUP_SIZE", "fuNO_ROOM",
                       "fuCHECK_BACKUP_SPACE", "fuES_DATA_FITS", "fuSAME_FS", "fuES_NEEDS_STEP", "fuES_STEP_WHAT", "fuES_STEP_TEXT",
                       "fuENV_VALUE", "fuDATA_PATH", "fuCOMPOSE_HAS", "fuES_IMAGE_VERSION", "fuOLD_ES_VERSION", "fuIMAGE_PULLABLE", "fuSORT_IMAGES",
                       # the archive of 24.04.1 is there to be found, the check must not take it
                       "fuOLD_RELEASE_BACKUP", "fuOLD_UPDATER_ARCHIVE", "fuOLD_UPDATER_FRESH", "fuOLD_UPDATER_READ",
                       "fuOLD_UPDATER_FILE", "fuARCHIVE_MEMBER", "fuARCHIVE_MEMBER_NAME", "fuARCHIVE_ENV",
                       "fuLOCAL_IMAGES_TEXT", "fuUNSURE_IMAGES_TEXT", "fuIMAGES", "fuLOSS_TARGET", "fuCHECK_DATA_LOSS")

    def check(self, extra=""):
        script = ('myCHECK_LOSS=1\n' + self.CHECK + extra + self.functions(*self.CHECK_FUNCTIONS)
                  + 'fuCHECK_DATA_LOSS\necho "rc=$?"\n')
        result = self.bash(script)
        out = result.stdout + result.stderr
        return out, [line for line in out.splitlines() if line.startswith("@@tpot loss ")]

    def head(self):
        return subprocess.run(["git", "-C", self.tpotce, "rev-parse", "HEAD"], capture_output=True,
                              universal_newlines=True).stdout

    def test_check_mode_finds_the_elastic_upgrade_and_the_local_images(self):
        self.target()
        head = self.head()
        out, lines = self.check()
        self.assertIn("rc=3", out)
        self.assertEqual([line.split()[2] for line in lines], ["elastic", "images"], out)
        self.assertIn("8.19.0 -> 9.1.0", lines[0])
        self.assertIn("1 image(s) built here cannot be pulled again: ghcr.io/telekom-security/mine:24.04.2",
                      lines[1])
        self.assertEqual(self.head(), head)                             # fetched, not moved

    def test_check_mode_with_full_has_no_elastic_finding(self):
        self.target()
        os.makedirs(os.path.join(self.tpotce, "data", "elk", "data"))
        out, lines = self.check(extra="myFULL=1\n")
        self.assertEqual([line.split()[2] for line in lines], ["images"], out)

    def test_check_mode_with_full_and_a_custom_data_path_has_no_elastic_finding(self):
        self.target()
        custom = os.path.join(self.home, "custom")
        os.makedirs(os.path.join(custom, "elk", "data"))
        with open(os.path.join(self.tpotce, ".env"), "a") as out:
            out.write(f"TPOT_DATA_PATH={custom}\n")
        out, lines = self.check(extra="myFULL=1\n")
        self.assertEqual([line.split()[2] for line in lines], ["images"], out)

    def test_check_mode_never_takes_a_backup_of_24_04_1(self):
        """The check runs for a T-Pot past 24.04.1: a fresh archive of that updater belongs to an update
        before this one, whatever its .env names."""
        for version in ("24.04.2", "24.04.1"):
            with self.subTest(version=version):
                shutil.rmtree(os.path.join(self.home, "origin"), ignore_errors=True)
                self.target()
                archive = self.old_release_backup(env=f"TPOT_VERSION={version}\n")
                out, lines = self.check()
                self.assertEqual([line.split()[2] for line in lines], ["elastic", "images"], out)
                self.assertIn("8.19.0 -> 9.1.0", lines[0])
                self.assertNotIn(archive, out)

    def test_check_mode_says_error_without_a_target(self):
        self.target()
        subprocess.run(["git", "-C", self.tpotce, "remote", "remove", "origin"], check=True)
        out, lines = self.check()
        self.assertIn("rc=1", out)
        self.assertEqual(lines, [])

    def test_check_mode_follows_a_branch(self):
        origin = self.target()
        subprocess.run(["git", "-C", origin, "branch", "-q", "same", "HEAD~1"], check=True)
        out, lines = self.check(extra="myTPOT_BRANCH=same myTPOT_SOURCE_GIVEN=1\n")
        self.assertEqual(lines, [], out)                   # the same Elasticsearch, the same image tag
        self.assertIn("rc=0", out)

    def test_check_mode_follows_a_repository(self):
        origin = self.target()
        fork = os.path.join(self.home, "fork")
        subprocess.run(["git", "clone", "-q", "--bare", origin, fork], check=True)
        subprocess.run(["git", "-C", fork, "update-ref", "refs/heads/master", "HEAD~1"], check=True)
        out, lines = self.check(extra=f"myTPOT_REPO_URL={fork} myTPOT_SOURCE_GIVEN=1\n")
        self.assertEqual(lines, [], out)
        self.assertIn("rc=0", out)
        self.assertEqual(subprocess.run(["git", "-C", self.tpotce, "remote", "get-url", "origin"],
                                        capture_output=True, universal_newlines=True).stdout.strip(), origin)

    def test_check_mode_runs_without_y_and_writes_nothing(self):
        names = self.archives([20000, 20000, 20000])
        result = self.run_script(os.path.join(REPO, "update.sh"), "--check-data-loss", "--backup-only")
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 3, out)
        self.assertEqual(self.left(), names)
        self.assertIn("@@tpot loss backups ", out)
        self.assertNotIn("'-y' switch", out)

    # --- options, restart and help -----------------------------------------------------------------

    def restart_args(self, script_text, *args):
        function = re.search(r"function fuRESTART_ARGS \(\) \{.*?\n\}", read("update.sh"), re.S).group(0)
        target = os.path.join(self.home, "target.sh")
        with open(target, "w", encoding="utf-8") as out:
            out.write(script_text)
        result = subprocess.run(["bash", "-c", f'{function}\nfuRESTART_ARGS "$@"', "x", target] + list(args),
                                capture_output=True, universal_newlines=True)
        return result.stdout.split("\n")[:-1]

    def test_restart_hands_accept_on(self):
        self.assertEqual(self.restart_args(read("update.sh"), "-F", "--accept-data-loss"),
                         ["-F", "--accept-data-loss"])

    def test_restart_into_an_older_update_sh_drops_accept(self):
        old = 'while getopts ":yFsob:r:B:h" opt; do\n'
        self.assertEqual(self.restart_args(old, "-F", "--accept-data-loss", "-B", "/run/f"),
                         ["-F", "-B", "/run/f"])

    @staticmethod
    def main_section():
        return read("update.sh").split("################\n# Main section #\n################", 1)[1]

    def test_selfupdate_gets_accept(self):
        main = self.main_section()
        self.assertIn('[ -z "${myACCEPT_LOSS}" ] || myLOSS_ARGS+=(--accept-data-loss)', main)
        self.assertIn('fuSELFUPDATE "$@" ${myLOSS_ARGS[@]+"${myLOSS_ARGS[@]}"}', main)

    def test_yes_does_not_set_accept(self):
        main = self.main_section()
        self.assertIn("--check-data-loss) myCHECK_LOSS=1", main)
        self.assertIn("--accept-data-loss) myACCEPT_LOSS=1", main)
        y = re.search(r"\n    y\)\n(.*?);;", main, re.S).group(1)
        self.assertNotIn("myACCEPT_LOSS", y)

    def test_space_is_checked_before_the_stop(self):
        main = self.main_section()
        for start in ("--backup-only: the backup", "\nfuCHECK_VERSION\n"):     # T-Pot keeps running
            part = main[main.index(start):]
            self.assertLess(part.index("fuCHECK_BACKUP_SPACE"), part.index("fuSTOP_TPOT"))

    def test_help_names_both_options(self):
        result = self.run_script(os.path.join(REPO, "update.sh"), "-h")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(re.search(r"^  --check-data-loss  ", result.stdout, re.M), result.stdout)
        self.assertTrue(re.search(r"^  --accept-data-loss  ", result.stdout, re.M), result.stdout)
        flat = " ".join(result.stdout.split())
        self.assertIn("-y Confirm the update, required. It does not confirm a loss of data, see "
                      "--accept-data-loss", flat)
        self.assertIn("./update.sh --check-data-loss", flat)
        self.assertIn("exit 5", flat)
        # the exit codes of the check: found, nothing found, cannot tell
        self.assertIn("Exit 0: nothing found, 3: found, 1: it cannot tell.", flat)

    # --- one data path, the old Elasticsearch under every registry ---------------------------------

    def data_path(self, script, line):
        """fuDATA_PATH of update.sh or restore.sh with this TPOT_DATA_PATH line in ~/tpotce/.env."""
        with open(os.path.join(self.tpotce, ".env"), "w", encoding="utf-8") as out:
            out.write("TPOT_TYPE=HIVE\n" + line)
        if script == "update.sh":
            text = self.functions("fuENV_VALUE", "fuDATA_PATH")
        else:
            text = (f'myTPOTDIR="$HOME/tpotce"\n'
                    + self.functions("fuENV_VALUE", "fuDATA_PATH").replace("' update.sh)", "' restore.sh)"))
        return self.bash(text + "fuDATA_PATH\n").stdout.strip().splitlines()[-1]

    def test_both_scripts_read_the_data_path_alike(self):
        cases = (("", f"{self.tpotce}/data"),
                 ("TPOT_DATA_PATH=./data\n", f"{self.tpotce}/data"),
                 ("TPOT_DATA_PATH=mydata/\n", f"{self.tpotce}/mydata"),
                 ('TPOT_DATA_PATH: "/srv/tpot//"\n', "/srv/tpot"),
                 ("TPOT_DATA_PATH='/srv/tpot'\n", "/srv/tpot"),
                 ("TPOT_DATA_PATH=~/tpotdata\n", f"{self.home}/tpotdata"),
                 ("TPOT_DATA_PATH=~\n", self.home))
        for script in ("update.sh", "restore.sh"):
            for line, wanted in cases:
                with self.subTest(script=script, line=line):
                    self.assertEqual(self.data_path(script, line), wanted)

    def test_both_scripts_have_the_same_code_for_it(self):
        def body(script):
            function = re.search(r"function fuDATA_PATH \(\) \{.*?\n\}", read(script), re.S).group(0)
            return function.split('local myPATH=""', 1)[1]
        self.assertEqual(body("update.sh"), body("restore.sh"))

    def test_the_old_elasticsearch_is_found_under_every_registry(self):
        """An installation that just moved from dtagdevsec to ghcr: its old images are dtagdevsec's."""
        docker = ('myREPOS="dtagdevsec ghcr.io/telekom-security"\n'
                  'docker () { case "$1 $2 $*" in "image inspect"*dtagdevsec/elasticsearch:24.04.1) '
                  "printf 'ES_VER=8.19.0\\n' ;; esac; }\n")
        result = self.bash(self.elastic(archive_has_data=False, stub_confirm=docker))
        out = result.stdout + result.stderr
        self.assertIn("HINT: Elasticsearch 8.19.0 -> 9.1.0.", out)
        self.assertNotIn("Cannot tell which Elasticsearch version ran so far", out)

    def test_check_mode_finds_the_old_elasticsearch_under_every_registry(self):
        self.target()
        docker = ('myREPOS="dtagdevsec ghcr.io/telekom-security"\n'
                  'docker () { case "$1 $2 $*" in "image inspect"*Env*dtagdevsec/elasticsearch:24.04.2) '
                  "printf 'ES_VER=8.19.0\\n' ;; esac; }\n")
        out, lines = self.check(extra=docker)
        self.assertEqual([line.split()[2] for line in lines], ["elastic"], out)
        self.assertIn("8.19.0 -> 9.1.0", lines[0])

    # --- review round 1 ----------------------------------------------------------------------------

    def pullable(self, inspect, buildx):
        """fuIMAGE_PULLABLE with the given RepoDigests lines and the answer of buildx imagetools
        (None: buildx fails)."""
        answer = "return 1" if buildx is None else f'echo "{buildx}"'
        script = textwrap.dedent(f"""
            docker () {{
              case "$1 $2" in
                "image inspect") printf '{inspect}' ;;
                "buildx imagetools") {answer} ;;
              esac
            }}
        """) + self.functions("fuIMAGE_PULLABLE") + 'fuIMAGE_PULLABLE repo/x:1; echo "rc=$?"\n'
        return {"rc=0": "pullable", "rc=1": "here", "rc=2": "unsure"}[self.bash(script).stdout.strip().splitlines()[-1]]

    def test_no_digest_is_built_here(self):
        self.assertEqual(self.pullable("", "sha256:aaa"), "here")

    def test_the_same_digest_as_the_registry_is_pullable(self):
        self.assertEqual(self.pullable("repo/x@sha256:bbb\\nrepo/x@sha256:aaa\\n", "sha256:aaa"), "pullable")

    def test_another_digest_than_the_registry_is_built_here(self):
        # the containerd image store gives a local build a RepoDigest of its own
        self.assertEqual(self.pullable("repo/x@sha256:bbb\\n", "sha256:aaa"), "here")

    def test_buildx_failing_cannot_tell(self):
        # no network, a timeout, no buildx, any other error: the registry did not say
        self.assertEqual(self.pullable("repo/x@sha256:aaa\\n", None), "unsure")
        self.assertEqual(self.pullable("repo/x@sha256:aaa\\n", ""), "unsure")

    def test_without_timeout_it_cannot_tell(self):
        # a PATH with sed, tr and grep but no timeout, a docker stub that records each call: the digest is
        # read (docker image inspect ran), the registry is never asked, it cannot tell (rc 2)
        tools = os.path.join(self.home, "tools")
        os.makedirs(tools)
        for name in ("sed", "tr", "grep"):
            os.symlink(shutil.which(name), os.path.join(tools, name))
        calls = os.path.join(self.home, "docker_calls")
        script = (self.functions("fuIMAGE_PULLABLE") + f'unset -f timeout\nPATH="{tools}"\n'
                  f'docker () {{ echo "$*" >> "{calls}"; case "$1 $2" in "image inspect") echo "repo/x@sha256:aaa" ;; '
                  '*) echo "sha256:aaa" ;; esac; }\n'
                  'fuIMAGE_PULLABLE repo/x:1; echo "rc=$?"\n')
        result = self.bash(script)
        self.assertEqual(result.stdout.strip().splitlines()[-1], "rc=2", result.stdout + result.stderr)
        with open(calls, encoding="utf-8") as handle:
            ran = handle.read().splitlines()
        self.assertTrue(ran and all(line.startswith("image inspect ") and line.endswith(" repo/x:1") for line in ran),
                        ran)
        self.assertFalse(any("buildx" in line for line in ran), ran)

    def sort_images(self, deadline, buildx="echo sha256:aaa"):
        """fuSORT_IMAGES on three images with RepoDigests, each registry request logged to HOME/asked."""
        script = textwrap.dedent(f"""
            myPULLABLE_DEADLINE={deadline}
            docker () {{
              case "$1 $2" in
                "image inspect") echo "repo/x@sha256:aaa" ;;
                "buildx imagetools") echo "$*" >> "$HOME/asked"; {buildx} ;;
              esac
            }}
        """) + self.functions("fuIMAGE_PULLABLE", "fuSORT_IMAGES") + textwrap.dedent("""
            fuSORT_IMAGES repo/a:1 repo/b:1 repo/c:1
            echo "pullable=${myPULLABLE[*]}"; echo "built=${myBUILT[*]}"; echo "unsure=${myUNSURE[*]}"
        """)
        out = self.bash(script).stdout
        try:
            with open(os.path.join(self.home, "asked"), encoding="utf-8") as handle:
                asked = handle.read().splitlines()
        except FileNotFoundError:
            asked = []
        return out, asked

    def test_the_three_states_are_sorted(self):
        out, asked = self.sort_images(90)
        self.assertIn("pullable=repo/a:1 repo/b:1 repo/c:1", out)
        self.assertEqual(len(asked), 3)
        out, _asked = self.sort_images(90, buildx="return 1")
        self.assertIn("unsure=repo/a:1 repo/b:1 repo/c:1", out)

    def test_after_the_deadline_the_registry_is_not_asked_again(self):
        out, asked = self.sort_images(1, buildx="sleep 1.2; echo sha256:aaa")
        self.assertEqual(len(asked), 1, out)                       # the first one took the whole time
        self.assertIn("pullable=repo/a:1\n", out)
        self.assertIn("unsure=repo/b:1 repo/c:1", out)
        os.remove(os.path.join(self.home, "asked"))
        out, asked = self.sort_images(0)
        self.assertEqual(asked, [], out)
        self.assertIn("unsure=repo/a:1 repo/b:1 repo/c:1", out)

    def test_the_deadline_is_90_seconds(self):
        self.assertIn("\nmyPULLABLE_DEADLINE=90\n", read("update.sh"))
        for name in ("fuREMOVEOLDIMAGES", "fuCHECK_DATA_LOSS"):
            body = re.search(rf"function {name} \(\) \{{.*?\n\}}", read("update.sh"), re.S).group(0)
            self.assertIn("fuSORT_IMAGES", body, name)
            self.assertNotIn("fuIMAGE_PULLABLE", body, name)

    def test_the_texts_name_each_state(self):
        script = self.functions("fuLOCAL_IMAGES_TEXT", "fuUNSURE_IMAGES_TEXT") + textwrap.dedent("""
            fuLOCAL_IMAGES_TEXT repo/a:1 repo/b:1; fuUNSURE_IMAGES_TEXT repo/c:1
        """)
        self.assertEqual(self.bash(script).stdout.splitlines()[-2:],
                         ["2 image(s) built here cannot be pulled again: repo/a:1, repo/b:1",
                          "1 image(s) could not be confirmed as pullable again (registry not reachable or slow): "
                          "repo/c:1"])

    def test_images_that_cannot_be_told_are_kept_without_confirmation(self):
        result = self.images(extra='myPULLABLE_DEADLINE=0\n')
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        self.assertEqual([line for line in out.splitlines() if line.startswith("RMI: ")], [])
        self.assertIn("WARN: 2 image(s) could not be confirmed as pullable again (registry not reachable or slow): "
                      "ghcr.io/telekom-security/cowrie:24.04.1, ghcr.io/telekom-security/mine:24.04.1", out)
        self.assertIn("went on", out)

    def test_check_mode_lists_images_that_cannot_be_told(self):
        self.target()
        out, lines = self.check(extra="myPULLABLE_DEADLINE=0\n")
        self.assertEqual([line.split()[2] for line in lines], ["elastic", "images"], out)
        self.assertIn("2 image(s) could not be confirmed as pullable again (registry not reachable or slow): "
                      "ghcr.io/telekom-security/elasticsearch:24.04.2, ghcr.io/telekom-security/mine:24.04.2",
                      lines[1])

    def test_ctrl_c_during_the_rollback_lets_it_finish(self):
        """A second Ctrl+C while restore.sh puts the checkout back must not leave it half way."""
        import signal
        import time
        rolling = os.path.join(self.home, "rolling")
        script = self.elastic(archive_has_data=False, stub_confirm=(
            f'fuROLLBACK_CHECKOUT () {{ : > "{rolling}"; sleep 2; echo "ROLLBACK DONE"; return 0; }}\n'))
        for attempt in range(3):
            if os.path.exists(rolling):
                os.remove(rolling)
            proc = subprocess.Popen(["bash", "-c", self.HELPERS + script], cwd=REPO, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, universal_newlines=True, start_new_session=True,
                                    stdin=subprocess.DEVNULL, env=self.environment())
            deadline = time.time() + 30
            while not os.path.exists(rolling) and proc.poll() is None and time.time() < deadline:
                time.sleep(0.01)
            self.assertTrue(os.path.exists(rolling), f"run {attempt}: the rollback did not start")
            time.sleep(0.2)
            os.killpg(proc.pid, signal.SIGINT)
            out, _err = proc.communicate(timeout=20)
            self.assertEqual(proc.returncode, 5, f"run {attempt}: {out}")
            self.assertIn("ROLLBACK DONE", out)
            self.assertIn("the checkout and the configuration are back", out)

    def test_still_short_after_deleting_stops_before_the_backup(self):
        names = self.archives([20000, 20000, 20000])
        self.write("df_frozen", "")                 # the filesystem frees less than du said
        result = self.bash('myACCEPT_LOSS=1\n' + self.functions(*self.SPACE)
                           + 'fuCHECK_BACKUP_SPACE\necho "went on"\n')
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, out)
        self.assertEqual(self.left(), names[1:])    # the confirmed plan, nothing beyond it
        self.assertIn("Not enough room for a backup", out)
        self.assertNotIn("went on", out)

    # --- --accept-data-loss-file: only what was shown is confirmed ---------------------------------

    def accept_file(self, *lines):
        path = os.path.join(self.home, "confirmed")
        with open(path, "w", encoding="utf-8") as out:
            out.write("".join(f"{line}\n" for line in lines))
        return f'myACCEPT_FILE="{path}"\n'

    IMAGES_ONLY = "@@tpot loss images 1 image(s) built here cannot be pulled again: ghcr.io/telekom-security/mine:24.04.1"

    def test_a_file_with_only_images_refuses_the_backups(self):
        names = self.archives([20000, 20000, 20000])
        result = self.bash(self.accept_file(self.IMAGES_ONLY) + self.functions(*self.SPACE)
                           + 'fuCHECK_BACKUP_SPACE\necho "went on"\n')
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 5, out)
        self.assertEqual(self.left(), names)
        self.assertNotIn("went on", out)
        self.assertIn("HINT: --accept-data-loss-file does not confirm it: it was not shown like this before the run.",
                      out)

    def test_a_file_with_only_images_refuses_the_elastic_upgrade(self):
        result = self.bash(self.elastic(archive_has_data=False, extra=self.accept_file(self.IMAGES_ONLY)))
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 5, out)
        self.assertIn("ROLLBACK", out)
        self.assertNotIn("went on", out)

    def test_a_file_with_the_elastic_line_confirms_the_upgrade(self):
        line = ("@@tpot loss elastic Elasticsearch 8.19.0 -> 9.1.0 upgrades the data in /x/elk on the next start.")
        result = self.bash(self.elastic(archive_has_data=False, extra=self.accept_file(line)))
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        self.assertIn("went on", out)
        self.assertIn("(confirmed with --accept-data-loss-file)", out)

    def backups_line(self, *names):
        listed = ", ".join(f"{name} (5 MB)" for name in names)
        return (f"@@tpot loss backups Making room for the backup deletes {len(names)} older backup(s) in "
                f"{self.backups}: {listed}.")

    def test_a_backup_the_file_does_not_name_is_refused(self):
        names = self.archives([5000, 5000, 5000, 5000])                 # 14 MB short: three of them go
        result = self.bash(self.accept_file(self.backups_line(names[0], names[1])) + self.functions(*self.SPACE)
                           + 'fuCHECK_BACKUP_SPACE\necho "went on"\n')
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 5, out)
        self.assertEqual(self.left(), names)                             # none of them, not even A and B
        self.assertNotIn("went on", out)

    def test_the_backups_the_file_names_are_confirmed(self):
        names = self.archives([20000, 20000, 20000])                    # only the oldest one goes
        result = self.bash(self.accept_file(self.backups_line(names[0], names[1])) + self.functions(*self.SPACE)
                           + 'fuCHECK_BACKUP_SPACE\necho "went on"\n')
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        self.assertEqual(self.left(), names[1:])
        self.assertIn("went on", out)
        self.assertIn("(confirmed with --accept-data-loss-file)", out)

    def test_a_name_is_matched_whole(self):
        names = self.archives([20000, 20000, 20000])
        longer = names[0].replace(".tar", "_1.tar")                     # another archive, same beginning
        result = self.bash(self.accept_file(self.backups_line(longer), self.backups_line("1" + names[0]))
                           + self.functions(*self.SPACE) + 'fuCHECK_BACKUP_SPACE\n')
        self.assertEqual(result.returncode, 5, result.stdout + result.stderr)
        self.assertEqual(self.left(), names)

    def confirmed(self, key, line, *names):
        """fuLOSS_IN_FILE of the file with this line: rc 0 for confirmed. The key and the names go through a
        file, one per line, into the arguments: no shell quoting, a name may hold any character but a newline."""
        self.write("loss_args", "".join(f"{item}\n" for item in (key,) + names))
        result = self.bash(self.accept_file(line) + self.functions("fuLOSS_IN_FILE")
                           + 'myARGS=()\nwhile IFS= read -r myA; do myARGS+=("${myA}"); done < "$HOME/loss_args"\n'
                           + 'fuLOSS_IN_FILE "${myARGS[@]}"; echo "rc=$?"\n')
        return "rc=0" in result.stdout

    def test_a_name_with_a_quote_is_matched_as_it_is(self):
        line = (f"@@tpot loss backups Making room for the backup deletes 2 older backup(s) in {self.backups}: "
                "it's mine_tpot_backup.tar (3 MB), \"$(x)\" `y`_tpot_backup.tar (1 MB).")
        self.assertTrue(self.confirmed("backups", line, "it's mine_tpot_backup.tar"))
        self.assertTrue(self.confirmed("backups", line, '"$(x)" `y`_tpot_backup.tar'))
        self.assertTrue(self.confirmed("backups", line, "it's mine_tpot_backup.tar", '"$(x)" `y`_tpot_backup.tar'))
        for name in ("it", "it's", "its mine_tpot_backup.tar", "it's mine_tpot_backup.tar'"):
            with self.subTest(name):
                self.assertFalse(self.confirmed("backups", line, name))

    def test_an_image_is_confirmed_as_a_whole_entry_only(self):
        line = ("@@tpot loss images 2 image(s) built here cannot be pulled again: "
                "ghcr.io/x/cowrie:24.04.1, ghcr.io/x/dionaea:24.04.1")
        self.assertTrue(self.confirmed("images", line, "ghcr.io/x/cowrie:24.04.1"))
        self.assertTrue(self.confirmed("images", line, "ghcr.io/x/dionaea:24.04.1"))     # the last entry
        self.assertTrue(self.confirmed("images", line, "ghcr.io/x/cowrie:24.04.1", "ghcr.io/x/dionaea:24.04.1"))
        for name in ("ghcr.io/x/cowrie:24.04", "ghcr.io/x/cowrie:24", "ghcr.io/x/dionaea:24.04", "cowrie:24.04.1",
                     "ghcr.io/x/cow"):
            with self.subTest(name):
                self.assertFalse(self.confirmed("images", line, name))
        self.assertFalse(self.confirmed("images", line, "ghcr.io/x/cowrie:24.04.1", "ghcr.io/x/cowrie:24.04"))

    def test_a_backup_is_confirmed_as_a_whole_entry_only(self):
        line = (f"@@tpot loss backups Making room for the backup deletes 3 older backup(s) in {self.backups}: "
                "20261010_tpot_backup.tar (3 MB), my backup.tar (1 MB), 20261011_tpot_backup.tar (4 MB).")
        self.assertTrue(self.confirmed("backups", line, "20261010_tpot_backup.tar"))
        self.assertTrue(self.confirmed("backups", line, "my backup.tar"))                  # a space in the name
        self.assertTrue(self.confirmed("backups", line, "20261011_tpot_backup.tar"))       # the last, full stop
        for name in ("20261010_tpot_backup", "20261010_tpot_backup.ta", "backup.tar", "my", "20261011_tpot_backup.tar."):
            with self.subTest(name):
                self.assertFalse(self.confirmed("backups", line, name))

    def test_local_images_the_file_names_go(self):
        line = "@@tpot loss images 1 image(s) built here cannot be pulled again: ghcr.io/telekom-security/mine:24.04.1"
        result = self.images(extra=self.accept_file(line))
        rmi = [entry for entry in (result.stdout + result.stderr).splitlines() if entry.startswith("RMI: ")]
        self.assertEqual(len(rmi), 1, result.stdout)
        self.assertIn("ghcr.io/telekom-security/mine:24.04.1", rmi[0])

    def test_local_images_the_file_does_not_name_stay(self):
        line = "@@tpot loss images 1 image(s) built here cannot be pulled again: ghcr.io/telekom-security/other:24.04.1"
        result = self.images(extra=self.accept_file(line))
        out = result.stdout + result.stderr
        self.assertEqual([entry for entry in out.splitlines() if entry.startswith("RMI: ")],
                         ["RMI: ghcr.io/telekom-security/cowrie:24.04.1"])
        self.assertIn("went on", out)

    def test_restart_hands_the_file_on(self):
        self.assertEqual(self.restart_args(read("update.sh"), "-F", "--accept-data-loss-file", "/run/c"),
                         ["-F", "--accept-data-loss-file", "/run/c"])

    def test_restart_into_an_older_update_sh_drops_the_file(self):
        older = 'while getopts ":yFsob:r:B:h" opt; do\n      --accept-data-loss) myACCEPT_LOSS=1 ;;\n'
        self.assertEqual(self.restart_args(older, "-F", "--accept-data-loss-file", "/run/c", "-s"), ["-F", "-s"])

    def test_the_option_needs_a_readable_file(self):
        for args in (["--accept-data-loss-file"], ["--accept-data-loss-file", os.path.join(self.home, "none")]):
            with self.subTest(args=args):
                result = self.run_script(os.path.join(REPO, "update.sh"), "-y", *args)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertNotIn("@@tpot phase", result.stdout)

    def test_selfupdate_gets_the_file(self):
        main = self.main_section()
        self.assertIn("--accept-data-loss-file) myACCEPT_FILE_NEXT=1", main)
        self.assertIn('myLOSS_ARGS+=(--accept-data-loss-file "${myACCEPT_FILE}")', main)
        self.assertIn('fuSELFUPDATE "$@" ${myLOSS_ARGS[@]+"${myLOSS_ARGS[@]}"}', main)


class OldUpdaterConfigTest(Harness):
    """The update.sh of 24.04.1 wrote $HOME/*_tpot_backup.tgz, ran `git reset --hard` and restarted into
    this one without a handover: .env and docker-compose.yml are the files of the release, the user's
    are only in that archive. fuOLD_UPDATER_CONFIG puts them back before the run checks or writes
    anything, and only when the archive is fresh, names an older version and the .env here is the one
    of the checkout."""

    HELPERS = DataLossTest.HELPERS
    FUNCTIONS = ("fuENV_VALUE", "fuTMPDIR", "fuOLD_UPDATER_ARCHIVE", "fuOLD_UPDATER_NEWEST", "fuOLD_UPDATER_FRESH",
                 "fuOLD_UPDATER_READ", "fuOLD_UPDATER_FILE", "fuOLD_UPDATER_UNREADABLE", "fuARCHIVE_MEMBER", "fuARCHIVE_MEMBER_NAME", "fuARCHIVE_ENV",
                 "fuOLD_UPDATER_CONFIG")
    RELEASE_ENV = "TPOT_REPO=ghcr.io/telekom-security\nTPOT_VERSION=24.04.2\nWEB_USER=\n"
    RELEASE_COMPOSE = "# T-Pot: STANDARD\nservices:\n  tpotinit:\n    image: x\n"
    USER_ENV = "TPOT_REPO=dtagdevsec\nTPOT_VERSION=24.04.1\nWEB_USER=dXNlcg==\n"
    USER_COMPOSE = "# T-Pot: MINI\nservices:\n  mine:\n    image: y\n"

    def setUp(self):
        super().setUp()
        self.tpotce = os.path.join(self.home, "tpotce")
        os.makedirs(self.tpotce)
        self.commit(self.RELEASE_ENV)

    def commit(self, env):
        """~/tpotce as after the reset: .env, docker-compose.yml and version of the release, committed."""
        for name, text in ((".env", env), ("docker-compose.yml", self.RELEASE_COMPOSE), ("version", "24.04.2\n")):
            self.put(name, text)
        git = ["git", "-C", self.tpotce, "-c", "user.email=t@t", "-c", "user.name=t"]
        if not os.path.isdir(os.path.join(self.tpotce, ".git")):
            subprocess.run(["git", "init", "-q", self.tpotce], check=True)
        subprocess.run(git + ["add", "-A"], check=True)
        subprocess.run(git + ["commit", "-q", "--allow-empty", "-m", "release"], check=True)

    def put(self, name, text):
        with open(os.path.join(self.tpotce, name), "w", encoding="utf-8", newline="") as out:
            out.write(text)

    def get(self, name):
        with open(os.path.join(self.tpotce, name), encoding="utf-8", newline="") as handle:
            return handle.read()

    def archive(self, env=None, compose=None, prefix="", age=0):
        """The archive of the update.sh of 24.04.1: `tar cvf` of `* .env` in ~/tpotce, plain tar despite
        the name; compose=False leaves docker-compose.yml out."""
        path = os.path.join(self.home, "20261013090000_tpot_backup.tgz")
        source = os.path.join(self.home, "src")
        shutil.rmtree(source, ignore_errors=True)
        os.makedirs(os.path.join(source, "data"))
        files = [("data/uuid", "u\n"), (".env", self.USER_ENV if env is None else env)]
        if compose is not False:
            files.insert(0, ("docker-compose.yml", self.USER_COMPOSE if compose is None else compose))
        with tarfile.open(path, "w") as archive:
            for name, text in files:
                with open(os.path.join(source, name), "w", encoding="utf-8", newline="") as out:
                    out.write(text)
                archive.add(os.path.join(source, name), arcname=prefix + name)
        if age:
            os.utime(path, (time.time() - age, time.time() - age))
        return path

    def run_config(self, extra=""):
        script = (f'eval "$(sed -n \'/^fuUI_VERSION_GE () {{/,/^}}/p\' {REPO}/installer/lib/ui.sh)"\n'
                  + extra + DataLossTest.functions(*self.FUNCTIONS)
                  + 'trap \'[ -z "${myTMPDIR}" ] || rm -rf "${myTMPDIR}"\' EXIT\n'
                  + 'fuOLD_UPDATER_CONFIG\necho "rc=$?"\n')
        environment = dict(os.environ, HOME=self.home, PATH=f"{self.bin}:{os.environ['PATH']}", TPOT_GUM="off")
        environment.pop("TPOT_MARKS", None)
        result = subprocess.run(["bash", "-c", self.HELPERS + script], cwd=REPO, capture_output=True,
                                universal_newlines=True, env=environment, stdin=subprocess.DEVNULL, timeout=60)
        self.rc = result.returncode
        return result.stdout + result.stderr

    def assertReleaseFiles(self, out):
        self.assertEqual(self.get(".env"), self.RELEASE_ENV, out)
        self.assertEqual(self.get("docker-compose.yml"), self.RELEASE_COMPOSE, out)
        self.assertNotIn("is back from", out)

    def test_both_files_come_back(self):
        archive = self.archive()
        out = self.run_config()
        self.assertIn("rc=0", out)
        self.assertEqual(self.get(".env"), self.USER_ENV, out)
        self.assertEqual(self.get("docker-compose.yml"), self.USER_COMPOSE, out)
        text = f"Your configuration of 24.04.1 is back from {archive} (the earlier updater had reset it)."
        self.assertIn(f"OK: {text}", out)
        self.assertIn(f"DID: ok: {text}", out)
        self.assertIn("HINT: Put back: .env and docker-compose.yml. Other files you changed in ~/tpotce are in "
                      "that archive as well.", out)

    def test_an_archive_older_than_a_day_is_not_taken(self):
        archive = self.archive(age=25 * 3600)
        out = self.run_config()
        self.assertReleaseFiles(out)
        self.assertIn(f"WARN: {self.tpotce}/.env is the one of the checkout, without a web user. If your "
                      f"configuration is in {archive}, put it back with: tar xOf {archive} .env > ~/tpotce/.env",
                      out)

    def test_an_archive_of_the_same_version_is_not_taken(self):
        archive = self.archive(env=self.USER_ENV.replace("24.04.1", "24.04.2"))
        out = self.run_config()
        self.assertReleaseFiles(out)
        self.assertIn(f"tar xOf {archive} .env > ~/tpotce/.env", out)

    def test_a_changed_env_is_not_replaced(self):
        for name, env in (("a SENSOR, no web user", self.RELEASE_ENV + "TPOT_TYPE=SENSOR\n"),
                          ("a web user", self.RELEASE_ENV.replace("WEB_USER=\n", "WEB_USER=bWluZQ==\n"))):
            with self.subTest(name):
                self.archive()
                self.put(".env", env)
                out = self.run_config()
                self.assertEqual(self.get(".env"), env, out)
                self.assertEqual(self.get("docker-compose.yml"), self.RELEASE_COMPOSE, out)
                self.assertNotIn("is back from", out)
                self.assertNotIn("WARN", out)          # a .env of its own is no sign of the reset

    def test_the_warning_only_without_a_web_user(self):
        self.commit(self.RELEASE_ENV.replace("WEB_USER=\n", "WEB_USER=cmVsZWFzZQ==\n"))
        self.archive(age=25 * 3600)
        out = self.run_config()
        self.assertNotIn("is back from", out)
        self.assertNotIn("WARN", out)

    def test_no_warning_when_the_archive_holds_the_same_env(self):
        self.archive(env=self.RELEASE_ENV, age=25 * 3600)
        out = self.run_config()
        self.assertNotIn("WARN", out)

    def test_the_restarted_run_takes_nothing(self):
        self.archive()
        out = self.run_config(extra="myPREPARED=1\n")
        self.assertReleaseFiles(out)
        self.assertNotIn("WARN", out)

    def test_no_archive_changes_nothing(self):
        out = self.run_config()
        self.assertReleaseFiles(out)
        self.assertNotIn("WARN", out)
        self.assertIn("rc=0", out)

    def test_members_with_a_leading_dot_slash(self):
        archive = self.archive(prefix="./")
        out = self.run_config()
        self.assertEqual(self.get(".env"), self.USER_ENV, out)
        self.assertEqual(self.get("docker-compose.yml"), self.USER_COMPOSE, out)
        self.assertIn(f"back from {archive}", out)

    def test_a_quoted_version_and_a_cr(self):
        for env in (self.USER_ENV.replace("TPOT_VERSION=24.04.1", 'TPOT_VERSION="24.04.1"'),
                    self.USER_ENV.replace("\n", "\r\n")):
            with self.subTest(env=env):
                self.commit(self.RELEASE_ENV)
                self.archive(env=env)
                out = self.run_config()
                self.assertEqual(self.get(".env"), env, out)
                self.assertIn("Your configuration of 24.04.1 is back", out)

    def test_mode_and_file_stay(self):
        os.chmod(os.path.join(self.tpotce, ".env"), 0o600)
        inode = os.stat(os.path.join(self.tpotce, ".env")).st_ino
        self.archive()
        out = self.run_config()
        self.assertEqual(self.get(".env"), self.USER_ENV, out)
        self.assertEqual(os.stat(os.path.join(self.tpotce, ".env")).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(os.path.join(self.tpotce, ".env")).st_ino, inode)

    def test_a_changed_compose_file_stays(self):
        self.archive()
        self.put("docker-compose.yml", "# mine since the reset\n")
        out = self.run_config()
        self.assertEqual(self.get(".env"), self.USER_ENV, out)
        self.assertEqual(self.get("docker-compose.yml"), "# mine since the reset\n")
        self.assertIn("HINT: Put back: .env. Other files", out)

    def test_an_archive_without_a_compose_file(self):
        self.archive(compose=False)
        out = self.run_config()
        self.assertEqual(self.get(".env"), self.USER_ENV, out)
        self.assertEqual(self.get("docker-compose.yml"), self.RELEASE_COMPOSE)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root writes a read-only file")
    def test_a_failed_write_puts_back_what_was_written_and_a_rerun_completes(self):
        """docker-compose.yml cannot be written: .env, written before it, is the one of the checkout again (in
        place), the run stops; the next run takes both."""
        archive = self.archive()
        compose = os.path.join(self.tpotce, "docker-compose.yml")
        inode = os.stat(os.path.join(self.tpotce, ".env")).st_ino
        os.chmod(compose, 0o444)
        self.addCleanup(os.chmod, compose, 0o644)
        out = self.run_config()
        self.assertEqual(self.rc, 1, out)
        self.assertNotIn("rc=0", out)
        self.assertReleaseFiles(out)
        self.assertEqual(os.stat(os.path.join(self.tpotce, ".env")).st_ino, inode)
        self.assertIn(f"ERROR: Could not put docker-compose.yml back from {archive}", out)
        self.assertIn("HINT: .env is the one of the checkout again.", out)
        self.assertIn(f"DID: fail: docker-compose.yml could not be put back from {archive}", out)
        os.chmod(compose, 0o644)
        out = self.run_config()
        self.assertIn("rc=0", out)
        self.assertEqual(self.get(".env"), self.USER_ENV, out)
        self.assertEqual(self.get("docker-compose.yml"), self.USER_COMPOSE, out)
        self.assertIn("HINT: Put back: .env and docker-compose.yml.", out)

    def test_a_rerun_with_the_env_of_the_archive_takes_the_compose_file(self):
        """A run before this one put .env back and stopped before docker-compose.yml: .env is the one of the
        archive (not of the checkout), the compose file still the one of the checkout."""
        archive = self.archive()
        self.put(".env", self.USER_ENV)
        out = self.run_config()
        self.assertIn("rc=0", out)
        self.assertEqual(self.get(".env"), self.USER_ENV, out)
        self.assertEqual(self.get("docker-compose.yml"), self.USER_COMPOSE, out)
        self.assertIn(f"OK: Your configuration of 24.04.1 is back from {archive}", out)
        self.assertIn("HINT: Put back: docker-compose.yml. Other files", out)
        self.assertNotIn("WARN", out)

    def test_both_in_place_already_writes_nothing(self):
        archive = self.archive()
        self.put(".env", self.USER_ENV)
        self.put("docker-compose.yml", self.USER_COMPOSE)
        out = self.run_config()
        self.assertIn("rc=0", out)
        self.assertIn(f"OK: Your configuration of 24.04.1 from {archive} is in place already.", out)
        self.assertNotIn("Put back:", out)
        self.assertNotIn("DID:", out)

    def stub(self, name, text):
        path = os.path.join(self.bin, name)
        with open(path, "w", encoding="utf-8") as out:
            out.write(text)
        os.chmod(path, 0o755)

    def test_a_broken_extract_stops_before_anything_is_written(self):
        """tar xf stops half way (a full disk, an I/O error): what it left is no file to take, the run stops
        as for a member it cannot read and changes nothing."""
        archive = self.archive()
        real = shutil.which("tar")
        self.stub("tar", '#!/bin/sh\ncase "$1" in\n  xf)\n    while [ $# -gt 0 ]; do [ "$1" = "-C" ] && myDIR="$2"; shift; done\n'
                         '    printf "TPOT_REPO=dtag" > "$myDIR/.env"; printf "# T-Pot" > "$myDIR/docker-compose.yml"\n'
                         f'    exit 1 ;;\nesac\nexec {real} "$@"\n')
        out = self.run_config()
        self.assertEqual(self.rc, 1, out)
        self.assertReleaseFiles(out)
        self.assertIn(f"ERROR: Could not read .env from {archive}", out)
        self.assertIn("HINT: Nothing was changed.", out)
        self.assertIn("DID: fail: .env could not be read from", out)

    def test_a_broken_extract_of_an_archive_that_does_not_count_changes_nothing(self):
        self.archive(age=25 * 3600)
        real = shutil.which("tar")
        self.stub("tar", f'#!/bin/sh\n[ "$1" = xf ] && exit 1\nexec {real} "$@"\n')
        out = self.run_config()
        self.assertIn("rc=0", out)
        self.assertReleaseFiles(out)
        self.assertNotIn("ERROR", out)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root writes a read-only file")
    def test_a_rollback_that_fails_says_so_and_how(self):
        """The compose file cannot be written and .env, written before it, cannot be put back either (git show
        fails): no claim that it is the one of the checkout, the way to put it back by hand instead."""
        archive = self.archive()
        compose = os.path.join(self.tpotce, "docker-compose.yml")
        os.chmod(compose, 0o444)
        self.addCleanup(os.chmod, compose, 0o644)
        real = shutil.which("git")
        self.stub("git", f'#!/bin/sh\nfor myA; do [ "$myA" = show ] && exit 1; done\nexec {real} "$@"\n')
        out = self.run_config()
        self.assertEqual(self.rc, 1, out)
        self.assertEqual(self.get(".env"), self.USER_ENV, out)          # written, and could not go back
        self.assertNotIn("is the one of the checkout again", out)
        self.assertIn(f"ERROR: Could not put docker-compose.yml back from {archive}", out)
        self.assertIn("HINT: .env could not be put back to the one of the checkout: "
                      "git -C ~/tpotce checkout HEAD -- .env", out)
        self.assertIn("DID: fail: .env could not be put back to the one of the checkout: "
                      "git -C ~/tpotce checkout HEAD -- .env", out)

    def test_a_compose_file_changed_after_the_reset_is_named_as_kept(self):
        """.env is back already, the compose file is neither the one of the checkout nor of the archive: it is
        not "in place", it stays as it is and the text says so."""
        archive = self.archive()
        self.put(".env", self.USER_ENV)
        self.put("docker-compose.yml", "# mine since the reset\n")
        out = self.run_config()
        self.assertIn("rc=0", out)
        self.assertNotIn("Your configuration of 24.04.1 from", out)
        self.assertIn("docker-compose.yml was changed after the reset, it stays as it is (the archive has the one "
                      "of 24.04.1).", out)
        self.assertIn(f"Your .env of 24.04.1 from {archive} is in place already.", out)
        self.assertEqual(self.get("docker-compose.yml"), "# mine since the reset\n")
        # with .env taken, the same note for the compose file
        self.commit(self.RELEASE_ENV)
        self.put("docker-compose.yml", "# mine since the reset\n")
        out = self.run_config()
        self.assertIn("HINT: Put back: .env.", out)
        self.assertIn("docker-compose.yml was changed after the reset, it stays as it is (the archive has the one "
                      "of 24.04.1).", out)

    def test_a_member_that_is_no_file_stops_before_anything_is_written(self):
        """Both files are read from the archive before either is written: a docker-compose.yml that is a link
        leaves .env as well as it is."""
        path = os.path.join(self.home, "20261013090000_tpot_backup.tgz")
        source = os.path.join(self.home, "src")
        os.makedirs(source)
        with open(os.path.join(source, ".env"), "w", encoding="utf-8") as out:
            out.write(self.USER_ENV)
        os.symlink("/etc/hosts", os.path.join(source, "docker-compose.yml"))
        with tarfile.open(path, "w") as archive:
            archive.add(os.path.join(source, "docker-compose.yml"), arcname="docker-compose.yml")
            archive.add(os.path.join(source, ".env"), arcname=".env")
        out = self.run_config()
        self.assertEqual(self.rc, 1, out)
        self.assertReleaseFiles(out)
        self.assertIn(f"ERROR: Could not read docker-compose.yml from {path}", out)
        self.assertIn("HINT: Nothing was changed.", out)

    def test_the_archive_is_listed_once_and_read_once(self):
        """One tar tf and one tar xf of both files for the whole run, fuOLD_RELEASE_BACKUP of the same
        archive included."""
        real = shutil.which("tar")
        calls = os.path.join(self.home, "tar_calls")
        with open(os.path.join(self.bin, "tar"), "w", encoding="utf-8") as out:
            out.write(f'#!/bin/sh\necho "$*" >> "{calls}"\nexec {real} "$@"\n')
        os.chmod(os.path.join(self.bin, "tar"), 0o755)
        archive = self.archive()
        os.makedirs(os.path.join(self.home, "src", "data", "elk", "data"))
        with tarfile.open(archive, "a") as handle:
            handle.add(os.path.join(self.home, "src", "data", "elk"), arcname="data/elk")
        extra = DataLossTest.functions("fuOLD_RELEASE_BACKUP")
        script = (f'eval "$(sed -n \'/^fuUI_VERSION_GE () {{/,/^}}/p\' {REPO}/installer/lib/ui.sh)"\n'
                  + DataLossTest.functions(*self.FUNCTIONS) + extra
                  + 'trap \'[ -z "${myTMPDIR}" ] || rm -rf "${myTMPDIR}"\' EXIT\n'
                  + f'fuOLD_UPDATER_CONFIG\necho "FOUND=$(fuOLD_RELEASE_BACKUP "{self.tpotce}/data" 24.04.1)"\n')
        environment = dict(os.environ, HOME=self.home, PATH=f"{self.bin}:{os.environ['PATH']}", TPOT_GUM="off")
        result = subprocess.run(["bash", "-c", self.HELPERS + script], cwd=REPO, capture_output=True,
                                universal_newlines=True, env=environment, stdin=subprocess.DEVNULL, timeout=60)
        out = result.stdout + result.stderr
        self.assertIn(f"FOUND={archive}\n", out)
        self.assertEqual(self.get(".env"), self.USER_ENV, out)
        with open(calls, encoding="utf-8") as handle:
            named = [line for line in handle.read().splitlines() if archive in line]
        self.assertLessEqual(len(named), 2, named)
        self.assertEqual([line.split()[0] for line in named], ["tf", "xf"], named)

    def test_it_runs_first_in_both_paths(self):
        main = read("update.sh").split("################\n# Main section #\n################", 1)[1]
        start = main.index("\nfuLOG_START\n")
        only = main.index("fuMARK phase check Checking the edition and the space")
        self.assertLess(start, only)
        self.assertLess(main.index("fuOLD_UPDATER_CONFIG", only), main.index("fuCHECK_EDITION", only))
        update = main.index("fuMARK phase check Checking the version and the source")
        first = main.index("\nfuOLD_UPDATER_CONFIG\n", update)
        self.assertLess(start, first)
        self.assertLess(first, main.index("\nfuCHECK_VERSION\n", update))
        self.assertLess(first, main.index("\nfuBACKUP\n", update))

    # --- the update.sh of 2026-08-24 on master: ~/tpot_backups/*_tpot_backup*.tar, no handover -------

    def tar_archive(self, env=None, compose=None, name="20261014080000_tpot_backup.tar", age=0):
        """The archive of the update.sh of 2026-08-24 (a1f2c659 to a7099cec): one tar of its stage in
        ~/tpot_backups, MANIFEST rollback.txt env tracked.patch docker-compose.yml, then the files of
        data/ it keeps; .env is the member env. It restarted into this script without a handover."""
        env = self.USER_ENV if env is None else env
        version = re.search(r"^TPOT_VERSION=(.*)$", env, re.M)
        manifest = ("T-Pot backup\nCreated:      2026-10-14 08:00:00 +0200\nHostname:     tpot\n"
                    "Edition:      MINI\nTPOT_TYPE:    \n"
                    f"TPOT_VERSION: {version.group(1) if version else ''}\n"
                    "Commit:       0123456789abcdef (master)\n"
                    "Repository:   https://github.com/telekom-security/tpotce\n\n"
                    "Restore with 'restore.sh -f <this archive>'.\nTo go back to the commit before the update:\n"
                    "  cd ~/tpotce && git reset --hard $(cat rollback.txt)\n")
        members = {"MANIFEST": manifest, "rollback.txt": "0123456789abcdef\n", "env": env, "tracked.patch": ""}
        if compose is not False:
            members["docker-compose.yml"] = self.USER_COMPOSE if compose is None else compose
        members["data/uuid"] = "u\n"
        path = make_backup(self.home, members, name=name)
        if age:
            os.utime(path, (time.time() - age, time.time() - age))
        return path

    def test_n1_takes_a_fresh_tar_of_the_2026_08_24_updater(self):
        archive = self.tar_archive()
        out = self.run_config()
        self.assertIn("rc=0", out)
        self.assertEqual(self.get(".env"), self.USER_ENV, out)
        self.assertEqual(self.get("docker-compose.yml"), self.USER_COMPOSE, out)
        text = f"Your configuration of 24.04.1 is back from {archive} (the earlier updater had reset it)."
        self.assertIn(f"OK: {text}", out)
        self.assertIn(f"DID: ok: {text}", out)
        self.assertIn("HINT: Put back: .env and docker-compose.yml.", out)
        # without a compose file in it, .env alone
        self.commit(self.RELEASE_ENV)
        os.remove(archive)
        self.tar_archive(compose=False)
        out = self.run_config()
        self.assertEqual(self.get(".env"), self.USER_ENV, out)
        self.assertEqual(self.get("docker-compose.yml"), self.RELEASE_COMPOSE, out)
        self.assertIn("HINT: Put back: .env. Other files", out)

    def test_n1_an_old_tar_is_not_opened_nor_named(self):
        """~/tpot_backups keeps archives of every age: a tar older than a day is never listed or
        extracted (a large --full one would cost two scans at every update) and no hint names it. A
        stale tgz of the tag 24.04.1 next to it still gets its hint, as before."""
        real = shutil.which("tar")
        calls = os.path.join(self.home, "tar_calls")
        with open(os.path.join(self.bin, "tar"), "w", encoding="utf-8") as out:
            out.write(f'#!/bin/sh\necho "$*" >> "{calls}"\nexec {real} "$@"\n')
        os.chmod(os.path.join(self.bin, "tar"), 0o755)
        archive = self.tar_archive(age=3 * 24 * 3600)
        out = self.run_config()
        self.assertIn("rc=0", out)
        self.assertReleaseFiles(out)
        self.assertNotIn(archive, out)
        self.assertNotIn("WARN", out)
        named = []
        if os.path.exists(calls):
            with open(calls, encoding="utf-8") as handle:
                named = [line for line in handle.read().splitlines() if archive in line]
        self.assertEqual(named, [])
        # the older tgz of the tag 24.04.1 keeps its hint
        tgz = self.archive(age=4 * 24 * 3600)
        out = self.run_config()
        self.assertReleaseFiles(out)
        self.assertNotIn(archive, out)
        self.assertIn(f"tar xOf {tgz} .env > ~/tpotce/.env", out)

    def test_n1_prefers_the_newer_of_tgz_and_tar(self):
        tgz_env = self.USER_ENV.replace("WEB_USER=dXNlcg==", "WEB_USER=dGd6")
        tar_env = self.USER_ENV.replace("WEB_USER=dXNlcg==", "WEB_USER=dGFy")
        for newer in ("tar", "tgz"):
            with self.subTest(newer=newer):
                self.commit(self.RELEASE_ENV)
                shutil.rmtree(os.path.join(self.home, "tpot_backups"), ignore_errors=True)
                tgz = self.archive(env=tgz_env, age=0 if newer == "tgz" else 3600)
                tar = self.tar_archive(env=tar_env, age=0 if newer == "tar" else 3600)
                out = self.run_config()
                self.assertIn("rc=0", out)
                self.assertEqual(self.get(".env"), tar_env if newer == "tar" else tgz_env, out)
                self.assertIn(f"is back from {tar if newer == 'tar' else tgz}", out)
        # a tgz of the tag 24.04.1 that is older than a day does not hide a fresh tar
        self.commit(self.RELEASE_ENV)
        shutil.rmtree(os.path.join(self.home, "tpot_backups"))
        self.archive(env=tgz_env, age=30 * 3600)
        tar = self.tar_archive(env=tar_env)
        out = self.run_config()
        self.assertEqual(self.get(".env"), tar_env, out)
        self.assertIn(f"is back from {tar}", out)

    def test_n1_tar_with_a_newer_env_version_is_left_alone(self):
        """The newest tar in ~/tpot_backups is often one of this release (or a later one): its .env names
        the version of the checkout or a newer one, nothing is taken from it."""
        for version in ("24.04.2", "24.04.3"):
            with self.subTest(version=version):
                self.commit(self.RELEASE_ENV)
                shutil.rmtree(os.path.join(self.home, "tpot_backups"), ignore_errors=True)
                archive = self.tar_archive(env=self.USER_ENV.replace("24.04.1", version))
                out = self.run_config()
                self.assertIn("rc=0", out)
                self.assertReleaseFiles(out)
                self.assertIn(f"tar xOf {archive} env > ~/tpotce/.env", out)
        # nor from a fresh one of 24.04.1 while the restarted run of a handover runs
        self.commit(self.RELEASE_ENV)
        self.tar_archive()
        out = self.run_config(extra="myPREPARED=1\n")
        self.assertReleaseFiles(out)


class UpdateRepairTest(Harness):
    """The steps of update.sh that used to lose something: a backup that keeps binary changes and the data
    path, a branch switch that keeps a ref, no image removal after a failed pull, side files that belong to
    the user, the log of the last run, the SENSOR edition over a compose file of your own, git and file
    reads that always go to ~/tpotce."""

    HELPERS = DataLossTest.HELPERS
    JEWELS = f'eval "$(sed -n \'/^myJEWELS="/,/cert"$/p\' {REPO}/update.sh)"\n'

    @staticmethod
    def functions(*names):
        """The functions of update.sh, whatever the current folder is."""
        return DataLossTest.functions(*names).replace("' update.sh)", f"' {REPO}/update.sh)")

    def setUp(self):
        super().setUp()
        self.tpotce = os.path.join(self.home, "tpotce")
        os.makedirs(self.tpotce)
        self.tmp = os.path.join(self.home, "tmp")
        os.makedirs(self.tmp)
        self.elsewhere = os.path.join(self.home, "elsewhere")
        os.makedirs(self.elsewhere)

    def stub(self, name, text):
        self.write(os.path.join(self.bin, name), text)
        os.chmod(os.path.join(self.bin, name), 0o755)

    def write(self, path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as out:
            out.write(text)

    def read(self, path):
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def bash(self, script, cwd=None):
        environment = dict(os.environ, HOME=self.home, PATH=f"{self.bin}:{os.environ['PATH']}", TPOT_GUM="off",
                           TMPDIR=self.tmp, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                           GIT_COMMITTER_EMAIL="t@t", GIT_CONFIG_NOSYSTEM="1")
        environment.pop("TPOT_MARKS", None)
        return subprocess.run(["bash", "-c", self.HELPERS + script], cwd=cwd or REPO, capture_output=True,
                              universal_newlines=True, env=environment, stdin=subprocess.DEVNULL, timeout=60)

    def git(self, *args, repo=None):
        return subprocess.run(["git", "-C", repo or self.tpotce, "-c", "user.email=t@t", "-c", "user.name=t"]
                              + list(args), capture_output=True, check=True, universal_newlines=True).stdout

    def clone_of_origin(self):
        """HOME/tpotce, a clone of an origin with the branches master and other, on master."""
        origin = os.path.join(self.home, "origin")
        subprocess.run(["git", "init", "-q", "-b", "master", origin], check=True)
        self.write(os.path.join(origin, "version"), read("version"))
        self.write(os.path.join(origin, "env.example"), "TPOT_VERSION=24.04.2\n")
        self.git("add", ".", repo=origin)
        self.git("commit", "-q", "-m", "one", repo=origin)
        self.git("branch", "other", repo=origin)
        shutil.rmtree(self.tpotce)
        subprocess.run(["git", "clone", "-q", origin, self.tpotce], check=True)

    def commit_mine(self):
        self.write(os.path.join(self.tpotce, "mine.txt"), "mine\n")
        self.git("add", "mine.txt")
        self.git("commit", "-q", "-m", "mine")
        return self.git("rev-parse", "HEAD").strip()

    # --- fuBACKUP: binary changes, the data path --------------------------------------------------

    def backup(self, custom=None, full=False):
        """fuBACKUP with a stub tar that records its arguments and keeps a copy of the staging folder."""
        self.stub("tar", '#!/bin/sh\nif [ "$1" = cf ]; then\n  echo "$*" >> "$HOME/tar_calls"\n  : > "$2"\n'
                         '  while [ $# -gt 0 ]; do [ "$1" = "-C" ] && { cp -R "$2" "$HOME/staged"; break; }; shift; done\n'
                         'elif [ "$1" = tf ]; then echo MANIFEST; fi\nexit 0\n')
        self.stub("hostname", "#!/bin/sh\necho test\n")
        self.write(os.path.join(self.tpotce, ".env"), "TPOT_TYPE=HIVE\nTPOT_VERSION=24.04.2\n")
        data = custom or os.path.join(self.tpotce, "data")
        for jewel in ("uuid", "nginx/cert/nginx.crt"):
            self.write(os.path.join(data, jewel), "x\n")
        script = (self.JEWELS + self.functions("fuBACKUP", "fuENV_VALUE", "fuDATA_PATH")
                  + f'fuTMPDIR () {{ myTMPDIR=$(mktemp -d); }}; fuARCHIVE_NAME () {{ echo "{self.home}/a.tar"; }}\n'
                  + f'fuROTATE () {{ :; }}; myEDITION=STANDARD myFULL={"1" if full else ""}\n'
                  + (f'fuDATA_PATH () {{ echo "{custom}"; }}\n' if custom else '') + 'fuBACKUP\n')
        return self.bash(script)

    def tar_calls(self):
        path = os.path.join(self.home, "tar_calls")
        text = self.read(path)
        os.remove(path)
        shutil.rmtree(os.path.join(self.home, "staged"))
        return text

    def test_backup_patch_is_binary(self):
        self.clone_of_origin()
        self.write(os.path.join(self.tpotce, "pic.bin"), "one\n")
        self.git("add", "pic.bin")
        self.git("commit", "-q", "-m", "pic")
        with open(os.path.join(self.tpotce, "pic.bin"), "wb") as out:
            out.write(bytes(range(256)))                                # a changed binary file
        result = self.backup()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        patch = os.path.join(self.home, "staged", "tracked.patch")
        self.assertIn("GIT binary patch", self.read(patch))
        self.git("checkout", "pic.bin")
        self.git("apply", "--check", patch)                             # and it applies again

    def test_jewels_and_full_follow_the_data_path(self):
        self.clone_of_origin()
        custom = os.path.join(self.home, "mnt", "tpotdata")
        result = self.backup(custom=custom)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        call = self.tar_calls()
        self.assertIn(f"-C {custom} ./uuid ./nginx/cert", call)
        self.assertIn("--transform flags=rh;s,^\\./,data/,", call)       # they land in the archive as data/...,
        #                                                               # h: hard link targets follow, symlink targets stay
        self.assertNotIn(f"{self.tpotce}/data", call)
        result = self.backup(custom=custom, full=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        call = self.tar_calls()
        self.assertIn(f"-C {custom} .", call)
        self.assertIn("--transform flags=rh;s,^\\./,data/,", call)
        self.assertNotIn("./uuid", call)

    def test_jewels_stay_in_data_for_the_default_path(self):
        self.clone_of_origin()
        result = self.backup()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        call = self.tar_calls()
        self.assertIn(f"-C {self.tpotce} data/uuid data/nginx/cert", call)
        self.assertNotIn("--transform", call)

    def test_backup_size_follows_the_data_path(self):
        custom = os.path.join(self.home, "mnt", "tpotdata")
        for jewel in ("uuid", "nginx/cert/nginx.crt"):
            self.write(os.path.join(custom, jewel), "x\n")
        self.write(os.path.join(self.tpotce, "data", "uuid"), "x\n")
        self.stub("du", '#!/bin/sh\necho "$*" >> "$HOME/du_calls"\nprintf "1\\ttotal\\n"\n')
        base = (self.JEWELS + self.functions("fuBACKUP_SIZE")
                + f'fuDATA_PATH () {{ echo "{custom}"; }}\nmyTMPDIR={self.tmp}\n')
        for full in ("", "1"):
            with self.subTest(full=bool(full)):
                result = self.bash(base + f"myFULL={full}\nfuBACKUP_SIZE\n")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                calls = self.read(os.path.join(self.home, "du_calls"))
                os.remove(os.path.join(self.home, "du_calls"))
                self.assertNotIn(f"{self.tpotce}/data", calls)
                if full:
                    self.assertIn(custom, calls)
                else:
                    self.assertIn(f"{custom}/uuid {custom}/nginx/cert", calls)

    # --- fuSWITCH_SOURCE: a ref for commits origin does not have -----------------------------------

    def switch(self, before=""):
        origin = os.path.join(self.home, "origin")
        script = (self.functions("fuSWITCH_SOURCE") + before
                  + f'myTPOT_SOURCE_GIVEN=1 myTPOT_REPO_URL="{origin}" myCURRENT_REPO="{origin}" '
                  'myTPOT_BRANCH=master myCURRENT_BRANCH=other myARCHIVE=/b/x_tpot_backup.tar\n'
                  'fuSWITCH_SOURCE\necho "rc=$?"\n')
        return self.bash(script, cwd=self.elsewhere)

    def test_switch_source_keeps_a_backup_ref(self):
        self.clone_of_origin()
        mine = self.commit_mine()
        self.git("checkout", "-q", "other")
        result = self.switch()
        out = result.stdout + result.stderr
        self.assertIn("rc=0", out)
        refs = [line for line in self.git("for-each-ref", "--format=%(refname) %(objectname)",
                                          "refs/tpot-backup/").split("\n") if line]
        self.assertEqual(len(refs), 1, refs)
        self.assertRegex(refs[0], r"^refs/tpot-backup/[0-9]{14}-[0-9]+/master " + mine + "$")
        warns = [line for line in out.splitlines() if line.startswith("WARN:")]
        self.assertTrue(any(refs[0].split(" ")[0] in line for line in warns), out)    # the warning names the ref
        ref = refs[0].split(" ")[0]
        self.assertIn(f"DID: warn: Local commits of master kept as {ref}, git switch -C master {ref} brings them "
                      "back.", out)                                                   # in the summary too
        self.assertNotEqual(self.git("rev-parse", "master").strip(), mine)            # master is origin's now
        self.assertEqual(self.git("branch", "--show-current").strip(), "master")

    def test_switch_source_without_own_commits_keeps_no_ref(self):
        self.clone_of_origin()
        self.git("checkout", "-q", "other")
        result = self.switch()
        self.assertIn("rc=0", result.stdout + result.stderr)
        self.assertEqual(self.git("for-each-ref", "refs/tpot-backup/").strip(), "")
        self.assertNotIn("WARN", result.stdout)

    def test_switch_source_stops_when_the_ref_cannot_be_kept(self):
        self.clone_of_origin()
        self.commit_mine()
        self.git("checkout", "-q", "other")
        result = self.switch('git () { case " $* " in *" update-ref "*) return 1 ;; esac; command git "$@"; }\n')
        out = result.stdout + result.stderr
        self.assertNotIn("rc=0", out)
        self.assertEqual(self.git("branch", "--show-current").strip(), "other")       # nothing was switched
        self.assertIn("mine", self.git("log", "--format=%s", "master"))
        self.assertIn("T-Pot is stopped", out)                                        # and what to do about it
        self.assertIn("/b/x_tpot_backup.tar", out)
        self.assertIn("systemctl start tpot", out)

    def test_switch_source_ref_is_unique_within_a_second(self):
        self.stub("date", "#!/bin/sh\necho 20260101000000\n")
        self.clone_of_origin()
        for _ in range(2):
            self.git("checkout", "-q", "master")
            self.commit_mine()
            self.git("checkout", "-q", "other")
            self.assertIn("rc=0", (lambda r: r.stdout + r.stderr)(self.switch()))
        refs = [line for line in self.git("for-each-ref", "--format=%(refname)", "refs/tpot-backup/").split("\n")
                if line]
        self.assertEqual(len(set(refs)), 2, refs)

    # --- the pull, the removal of images -----------------------------------------------------------

    def updater(self, pull_rc):
        self.write(os.path.join(self.tpotce, ".env"), "TPOT_REPO=ghcr.io/x\nTPOT_VERSION=24.04.2\n")
        script = (self.functions("fuUPDATER")
                  + f'fuPULLIMAGES () {{ return {pull_rc}; }}; fuREMOVEOLDIMAGES () {{ echo "REMOVE-CALLED"; }}\n'
                  'newVERSION=24.04.2 myCOMPOSE_CUSTOMIZED="" myARCHIVE=/x.tar\nfuUPDATER\n')
        return self.bash(script)

    def test_no_image_removal_after_a_failed_pull(self):
        out = (lambda r: r.stdout + r.stderr)(self.updater(1))
        self.assertNotIn("REMOVE-CALLED", out)
        self.assertIn("Could not pull all images", out)

    def test_images_go_after_a_pull_that_worked(self):
        self.assertIn("REMOVE-CALLED", self.updater(0).stdout)

    # --- .env and its side files -------------------------------------------------------------------

    def test_env_side_files_are_not_overwritten(self):
        env = os.path.join(self.tpotce, ".env")
        self.write(env, "TPOT_TYPE=HIVE\nBEELZEBUB_LLM_MODEL=ollama\nBEELZEBUB_OLLAMA_MODEL=m1\n")
        self.write(os.path.join(self.tpotce, "env.example"), "TPOT_TYPE=HIVE\nNEW_KEY=1\n")
        mine = {name: f"mine {name}\n" for name in (".env.user", ".env.system", ".env.merge", ".env.beelzebub")}
        for name, text in mine.items():
            self.write(os.path.join(self.tpotce, name), text)
        script = (self.functions("fuENV_KEYS", "fuENV_VALUE", "fuMERGE_ENV_KEYS", "fuMIGRATE_BEELZEBUB_ENV")
                  + f'fuMIGRATE_BEELZEBUB_ENV "{env}"; fuMERGE_ENV_KEYS "{env}" "{self.tpotce}/env.example"\n')
        result = self.bash(script)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        text = self.read(env)
        self.assertIn("BEELZEBUB_LLM_PROVIDER", text)                   # both functions did their work
        self.assertIn("NEW_KEY=1", text)
        for name, kept in mine.items():
            self.assertEqual(self.read(os.path.join(self.tpotce, name)), kept, name)
        self.assertEqual(os.listdir(self.tmp), [])                      # and left nothing in TMPDIR

    def test_a_failed_mktemp_is_a_warning_naming_the_step(self):
        env = os.path.join(self.tpotce, ".env")
        self.write(env, "TPOT_TYPE=HIVE\nBEELZEBUB_LLM_MODEL=ollama\nBEELZEBUB_OLLAMA_MODEL=m1\n")
        self.write(os.path.join(self.tpotce, "env.example"), "TPOT_TYPE=HIVE\nNEW_KEY=1\n")
        self.stub("mktemp", "#!/bin/sh\nexit 1\n")
        script = (self.functions("fuENV_KEYS", "fuENV_VALUE", "fuMERGE_ENV_KEYS", "fuMIGRATE_BEELZEBUB_ENV")
                  + f'fuMIGRATE_BEELZEBUB_ENV "{env}"; fuMERGE_ENV_KEYS "{env}" "{self.tpotce}/env.example"\n')
        result = self.bash(script)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        warns = [line for line in result.stdout.splitlines() if line.startswith("WARN:")]
        self.assertEqual(len(warns), 2, warns)
        self.assertTrue(any("Beelzebub" in line for line in warns), warns)
        self.assertTrue(any("env.example" in line for line in warns), warns)
        # the summary at the end names them too, with the same text
        summary = [line for line in result.stdout.splitlines() if line.startswith("DID: warn: ")]
        self.assertEqual([line[len("DID: warn: "):] for line in summary],
                         [line[len("WARN: "):] for line in warns], result.stdout)
        self.assertEqual(self.read(env), "TPOT_TYPE=HIVE\nBEELZEBUB_LLM_MODEL=ollama\nBEELZEBUB_OLLAMA_MODEL=m1\n")

    # --- the log, the edition ----------------------------------------------------------------------

    def log_start(self, prepared=""):
        backups = os.path.join(self.home, "tpot_backups")
        self.write(os.path.join(backups, "update.log"), "the last run\n")
        script = (self.functions("fuLOG_START") + f'myBACKUPDIR="{backups}" myLOG="{backups}/update.log" '
                  f'myPREPARED="{prepared}"\nfuLOG_START\n')
        self.assertEqual(self.bash(script).returncode, 0)
        return backups

    def test_log_of_the_last_run_is_kept(self):
        backups = self.log_start()
        self.assertEqual(self.read(os.path.join(backups, "update.log.1")), "the last run\n")
        self.assertEqual(self.read(os.path.join(backups, "update.log")), "")

    def test_log_of_the_run_before_a_restart_stays(self):
        backups = self.log_start("1")                                   # the restarted script adds to it
        self.assertEqual(self.read(os.path.join(backups, "update.log")), "the last run\n")
        self.assertFalse(os.path.exists(os.path.join(backups, "update.log.1")))

    def test_unknown_sensor_edition_warns(self):
        self.write(os.path.join(self.tpotce, ".env"), "TPOT_TYPE=SENSOR\n")
        self.write(os.path.join(self.tpotce, "docker-compose.yml"), "services: {own: {}}\n")
        self.write(os.path.join(self.tpotce, "compose", "sensor.yml"), "# T-Pot: SENSOR\n")
        script = (self.functions("fuEDITION_TEMPLATE", "fuRESTORE_EDITION")
                  + 'myEDITION=UNKNOWN myCOMPOSE_CUSTOMIZED="" myARCHIVE=/b/x_tpot_backup.tar\nfuRESTORE_EDITION\n')
        result = self.bash(script)
        warns = [line for line in result.stdout.splitlines() if line.startswith("WARN:")]
        self.assertTrue(any("docker-compose.yml" in line and "/b/x_tpot_backup.tar" in line for line in warns), warns)
        self.assertEqual(self.read(os.path.join(self.tpotce, "docker-compose.yml")), "# T-Pot: SENSOR\n")

    # --- the edition line under a comment of your own ----------------------------------------------

    EDITIONS = f'eval "$(grep "^myEDITIONS=" {REPO}/update.sh)"\n'
    EDITION_FUNCTIONS = ("fuEDITION_TEMPLATE", "fuEDITION_LINE", "fuCUSTOM_FILE", "fuCUSTOM_CHECKSUM",
                         "fuCHECK_EDITION", "fuCOMPOSE_FROM_ARCHIVE", "fuRESTORE_EDITION")
    TEMPLATE = "# T-Pot: STANDARD\nservices:\n  tpotinit:\n    image: new\n"

    def check_edition(self, compose, then=""):
        """fuCHECK_EDITION on HOME/tpotce/docker-compose.yml, with compose/standard.yml as the template."""
        self.write(os.path.join(self.tpotce, ".env"), "TPOT_TYPE=HIVE\n")
        self.write(os.path.join(self.tpotce, "docker-compose.yml"), compose)
        self.write(os.path.join(self.tpotce, "compose", "standard.yml"), self.TEMPLATE)
        script = (self.EDITIONS + self.functions(*self.EDITION_FUNCTIONS)
                  + 'fuTMPDIR () { myTMPDIR=$(mktemp -d); }\nmyEDITION="" myCOMPOSE_CUSTOMIZED="" '
                  'myARCHIVE=/b/x_tpot_backup.tar\nfuCHECK_EDITION\n'
                  'echo "edition=${myEDITION} customized=${myCOMPOSE_CUSTOMIZED}"\n' + then)
        result = self.bash(script)
        return result.stdout + result.stderr

    def test_a_comment_of_your_own_above_the_edition_line_keeps_the_edition(self):
        out = self.check_edition("# e2e-marker\n# T-Pot: STANDARD\nservices:\n  tpotinit:\n    image: old\n",
                                 then="fuRESTORE_EDITION\n")
        self.assertIn("edition=STANDARD customized=1", out)
        self.assertIn("OK: Edition STANDARD (customized).", out)
        # a detected edition with changes against its template: the template, the warning, the diff hint
        self.assertEqual(self.read(os.path.join(self.tpotce, "docker-compose.yml")), self.TEMPLATE)
        self.assertIn("WARN: Your docker-compose.yml had been modified. The STANDARD edition of this release is in "
                      "place now", out)
        self.assertIn(f"HINT:   tar xOf /b/x_tpot_backup.tar docker-compose.yml | diff - {self.tpotce}/docker-compose.yml",
                      out)
        self.assertNotIn("Unable to determine", out)

    def test_an_edition_line_after_the_first_yaml_line_is_not_taken(self):
        out = self.check_edition("services:\n# T-Pot: STANDARD\n  tpotinit:\n    image: old\n")
        self.assertIn("edition=UNKNOWN customized=", out)
        self.assertIn("WARN: Unable to determine the edition", out)

    def custom_file(self, preamble=""):
        body = "\nservices:\n  tpotinit:\n    image: x\n"
        header = ("# T-Pot: CUSTOM\n# customizer: version=2 base=MINI\n# customizer: add=\n# customizer: remove=\n"
                  "# customizer: port=\n# customizer: sha256=" + hashlib.sha256(body.encode()).hexdigest() + "\n")
        return preamble + header + body

    def test_a_comment_of_your_own_above_the_customizer_header(self):
        """fuCUSTOM_FILE finds the header under the edition line, the checksum covers the body only."""
        out = self.check_edition(self.custom_file("# e2e-marker\n\n"))
        self.assertIn("edition=CUSTOM customized=\n", out)
        self.assertIn("OK: Edition CUSTOM, built by compose/customizer.py.", out)
        out = self.check_edition(self.custom_file("# e2e-marker\n").replace("image: x", "image: mine"))
        self.assertIn("edition=CUSTOM customized=1", out)

    def test_a_rebuild_names_the_lines_above_the_header_it_leaves_out(self):
        archive = os.path.join(self.home, "x_tpot_backup.tar")
        staging = os.path.join(self.home, "staging")
        for preamble, warned in (("# e2e-marker\n", True), ("", False), ("\n  \n", False), ("\n# mine\n", True)):
            with self.subTest(preamble=preamble):
                self.write(os.path.join(staging, "docker-compose.yml"), self.custom_file(preamble))
                subprocess.run(["tar", "cf", archive, "-C", staging, "docker-compose.yml"], check=True)
                # the customizer of the checkout: writes the file anew from its header (here: without the comment)
                self.write(os.path.join(self.tpotce, "compose", "customizer.py"),
                           "import sys  # --rebuild\nsrc, dst = sys.argv[2], sys.argv[4]\n"
                           "text = open(src).read()\nopen(dst, 'w').write(text[text.index('# T-Pot:'):])\n")
                script = (self.functions("fuEDITION_LINE", "fuCOMPOSE_FROM_ARCHIVE", "fuRESTORE_CUSTOM")
                          + f'fuTMPDIR () {{ myTMPDIR=$(mktemp -d); }}\nmyARCHIVE="{archive}" '
                          'myCOMPOSE_CUSTOMIZED=""\nfuRESTORE_CUSTOM\n')
                result = self.bash(script)
                out = result.stdout + result.stderr
                self.assertEqual(self.read(os.path.join(self.tpotce, "docker-compose.yml")), self.custom_file(), out)
                self.assertEqual("WARN: The lines above '# T-Pot: CUSTOM'" in out, warned, out)
                self.assertEqual("DID: warn: The lines above '# T-Pot: CUSTOM'" in out, warned, out)

    def test_update_sh_and_the_t_pot_manager_take_the_same_edition_line(self):
        import importlib.util
        try:
            import yaml  # noqa: F401  (customizer_core needs it)
        except ImportError:
            self.skipTest("no PyYAML")
        from tpotctl import ops
        spec = importlib.util.spec_from_file_location("customizer_core_e1",
                                                      os.path.join(REPO, "compose", "customizer_core.py"))
        core = importlib.util.module_from_spec(spec)
        import sys
        sys.modules[spec.name] = core                    # the dataclasses look their module up there
        self.addCleanup(sys.modules.pop, spec.name, None)
        spec.loader.exec_module(core)
        cases = ["# T-Pot: STANDARD\nservices:\n", "# e2e-marker\n# T-Pot: STANDARD\n", "\n\n# T-Pot: MINI\n",
                 "  # indented\n# T-Pot: LLM\n", "services:\n# T-Pot: STANDARD\n", "# only a comment\n", "",
                 "# T-Pot: one\n# T-Pot: STANDARD\n", "x: 1\n", "# a\n#T-Pot: STANDARD\n# T-Pot: MINI\n",
                 "# T-Pot:STANDARD\n", "# T-Pot:   SENSOR  \n", "# T-Pot:\tMINI\r\n", "# T-Pot:\n# T-Pot: MINI\n",
                 "# T-Pot: standard\n", "# T-Pot: CUSTOM EDITION\n"]
        path = os.path.join(self.home, "compose.yml")
        for text in cases:
            with self.subTest(text=text):
                self.write(path, text)
                result = self.bash(self.functions("fuEDITION_LINE") + f'fuEDITION_LINE "{path}"\n')
                bash_line = int(result.stdout.split(" ", 1)[0]) - 1 if result.stdout.strip() else -1
                bash_name = result.stdout.split(" ", 1)[1].rstrip("\n") if result.stdout.strip() else ""
                lines = text.split("\n")
                self.assertEqual(ops.edition_line(lines), bash_line)
                self.assertEqual(core.edition_line(lines), bash_line)
                # the same name: update.sh, the T-Pot Manager (ops) and the customizer (editions in capitals)
                self.write(os.path.join(self.home, "repo", "docker-compose.yml"), text)
                name = ops.edition_header(os.path.join(self.home, "repo", "docker-compose.yml"))[0]
                self.assertEqual(name, bash_name)
                self.assertEqual(core.edition_of(lines), name if re.fullmatch(r"[A-Z_]+", name) else "")
        self.assertEqual(core.edition_of(["# T-Pot:STANDARD"]), "STANDARD")

    # --- git and files always from ~/tpotce --------------------------------------------------------

    def test_source_and_version_come_from_the_checkout_not_the_current_folder(self):
        self.clone_of_origin()
        script = (f'source {REPO}/installer/lib/ui.sh; fuUI_INIT\n'
                  + self.functions("fuNORMALIZE_REPO", "fuCHECK_SOURCE", "fuCHECK_VERSION")
                  + f'myHERE="{REPO}" myTPOT_SOURCE_GIVEN="" myTPOT_BRANCH="" myTPOT_REPO_URL=""\n'
                  'fuCHECK_SOURCE; echo "branch=${myCURRENT_BRANCH} repo=${myCURRENT_REPO}"; fuCHECK_VERSION\n')
        result = self.bash(script, cwd=self.elsewhere)
        out = result.stdout + result.stderr
        self.assertIn("branch=master", out)
        self.assertIn("is eligible for the update procedure", out)

    def test_no_bare_git_or_file_read_in_update_sh(self):
        bad = []
        for number, line in enumerate(read("update.sh").splitlines(), 1):
            code = line.strip()
            if code.startswith(("#", "echo", "fuDID ")) or "fuUI_" in code:      # messages only
                continue
            # a text for a message (a plain string, nothing run or read in it)
            if re.fullmatch(r'(local )?my[A-Z_]+="[^"$`]*"', code):
                continue
            if re.search(r'(^|[\s(!&|;])git (rev-parse|remote|fetch|reset|pull|checkout|branch|describe|ls-files)\b',
                         code):
                bad.append((number, code))
            if re.search(r'(env\.example|< version|cat version|-f "version")', code) \
               and "$HOME/tpotce" not in code and "${myHERE}" not in code and " show " not in code:
                bad.append((number, code))
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
