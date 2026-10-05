"""The T-Pot scripts a person runs look like tpot: installer/lib/ui.sh, @@tpot marks, -B.

Bash harnesses: the scripts run in a temporary HOME with a sudo that only runs the
command, never on the real checkout.
"""

import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import unittest

from tpotctl.tests import isolate

isolate()

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SUDO = "#!/bin/sh\n# runs the command without sudo, options of sudo are left out\nwhile [ $# -gt 0 ]; do\n" \
       "  case \"$1\" in -S|-k|-v|-n) shift ;; -p) shift 2 ;; *) break ;; esac\ndone\n" \
       "[ $# -eq 0 ] && exit 0\nexec \"$@\"\n"


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
                           ("docker", "#!/bin/sh\necho \"docker $*\" >> \"$HOME/calls\"\n")):
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

    def test_the_elastic_check_comes_before_the_tpot_setup(self):
        main = read("update.sh").split("\nfuREMOVE_DROPPED_SERVICES\n", 1)[1]     # the main section
        self.assertLess(main.index("\nfuCHECK_ELASTIC\n"), main.index("\nfuTPOT_SETUP\n"))

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

    def test_genuser_fallback(self):
        result = self.run_script(os.path.join(REPO, "genuser.sh"))
        self.assertIn("### [WARNING] - tpot is not available, using the tpotinit container.", result.stdout)
        with open(os.path.join(self.home, "calls"), encoding="utf-8") as handle:
            self.assertIn("tpotinit:24.04.2", handle.read())

    def test_deploy_fallback_speaks_like_ui_sh(self):
        text = read("deploy.sh")
        text = text.replace(text[text.index("# >>> plain fallback"):text.index("# <<< plain fallback")], "")
        self.assertEqual(re.findall(r'.*(?:echo "#|read -r?p).*', text), [])
        self.assertIn("fuUI_BANNER", text)

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


if __name__ == "__main__":
    unittest.main()
