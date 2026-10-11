"""The T-Pot scripts a person runs look like tpot: installer/lib/ui.sh, @@tpot marks, -B.

Bash harnesses: the scripts run in a temporary HOME with a sudo that only runs the
command, never on the real checkout.
"""

import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import textwrap
import unittest

from tpotctl.tests import isolate

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
        function = re.search(r"function fuROLLBACK_CHECKOUT \(\) \{.*?\n\}", read("update.sh"), re.S).group(0)
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
                  f'cd "{clone}" && fuSELFUPDATE')

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
        self.assertEqual(len(re.findall(r"trap .*INT", read("update.sh"))), 5)

    def test_the_refusal_names_full(self):
        self.assertTrue("--full" in self.check_elastic())

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

    def run_migration(self, old_env: str, policy, read_only=False):
        with tempfile.TemporaryDirectory() as tmp:
            env, old, export = (os.path.join(tmp, n) for n in (".env", "old.env", "ilm_policy_tpot.json"))
            with open(env, "w") as h:
                h.write("TPOT_PERSISTENCE_CYCLES=30\nTPOT_ES_RETENTION=30\nTPOT_TYPE=HIVE\n")
            if read_only:
                os.chmod(env, 0o444)
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
# says what HOME/need holds
DU = ('#!/bin/sh\nfor a; do f="$a"; done\n'
      'case "$1" in -s*b) echo "$(cat "$HOME/need" 2>/dev/null || echo 1)\ttotal"; exit 0 ;; esac\n'
      'k=$(sed -n "s|^${f##*/} ||p" "$HOME/sizes" 2>/dev/null)\necho "${k:-1}\t$f"\n')


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

    SPACE = ("fuLOSS_ADD", "fuLOSS_CONFIRM", "fuDROP_OLDEST", "fuBACKUP_SIZE", "fuNO_ROOM", "fuCHECK_BACKUP_SPACE")

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

    def elastic(self, archive_has_data, extra="", stub_confirm=""):
        os.makedirs(os.path.join(self.tpotce, "docker", "elk", "elasticsearch"))
        with open(os.path.join(self.tpotce, "docker", "elk", "elasticsearch", "Dockerfile"), "w") as out:
            out.write("FROM alpine\nARG ES_VER=9.1.0\n")
        with open(os.path.join(self.tpotce, "docker-compose.yml"), "w") as out:
            out.write("services:\n  elasticsearch:\n    image: x\n")
        with open(os.path.join(self.tpotce, ".env"), "w") as out:
            out.write("TPOT_REPO=ghcr.io/telekom-security\nTPOT_VERSION=24.04.2\n")
        return textwrap.dedent(f"""
            myOLDVERSION=24.04.1 myARCHIVE="$HOME/tpot_backups/x.tar"
            docker () {{ [ "$1 $2" = "image inspect" ] && printf 'PATH=/bin\\nES_VER=8.19.0\\n'; }}
            tar () {{ {"return 0" if archive_has_data else "return 1"}; }}
            fuROLLBACK_CHECKOUT () {{ echo "ROLLBACK"; return 0; }}
        """) + extra + self.functions("fuLOSS_CONFIRM", "fuDATA_PATH", "fuCOMPOSE_HAS", "fuES_IMAGE_VERSION", "fuELASTIC_REFUSED",
                                                                  "fuCHECK_ELASTIC") \
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
                      "of this run does not hold it (--full does).", out)
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
        """) + extra + self.functions("fuLOSS_CONFIRM", "fuIMAGE_PULLABLE", "fuLOCAL_IMAGES_TEXT", "fuIMAGES",
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
    CHECK_FUNCTIONS = ("fuLOSS_ADD", "fuLOSS_CONFIRM", "fuDROP_OLDEST", "fuBACKUP_SIZE", "fuNO_ROOM",
                       "fuCHECK_BACKUP_SPACE",
                       "fuDATA_PATH", "fuCOMPOSE_HAS", "fuES_IMAGE_VERSION", "fuIMAGE_PULLABLE",
                       "fuLOCAL_IMAGES_TEXT", "fuIMAGES", "fuLOSS_TARGET", "fuCHECK_DATA_LOSS")

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
        self.assertIn('fuSELFUPDATE "$@" ${myACCEPT_LOSS:+--accept-data-loss}', self.main_section())

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
        """) + self.functions("fuIMAGE_PULLABLE") + 'fuIMAGE_PULLABLE repo/x:1 && echo pullable || echo here\n'
        return self.bash(script).stdout.strip().splitlines()[-1]

    def test_no_digest_is_built_here(self):
        self.assertEqual(self.pullable("", "sha256:aaa"), "here")

    def test_the_same_digest_as_the_registry_is_pullable(self):
        self.assertEqual(self.pullable("repo/x@sha256:bbb\\nrepo/x@sha256:aaa\\n", "sha256:aaa"), "pullable")

    def test_another_digest_than_the_registry_is_built_here(self):
        # the containerd image store gives a local build a RepoDigest of its own
        self.assertEqual(self.pullable("repo/x@sha256:bbb\\n", "sha256:aaa"), "here")

    def test_buildx_failing_is_built_here(self):
        self.assertEqual(self.pullable("repo/x@sha256:aaa\\n", None), "here")
        self.assertEqual(self.pullable("repo/x@sha256:aaa\\n", ""), "here")

    def test_without_timeout_it_is_built_here(self):
        script = ('unset -f timeout\nPATH=/nonexistent\ndocker () { echo "repo/x@sha256:aaa"; }\n'
                  + self.functions("fuIMAGE_PULLABLE") + 'fuIMAGE_PULLABLE repo/x:1 && echo pullable || echo here\n')
        self.assertEqual(self.bash(script).stdout.strip().splitlines()[-1], "here")

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


if __name__ == "__main__":
    unittest.main()
