"""tpot-auto-updates, the helper of the automatic updates (Debian): what .env turns off, the step it writes
into the cron job of the nightly reboot (the very line of the playbook), and that the playbooks set up and
remove the same files.

The helper runs with myTPOT_AU_ROOT (its paths in a temporary folder) and a stub crontab; nothing here
touches the host. Its apt and Docker side is checked in a Debian container, not here.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from tpotctl.tests import isolate  # noqa: E402

isolate()

from tpotctl.bootstrap import REPO_DIR  # noqa: E402

FOLDER = os.path.join(REPO_DIR, "installer", "install", "auto-updates")
HELPER = os.path.join(FOLDER, "tpot-auto-updates")
MARK = "#Ansible: T-Pot Daily Reboot"


def read(*parts):
    with open(os.path.join(REPO_DIR, *parts), encoding="utf-8") as handle:
        return handle.read()


def playbook_job(step):
    """The job of the cron task of the playbook, with auto_updates_step as given."""
    job = re.search(r'name: "T-Pot Daily Reboot"\n(?:.*\n)*?\s*job: "(.*)"\n', read("installer", "install", "tpot.yml"))
    return job.group(1).replace('\\"', '"').replace("{{ auto_updates_step }}", step)


class Helper(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.root = os.path.join(self.tmp, "root")
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(os.path.join(self.root, "etc"))
        os.makedirs(self.bin)
        self.tpotce = os.path.join(self.tmp, "tpotce")
        os.makedirs(self.tpotce)
        self.release("ID=debian\n")

    def release(self, text):
        with open(os.path.join(self.root, "etc", "os-release"), "w", encoding="utf-8") as out:
            out.write(text)

    def run_helper(self, *args, script=None):
        env = {"PATH": f"{self.bin}:{os.environ['PATH']}", "HOME": self.tmp, "myTPOT_AU_ROOT": self.root,
               "CRONTAB": os.path.join(self.tmp, "crontab"), "CALLS": os.path.join(self.tmp, "calls")}
        command = ["bash", "-c", f'source "{HELPER}"; {script}'] if script else ["bash", HELPER, *args]
        return subprocess.run(command, capture_output=True, universal_newlines=True, env=env, timeout=60)


class CheckTest(Helper):
    """check: rc 1 only for TPOT_AUTO_UPDATES=DISABLED (any case, quoted, with a comment after it), the last
    line wins; an .env without the key, an empty or an unknown value keep the updates on."""

    def check(self, text):
        env = os.path.join(self.tpotce, ".env")
        if text is None:
            if os.path.exists(env):
                os.remove(env)
        else:
            with open(env, "w", encoding="utf-8") as out:
                out.write(text)
        return self.run_helper("check", self.tpotce).returncode

    def test_what_turns_them_off(self):
        for text, rc in ((None, 0), ("", 0), ("TPOT_AUTO_UPDATES=\n", 0), ("TPOT_AUTO_UPDATES=ENABLED\n", 0),
                         ("TPOT_AUTO_UPDATES=bogus\n", 0), ("TPOT_AUTO_UPDATES=DISABLED\n", 1),
                         ("TPOT_AUTO_UPDATES=disabled\n", 1), ("TPOT_AUTO_UPDATES=DISABLED # off\n", 1),
                         ("TPOT_AUTO_UPDATES='DISABLED'\n", 1), ('TPOT_AUTO_UPDATES="DISABLED"\n', 1),
                         (" TPOT_AUTO_UPDATES = DISABLED\n", 1), ("#TPOT_AUTO_UPDATES=DISABLED\n", 0),
                         ("TPOT_AUTO_UPDATES=DISABLED\nTPOT_AUTO_UPDATES=ENABLED\n", 0),
                         ("MY_TPOT_AUTO_UPDATES=DISABLED\n", 0)):
            with self.subTest(text=text):
                self.assertEqual(self.check(text), rc)

    def test_env_example_keeps_them_on(self):
        self.assertIn("\nTPOT_AUTO_UPDATES=ENABLED\n", read("env.example"))
        self.assertEqual(self.check(read("env.example")), 0)

    def test_a_folder_is_required(self):
        for args in ((), ("check",), ("setup",), ("docker",), ("bogus", self.tpotce),
                     ("docker", self.tpotce, "--later")):
            with self.subTest(args=args):
                result = self.run_helper(*args)
                self.assertEqual(result.returncode, 2)
                self.assertIn("Usage:", result.stderr)


class NotDebianTest(Helper):
    """Raspbian (32-bit), Ubuntu, Fedora: setup says so and does nothing, the Docker step does nothing."""

    def test_setup_and_docker_do_nothing(self):
        for text in ("ID=raspbian\nID_LIKE=debian\n", "ID=ubuntu\nID_LIKE=debian\n", "ID=fedora\n", ""):
            with self.subTest(release=text):
                self.release(text)
                result = self.run_helper("setup", self.tpotce)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("Not Debian", result.stdout)
                result = self.run_helper("docker", self.tpotce, "--now")
                self.assertEqual((result.returncode, result.stdout), (0, ""))
                self.assertEqual(sorted(os.listdir(self.root)), ["etc"])
                self.assertEqual(os.listdir(os.path.join(self.root, "etc")), ["os-release"])

    def test_the_quoted_id_of_debian_counts(self):
        for text in ('ID="debian"\n', "ID='debian'\n"):
            with self.subTest(release=text):
                self.release(text)
                self.assertEqual(self.run_helper(script="fuDEBIAN").returncode, 0)


class CronTest(Helper):
    """fuCRON puts the Docker Engine step right before the reboot of the job the playbook wrote, the same line
    the playbook of this release writes; a step of another tpotce folder is replaced, the rest of the crontab
    stays, nothing is written without a change, without the job or without a crontab."""

    def setUp(self):
        super().setUp()
        with open(os.path.join(self.bin, "crontab"), "w", encoding="utf-8") as out:
            out.write('#!/bin/sh\n[ "$1 $2" = "-u root" ] || exit 9\n'
                      'case "$3" in\n'
                      '  -l) [ -f "$CRONTAB" ] || { echo "no crontab for root" >&2; exit 1; }; cat "$CRONTAB" ;;\n'
                      '  -) cat > "$CRONTAB"; echo written >> "$CALLS" ;;\n'
                      '  *) exit 9 ;;\n'
                      'esac\n')
        os.chmod(os.path.join(self.bin, "crontab"), 0o755)

    def step(self, folder):
        return f"/usr/local/sbin/tpot-auto-updates docker {folder}; "

    def crontab(self, text=None):
        path = os.path.join(self.tmp, "crontab")
        if text is not None:
            with open(path, "w", encoding="utf-8") as out:
                out.write(text)
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def writes(self):
        path = os.path.join(self.tmp, "calls")
        if not os.path.exists(path):
            return 0
        with open(path, encoding="utf-8") as handle:
            return len(handle.read().splitlines())

    def cron(self, folder):
        return self.run_helper(script=f'fuCRON "{folder}"')

    def test_the_job_of_an_earlier_t_pot_gets_the_step_of_the_playbook(self):
        mine = "0 1 * * * echo mine\n#Ansible: something else\n5 4 * * * /usr/sbin/shutdown -r now\n"
        self.crontab(f"{mine}{MARK}\n12 3 * * * {playbook_job('')}\n# after\n")
        result = self.cron("/home/alice/tpotce")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "changed: the cron job T-Pot Daily Reboot\n")
        self.assertEqual(self.crontab(),
                         f"{mine}{MARK}\n12 3 * * * {playbook_job(self.step('/home/alice/tpotce'))}\n# after\n")
        self.assertEqual(self.writes(), 1)
        # once more: the same, nothing written
        result = self.cron("/home/alice/tpotce")
        self.assertEqual((result.returncode, result.stdout), (0, ""))
        self.assertEqual(self.writes(), 1)

    def test_the_line_of_the_playbook_stays_as_it_is(self):
        line = f"{MARK}\n12 3 * * * {playbook_job(self.step('/home/alice/tpotce'))}\n"
        self.crontab(line)
        self.assertEqual(self.cron("/home/alice/tpotce").stdout, "")
        self.assertEqual((self.crontab(), self.writes()), (line, 0))

    def test_the_step_of_another_folder_is_replaced(self):
        self.crontab(f"{MARK}\n12 3 * * * {playbook_job(self.step('/home/bob/tpotce'))}\n")
        self.assertIn("changed:", self.cron("/home/alice/tpotce").stdout)
        self.assertEqual(self.crontab(), f"{MARK}\n12 3 * * * {playbook_job(self.step('/home/alice/tpotce'))}\n")

    def test_without_the_job_or_a_crontab_nothing_is_written(self):
        for text in ("0 1 * * * echo mine\n", f"# {MARK}\n12 3 * * * {playbook_job('')}\n"):
            with self.subTest(text=text):
                self.crontab(text)
                result = self.cron("/home/alice/tpotce")
                self.assertEqual((result.returncode, result.stdout), (0, ""))
                self.assertEqual((self.crontab(), self.writes()), (text, 0))
        os.remove(os.path.join(self.tmp, "crontab"))
        result = self.cron("/home/alice/tpotce")
        self.assertEqual((result.returncode, result.stdout), (0, ""))
        self.assertEqual(self.writes(), 0)


class PlaybooksTest(unittest.TestCase):
    """The install playbook runs the setup of the checkout as root on Debian, before Docker Engine comes from
    the Docker repository (the pin is there first); the cron job has the step where the helper is installed;
    the remove playbook removes everything the setup installs."""

    def setup_targets(self):
        text = read("installer", "install", "auto-updates", "tpot-auto-updates")
        setup = re.search(r"^fuSETUP \(\) \{.*?^\}", text, re.S | re.M).group(0)
        targets = re.findall(r'"\$\{myROOT\}(/[^"$]+)"', setup)
        return text, targets

    def test_the_setup_installs_the_files_of_its_folder(self):
        text, targets = self.setup_targets()
        sources = re.findall(r'fuPUT "\$\{mySRC\}/([^"]+)"', text)
        self.assertEqual(sorted(sources), sorted(n for n in os.listdir(FOLDER) if not n.startswith(".")))
        self.assertTrue(os.access(HELPER, os.X_OK))
        self.assertIn('myBIN="/usr/local/sbin/tpot-auto-updates"', text)
        self.assertEqual(len(targets), 5, targets)

    def test_the_remove_playbook_removes_all_of_it(self):
        text, targets = self.setup_targets()
        remove = read("installer", "remove", "tpot.yml")
        play = remove[remove.index("- name: T-Pot - Remove automatic updates (Debian)"):]
        play = play[:play.index("\n- name:", 1)] if "\n- name:" in play[1:] else play
        for path in targets + ["/usr/local/sbin/tpot-auto-updates", "/var/lib/tpot-auto-updates",
                               "/etc/systemd/system/apt-daily-upgrade.service.d/tpot.conf"]:
            with self.subTest(path=path):
                self.assertIn(path, play)
        self.assertIn("/var/lib/tpot-auto-updates/unattended-upgrades", play)

    def test_the_install_playbook_sets_them_up_before_docker_on_debian(self):
        playbook = read("installer", "install", "tpot.yml")
        start = playbook.index("- name: T-Pot - Automatic updates (Debian)")
        self.assertLess(start, playbook.index("- name: T-Pot - Install Docker Engine"))
        play = playbook[start:playbook.index("\n- name:", start + 1)]
        self.assertRegex(play, r'tags:\n\s*- "Debian"\n')
        self.assertNotIn("Raspbian", play)
        self.assertIn("tpot-auto-updates setup {{ ansible_facts.user_dir }}/tpotce", play)
        self.assertIn("become: true", play)

    def test_the_cron_step_needs_the_helper(self):
        playbook = read("installer", "install", "tpot.yml")
        start = playbook.index("- name: T-Pot - Setup a randomized daily reboot")
        play = playbook[start:]
        self.assertIn("if tpot_auto_updates_helper.stat.exists else ''", play)
        self.assertLess(play.index("register: tpot_auto_updates_helper"), play.index("cron:"))
        self.assertIn(" - name: Look for the automatic updates", play)
        self.assertIn("path: /usr/local/sbin/tpot-auto-updates", play)


if __name__ == "__main__":
    unittest.main()
