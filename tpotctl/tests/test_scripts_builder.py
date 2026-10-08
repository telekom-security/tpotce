"""docker/_builder/builder.sh: options for unattended runs, the menu, the builder setup, the upload
limit, the push login and the smoke tests after a build.

The script runs with stubs for docker, id, ip, tc, chown and uname (every call in $HOME/calls), its
logs in a temporary TPOT_BUILDER_LOG_DIR and its settings in a temporary TPOT_BUILDER_ENV_LOCAL; it
never calls the real docker and never writes the checkout.
"""

import fcntl
import hashlib
import os
import pty
import re
import select
import shutil
import signal
import struct
import subprocess
import tempfile
import termios
import time
import unittest

from tpotctl.tests import test_scripts as base

REPO = base.REPO
BUILDER = os.path.join(REPO, "docker", "_builder", "builder.sh")
SETUP = os.path.join(REPO, "docker", "_builder", "setup_builder.sh")
ENVFILE = os.path.join(REPO, "docker", "_builder", ".env")
LOCAL = os.path.join(REPO, "docker", "_builder", ".env.local")
COMPOSE = os.path.join(REPO, "docker", "_builder", "docker-compose.yml")
VERSION = base.read("version").strip()
# the platforms buildx lists on an arm64 host with the QEMU emulators of tonistiigi/binfmt --install all
THIRTEEN = ("linux/arm64, linux/amd64, linux/amd64/v2, linux/amd64/v3, linux/riscv64, linux/ppc64le, linux/s390x, "
            "linux/386, linux/mips64le, linux/mips64, linux/loong64, linux/arm/v7, linux/arm/v6")

DOCKER = r"""#!/bin/sh
echo "docker $* | TPOT_VERSION=${TPOT_VERSION:-} TPOT_DOCKER_REPO=${TPOT_DOCKER_REPO:-} TPOT_GHCR_REPO=${TPOT_GHCR_REPO:-}" >> "$HOME/calls"
case "$*" in
  info) exit "${STUB_INFO_RC:-0}" ;;
  'info --format'*)
    [ "${STUB_INFO_RC:-0}" = 0 ] || exit "${STUB_INFO_RC}"
    [ "${STUB_ROOTLESS:-}" = 1 ] && echo '[name=seccomp,profile=builtin name=rootless name=cgroupns]'
    exit 0 ;;
  *'compose version --short'*) echo "${STUB_COMPOSE_VERSION:-2.29.1}"; exit 0 ;;
  *'buildx inspect'*)
    [ "${STUB_NO_BUILDER:-}" = 1 ] && { echo "no builder"; exit 1; }
    printf 'Name:   mybuilder\nDriver: docker-container\nNodes:\nStatus:    running\nPlatforms: %s\n' \
      "${STUB_PLATFORMS:-linux/amd64, linux/arm64}"
    exit 0 ;;
  *'config --services'*) printf '%s\n' ${STUB_SERVICES:-cowrie}; exit 0 ;;
  *'tonistiigi/binfmt'*) exit "${STUB_BINFMT_RC:-0}" ;;
  login*) [ -n "${STUB_LOGIN_SLEEP:-}" ] && sleep "${STUB_LOGIN_SLEEP}"; exit "${STUB_LOGIN_RC:-0}" ;;
  *override-load.yml*' build '*) exit "${STUB_LOAD_RC:-0}" ;;
  *' build '*)
    for s in ${STUB_FAIL:-}; do
      case "$*" in *"build $s "*) echo "no such file"; exit 1 ;; esac
    done
    for s in ${STUB_SLOW:-}; do
      case "$*" in *"build $s "*) sleep 1 ;; esac
    done
    [ -n "${STUB_BUILD_PIDS:-}" ] && echo "$$" >> "${STUB_BUILD_PIDS}"
    if [ -n "${STUB_BUILD_SLEEP:-}" ]; then
      sleep "${STUB_BUILD_SLEEP}" &
      [ -n "${STUB_BUILD_PIDS:-}" ] && echo "$!" >> "${STUB_BUILD_PIDS}"
      wait "$!"
    fi
    [ -n "${STUB_BUILD_MARK:-}" ] && echo "finished $*" >> "${STUB_BUILD_MARK}"
    echo "built"
    exit 0 ;;
esac
exit 0
"""

STUBS = {
    "docker": DOCKER,
    # root (uid 0) without STUB_USER, any other name is uid / gid 1000 with a group of its name
    "id": "#!/bin/sh\nu=\"${STUB_USER:-root}\"; n=1000; [ \"$u\" = root ] && n=0\n"
          "case \"$*\" in\n  -u|-g) echo \"$n\" ;;\n  -un|-gn) echo \"$u\" ;;\n  *) echo \"${STUB_GROUPS:-$u}\" ;;\nesac\n",
    "uname": "#!/bin/sh\ncase \"$1\" in -s) echo \"${STUB_SYSTEM:-Linux}\" ;; *) echo \"${STUB_ARCH:-x86_64}\" ;; esac\n",
    "chown": "#!/bin/sh\necho \"chown $*\" >> \"$HOME/calls\"\n",
    "ip": "#!/bin/sh\necho \"ip $*\" >> \"$HOME/calls\"\necho 'default via 10.0.0.1 dev eth0 proto dhcp'\n",
    "tc": "#!/bin/sh\necho \"tc $*\" >> \"$HOME/calls\"\n"
          "case \"$*\" in\n"
          "  *'qdisc show'*) [ -n \"${STUB_TC_SHOW:-}\" ] && echo \"${STUB_TC_SHOW}\"; exit 0 ;;\n"
          "  *'qdisc add'*) exit \"${STUB_TC_ADD_RC:-0}\" ;;\n"
          "esac\nexit 0\n",
}


def at_terminal(args, env, answers="", timeout=30):
    """Runs builder.sh in a pty (stdin, stdout and stderr) with the answers typed ahead; rc, output."""
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 120, 0, 0))
    proc = subprocess.Popen(["bash", BUILDER] + list(args), env=env, stdin=slave, stdout=slave, stderr=slave,
                            close_fds=True)
    os.close(slave)
    if answers:
        os.write(master, answers.encode())
    out = b""
    deadline = time.time() + timeout
    while time.time() < deadline:
        ready, _w, _x = select.select([master], [], [], 0.1)
        if ready:
            try:
                data = os.read(master, 65536)
            except OSError:
                break
            if not data:
                break
            out += data
        elif proc.poll() is not None:
            break
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=5)
    os.close(master)
    return proc.returncode, out.decode("utf-8", "replace").replace("\r\n", "\n")


class BuilderHarness(base.Harness):

    def setUp(self):
        super().setUp()
        for name, text in STUBS.items():
            self.stub(name, text)
        self.log = os.path.join(self.home, "log")
        self.binfmt = os.path.join(self.home, "binfmt")
        self.local = os.path.join(self.home, "env.local")
        # the tracked .env of the builder and the settings of the checkout are never written
        self.tracked = self.digest(ENVFILE)
        self.checkout_local = self.digest(LOCAL)
        self.addCleanup(self.untouched)

    @staticmethod
    def digest(path):
        if not os.path.exists(path):
            return None
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()

    def untouched(self):
        self.assertEqual(self.digest(ENVFILE), self.tracked, "docker/_builder/.env was written")
        self.assertEqual(self.digest(LOCAL), self.checkout_local, "docker/_builder/.env.local was written")

    def stub(self, name, text, folder=None):
        path = os.path.join(folder or self.bin, name)
        with open(path, "w", encoding="utf-8") as out:
            out.write(text)
        os.chmod(path, 0o755)
        return path

    def env(self, **extra):
        env = {"TPOT_BUILDER_LOG_DIR": self.log, "TPOT_BINFMT_DIR": self.binfmt, "TPOT_BUILDER_ENV_LOCAL": self.local}
        # the settings of the one who runs the tests stay out
        for key in ("TPOT_VERSION", "TPOT_DOCKER_REPO", "TPOT_GHCR_REPO", "TPOT_BUILDER_ARCH", "TPOT_BUILDER_JOBS",
                    "TPOT_BUILDER_LIMIT", "SUDO_UID", "SUDO_GID", "SUDO_USER", "myUI_VERSION", "DOCKER_HOST",
                    "XDG_RUNTIME_DIR"):
            env[key] = ""
        env.update(extra)
        return env

    def builder(self, *args, **extra):
        result = self.run_script(BUILDER, *args, env=self.env(**extra))
        return result.returncode, result.stdout + result.stderr

    def calls(self):
        path = os.path.join(self.home, "calls")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return handle.read().splitlines()

    def calls_reset(self):
        path = os.path.join(self.home, "calls")
        if os.path.exists(path):
            os.remove(path)

    def chowns(self):
        return [line for line in self.calls() if line.startswith("chown ")]

    def builds(self):
        # the images built, in the order of the calls
        found = []
        for line in self.calls():
            match = re.search(r" build (\S+) --builder", line)
            if match and "override-load.yml" not in line:
                found.append(match.group(1))
        return found

    def override(self, name="override.yml"):
        with open(os.path.join(self.log, name), encoding="utf-8") as handle:
            return handle.read()

    def bash(self, script, **extra):
        """Sources builder.sh (the functions only) and runs the script."""
        environment = dict(os.environ, HOME=self.home, PATH=f"{self.bin}:{os.environ['PATH']}", TPOT_GUM="off")
        environment.update(self.env(**extra))
        return subprocess.run(["bash", "-c", f'source "{BUILDER}"\n{script}'], capture_output=True,
                              universal_newlines=True, env=environment, stdin=subprocess.DEVNULL, timeout=60)

    def terminal_env(self, **extra):
        env = dict(os.environ, HOME=self.home, PATH=f"{self.bin}:{os.environ['PATH']}", TPOT_GUM="off",
                   TERM="xterm-256color")
        env.pop("TPOT_MARKS", None)
        env.update(self.env(**extra))
        return env


class CliTest(BuilderHarness):

    def test_help_names_every_option_and_the_exit_codes(self):
        rc, out = self.builder("-h")
        self.assertEqual(rc, 0, out)
        for flag in ("-y, --yes", "-i, --images", "-g, --group", "-a, --arch", "-p, --push", "--push-hub",
                     "--push-ghcr", "-n, --no-cache", "-j, --jobs", "-l, --upload-limit", "-t, --tag",
                     "--docker-repo", "--ghcr-repo", "-T, --test", "-L, --list", "--check", "--setup",
                     "--uninstall", "--set KEY=VALUE", "--unset KEY", "--show-config", "-h, --help"):
            self.assertIn(flag, out)
        for code in ("0 done", "1 an image failed", "2 a wrong option", "3 the environment", "4 built but",
                     "130 cancelled"):
            self.assertIn(code, out)
        self.assertEqual(self.calls(), [])

    def test_bad_usage_exits_2(self):
        for args in (["-Z"], ["--nope"], ["-g", "games"], ["-i", "nosuch"], ["-i", "cowrie,nosuch"], ["-i"],
                     ["-a", "sparc"], ["-j", "0"], ["-j", "17"], ["-j", "x"], ["--jobs=99"], ["-l", "fast"],
                     ["-t", "bad tag"], ["--docker-repo", "Not/Valid"], ["-L", "--check"], ["--setup", "--uninstall"],
                     ["--push=yes"], ["-i", ","]):
            with self.subTest(args=args):
                rc, out = self.builder(*args)
                self.assertEqual(rc, 2, out)
                self.assertIn("### [ERROR] - ", out)
                self.assertIn("-h shows the options.", out)
        # a wrong option stops before anything is asked or checked, even without rights
        self.assertEqual(self.calls(), [])

    def test_options_are_parsed_like_getopts(self):
        result = self.bash('fuPARSE -pnT --jobs=4 -icowrie -g nsm,tools --upload-limit=off -a host -t 9.9.9\n'
                           'echo "$myPUSH_HUB$myPUSH_GHCR $myNO_CACHE $myTEST $myJOBS $myIMAGES $myGROUPS $myLIMIT '
                           '$myARCH $myTAG"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "11 1 1 4 cowrie nsm,tools off host 9.9.9\n")

    def test_list_needs_neither_rights_nor_docker(self):
        rc, out = self.builder("-L", STUB_USER="tester", STUB_GROUPS="tester")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.calls(), [])
        groups = dict(line.split(None, 1) for line in out.strip().splitlines())
        self.assertEqual(sorted(groups), ["elk", "honeypots", "nsm", "tanner", "tools"])
        self.assertIn("cowrie", groups["honeypots"].split())

    def test_groups_cover_compose(self):
        # every image of the compose file in exactly one group, and no group names one that is not there
        with open(COMPOSE, encoding="utf-8") as handle:
            text = handle.read()
        services = set(re.findall(r"^  ([a-z0-9][a-z0-9_.-]*):\s*$", text.split("\nservices:", 1)[1], re.M))
        rc, out = self.builder("-L")
        self.assertEqual(rc, 0, out)
        listed = [name for line in out.strip().splitlines() for name in line.split()[1:]]
        self.assertEqual(len(listed), len(set(listed)), listed)
        self.assertEqual(set(listed), services)
        result = self.bash("fuCOMPOSE_SERVICES")
        self.assertEqual(set(result.stdout.split()), services)

    def test_selection_images_and_groups(self):
        rc, out = self.builder("-i", "cowrie", "-g", "nsm")
        self.assertEqual(rc, 0, out)
        self.assertEqual(sorted(self.builds()), ["cowrie", "p0f", "suricata"])
        self.assertFalse(any("config --services" in line for line in self.calls()))
        self.calls_reset()
        rc, out = self.builder("-g", "tanner,all", STUB_SERVICES="adbhoney cowrie")
        self.assertEqual(rc, 0, out)
        self.assertEqual(sorted(self.builds()), ["adbhoney", "cowrie"])
        self.calls_reset()
        rc, out = self.builder("-y", STUB_SERVICES="wordpot cowrie")
        self.assertEqual(rc, 0, out)
        self.assertEqual(sorted(self.builds()), ["cowrie", "wordpot"])
        self.assertIn("### [OK] - 2 of 2 images built", out)

    def test_setup_builder_is_gone(self):
        # it was a wrapper on --setup / --uninstall only, builder.sh has both itself
        self.assertFalse(os.path.exists(SETUP))
        rc, out = self.builder("-h")
        self.assertIn("--setup", out)
        self.assertIn("--uninstall", out)

    def test_only_on_linux(self):
        """Outside Linux (macOS, Windows without WSL2) it stops with rc 3 and where it runs, before it asks
        docker anything; -h shows everywhere. WSL2 says Linux."""
        for system, name in (("Darwin", "macOS"), ("MINGW64_NT-10.0-19045", "Windows (MINGW64_NT-10.0-19045)"),
                             ("CYGWIN_NT-10.0", "Windows (CYGWIN_NT-10.0)")):
            for args in ([], ["-y"], ["-L"], ["--check"], ["--setup"], ["--show-config"],
                         ["--set", "TPOT_BUILDER_JOBS=4"]):
                with self.subTest(system=system, args=args):
                    rc, out = self.builder(*args, STUB_SYSTEM=system)
                    self.assertEqual(rc, 3, out)
                    self.assertIn(f"### [ERROR] - Image Builder does not run on {name}.", out)
                    self.assertIn("runs on Linux: a T-Pot host, a build host or a VM, WSL2 on Windows", out)
                    self.assertEqual(self.calls(), [])
                    self.assertFalse(os.path.exists(self.local))
        rc, out = self.builder("-h", STUB_SYSTEM="Darwin")
        self.assertEqual(rc, 0, out)
        self.assertIn("Usage:", out)
        rc, out = self.builder("-y", STUB_SYSTEM="Linux")
        self.assertEqual(rc, 0, out)

    def test_versions_compare_like_ui_sh(self):
        # the compose version through fuUI_VERSION_GE of ui.sh (and its plain fallback), no copy of its own
        text = base.read("docker/_builder/builder.sh")
        block = text[text.index("# >>> plain fallback"):text.index("# <<< plain fallback")]
        rest = text.replace(block, "")
        self.assertNotIn("fuVERSION_GE", rest)
        self.assertIn("fuUI_VERSION_GE", rest)
        self.assertIn("fuUI_VERSION_GE ()", block)
        self.assertIn("fuUI_LINUX_ONLY ()", block)

    def test_status_line_names_the_platforms_of_t_pot(self):
        """The banner line about mybuilder: the two platforms T-Pot builds and how many more, not all of
        them (13 with the QEMU emulators wrap at 80 columns)."""
        result = self.bash("fuSTATUS", STUB_PLATFORMS=THIRTEEN)
        line = result.stdout.rstrip("\n")
        self.assertEqual(line, "Builder 'mybuilder': running, builds linux/amd64 and linux/arm64 (+11 more)")
        # a line of the banner, which leaves 76 of 80 columns
        self.assertLessEqual(len(line), 76)
        result = self.bash("fuSTATUS", STUB_PLATFORMS="linux/amd64*, linux/amd64/v2, linux/386")
        self.assertEqual(result.stdout.rstrip("\n"),
                         "Builder 'mybuilder': running, builds linux/amd64 (+2 more), not linux/arm64")
        result = self.bash("fuSTATUS", STUB_PLATFORMS="linux/386")
        self.assertEqual(result.stdout.rstrip("\n"),
                         "Builder 'mybuilder': running, 1 other platform, not linux/amd64 and linux/arm64")
        result = self.bash("fuSTATUS", STUB_NO_BUILDER="1")
        self.assertEqual(result.stdout.rstrip("\n"), "Builder 'mybuilder': not set up (Builder setup sets it up)")

    def test_platforms_are_matched_whole(self):
        """linux/amd64/v2 is not linux/amd64: --check, the run (fuENSURE_PLATFORMS) and the status line of
        the menu read the platforms of buildx the same way, an entry as a whole (BuildKit marks the
        native ones with *)."""
        for platforms, has in (("linux/amd64/v2, linux/arm64", "1"), ("linux/amd64*, linux/arm64", "0"),
                               ("linux/arm64/v8, linux/amd64", "1"), ("linux/amd64, linux/arm64", "0"),
                               ("linux/arm64", "1"), ("", "1")):
            with self.subTest(platforms=platforms):
                result = self.bash(f'fuHAS_PLATFORMS "{platforms}" linux/amd64 linux/arm64; echo "$?"')
                self.assertEqual(result.stdout.strip(), has)
        rc, out = self.builder("--check", STUB_PLATFORMS="linux/amd64/v2, linux/arm64")
        self.assertEqual(rc, 3, out)
        self.assertIn("It does not build linux/amd64", out)
        result = self.bash("fuSTATUS", STUB_PLATFORMS="linux/amd64/v2, linux/arm64")
        self.assertEqual(result.stdout.rstrip("\n"),
                         "Builder 'mybuilder': running, builds linux/arm64 (+1 more), not linux/amd64")

    def test_build_options_next_to_another_action_are_wrong(self):
        """--set / --unset, -L, --check, --setup and --uninstall build nothing: a build option next to them
        would be dropped without a word, so it is a wrong option (2) and nothing happens."""
        with open(self.local, "w", encoding="utf-8") as handle:
            handle.write("TPOT_BUILDER_JOBS=3\n")
        for args in (["--set", "TPOT_BUILDER_JOBS=8", "-y"], ["--set", "TPOT_BUILDER_JOBS=8", "-i", "cowrie"],
                     ["-p", "--set", "TPOT_BUILDER_JOBS=8"], ["--unset", "TPOT_BUILDER_JOBS", "-T"],
                     ["--set", "TPOT_BUILDER_JOBS=8", "-y", "-i", "cowrie", "-p"], ["--check", "-a", "arm64"],
                     ["--setup", "-n"], ["--uninstall", "-g", "nsm"], ["-L", "-j", "4"], ["--check", "-t", "1.2.3"]):
            with self.subTest(args=args):
                self.calls_reset()
                rc, out = self.builder(*args)
                self.assertEqual(rc, 2, out)
                self.assertIn("### [ERROR] - ", out)
                self.assertIn("-h shows the options.", out)
                self.assertEqual(self.calls(), [])
                with open(self.local, encoding="utf-8") as handle:
                    self.assertEqual(handle.read(), "TPOT_BUILDER_JOBS=3\n")
        # --show-config shows what the options of a run would take
        rc, out = self.builder("--show-config", "-j", "6", "--docker-repo", "me")
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"TPOT_BUILDER_JOBS=6\s+# option")

    def test_readme_says_what_a_run_empties_and_caches(self):
        """The README section of the builder: a run empties the logs of what it does, not all of them, and
        the load build of the smoke tests takes the cache only where the run built this host's platform."""
        readme = base.read("README.md")
        section = readme[readme.index("## Build the Images Yourself"):]
        section = section[:section.index("\n## ", 1)]
        self.assertNotIn("each run starts them anew", section)
        self.assertIn("the logs of other images stay", section)
        self.assertIn("without the cache after `-n`", section)
        text = base.read("docker/_builder/builder.sh")
        self.assertNotIn("the same and fast", text)

    @unittest.skipUnless(shutil.which("shellcheck"), "no shellcheck")
    def test_shellcheck(self):
        result = subprocess.run(["shellcheck", "-S", "warning", BUILDER], capture_output=True,
                                universal_newlines=True, cwd=REPO, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class RightsTest(BuilderHarness):

    def test_docker_decides_without_root_or_the_docker_group(self):
        # rootless Docker, a socket of its own (DOCKER_HOST) or a group by another name: it builds when
        # docker answers
        rc, out = self.builder("-y", STUB_USER="tester", STUB_GROUPS="tester staff")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.builds(), ["cowrie"])
        rc, out = self.builder("-y", STUB_USER="tester", STUB_GROUPS="tester docker")
        self.assertEqual(rc, 0, out)

    def test_a_docker_that_does_not_answer_stops(self):
        rc, out = self.builder("-y", STUB_USER="tester", STUB_INFO_RC="1")
        self.assertEqual(rc, 3, out)
        self.assertIn("### [ERROR] - Docker does not answer", out)
        for hint in ("with sudo", "sudo usermod -aG docker tester", "rootless Docker"):
            self.assertIn(hint, out)
        self.assertEqual(self.builds(), [])
        self.assertEqual([line.split(" | ")[0] for line in self.calls()], ["docker info"])
        # root: is it running; with sudo also that rootless Docker is another daemon for root
        rc, out = self.builder("-y", STUB_INFO_RC="1")
        self.assertEqual(rc, 3, out)
        self.assertIn("systemctl start docker", out)
        self.assertNotIn("usermod", out)
        self.assertNotIn("rootless", out)
        rc, out = self.builder("-y", STUB_INFO_RC="1", SUDO_UID="1000", SUDO_GID="1000")
        self.assertEqual(rc, 3, out)
        self.assertIn("rootless Docker", out)
        self.assertIn("-l off", out)

    def test_push_with_limit_needs_root(self):
        rc, out = self.builder("-p", STUB_USER="tester", STUB_GROUPS="tester docker")
        self.assertEqual(rc, 3, out)
        self.assertIn("sudo", out)
        self.assertIn("-l off", out)
        self.assertEqual(self.builds(), [])
        rc, out = self.builder("-p", "-l", "off", STUB_USER="tester", STUB_GROUPS="tester docker")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.builds(), ["cowrie"])
        self.assertFalse(any(line.startswith("tc ") for line in self.calls()))

    @unittest.skipIf(os.geteuid() == 0, "root writes everywhere")
    def test_a_log_folder_it_cannot_write(self):
        locked = os.path.join(self.home, "lo cked")
        os.makedirs(locked)
        os.chmod(locked, 0o500)
        self.addCleanup(os.chmod, locked, 0o700)
        rc, out = self.builder("-y", STUB_USER="tester", TPOT_BUILDER_LOG_DIR=os.path.join(locked, "log"))
        self.assertEqual(rc, 3, out)
        self.assertIn("Cannot write the logs", out)
        # user:group, and the folder quoted for the shell
        self.assertIn("sudo chown -R tester:tester " + locked.replace(" ", "\\ ") + "/log", out)
        self.assertEqual(self.builds(), [])

    def test_the_logs_go_back_to_the_sudo_user(self):
        """Run with sudo, the logs root wrote belong to the user of sudo again at the end (recursive,
        symlinks themselves), for a run of that user without sudo."""
        uid, gid = str(os.getuid()), str(os.getgid())
        rc, out = self.builder("-y", SUDO_UID=uid, SUDO_GID=gid)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.chowns(), [f"chown -R -h {uid}:{gid} -- {os.path.realpath(self.log)}"])
        # a failed run too: every end of a run, once
        self.calls_reset()
        rc, out = self.builder("-y", STUB_FAIL="cowrie", SUDO_UID=uid, SUDO_GID=gid)
        self.assertEqual(rc, 1, out)
        self.assertEqual(self.chowns(), [f"chown -R -h {uid}:{gid} -- {os.path.realpath(self.log)}"])

    def test_the_logs_stay_where_sudo_does_not_fit(self):
        uid, gid = str(os.getuid()), str(os.getgid())
        for case, extra in (("another user's folder", {"SUDO_UID": "4242", "SUDO_GID": "4242"}),
                            ("sudo by root", {"SUDO_UID": "0", "SUDO_GID": "0"}),
                            ("no sudo", {}),
                            ("not a number", {"SUDO_UID": uid + "x", "SUDO_GID": gid}),
                            ("no group", {"SUDO_UID": uid}),
                            ("not root", {"SUDO_UID": uid, "SUDO_GID": gid, "STUB_USER": "tester"})):
            with self.subTest(case=case):
                self.calls_reset()
                rc, out = self.builder("-y", **extra)
                self.assertEqual(rc, 0, out)
                self.assertEqual(self.chowns(), [])

    def test_a_link_in_the_log_path_is_not_followed_out(self):
        """A symlink anywhere in the path of the logs (the last part with a slash after it, or a part in
        the middle) leads to a folder of another owner: what is there stays theirs. chown -R would follow
        such a link, so the folder of the logs is resolved first and its real parent decides."""
        uid, gid = str(os.getuid()), str(os.getgid())
        # a folder of the user in a folder of root (/tmp), and a folder of root (/usr/lib, the logs cannot be
        # written there, the end of the run comes all the same)
        shared = tempfile.mkdtemp(prefix="tpot-shared-", dir="/tmp")
        self.addCleanup(shutil.rmtree, shared)
        link = os.path.join(self.home, "linked")
        os.symlink(shared, link)
        system = os.path.join(self.home, "system")
        os.symlink("/usr", system)
        for case, folder, code in (("the link with a slash", link + "/", 0), ("the link itself", link, 0),
                                   ("a link in the middle", system + "/lib", 3)):
            with self.subTest(case=case):
                self.calls_reset()
                rc, out = self.builder("-y", SUDO_UID=uid, SUDO_GID=gid, TPOT_BUILDER_LOG_DIR=folder)
                self.assertEqual(rc, code, out)
                self.assertEqual(self.chowns(), [])

    def test_a_linked_log_folder_of_the_user_goes_back(self):
        """The folder of the logs is a link to a folder of the user (another disk of theirs): with sudo the
        logs in the real folder go back to them, the link and what is outside stay as they are."""
        uid, gid = str(os.getuid()), str(os.getgid())
        disk = os.path.join(self.home, "disk")
        os.makedirs(os.path.join(disk, "logs"))
        link = os.path.join(self.home, "linked")
        os.symlink(os.path.join(disk, "logs"), link)
        real = os.path.realpath(os.path.join(disk, "logs"))
        for folder in (link, link + "/"):
            with self.subTest(folder=folder):
                self.calls_reset()
                rc, out = self.builder("-y", SUDO_UID=uid, SUDO_GID=gid, TPOT_BUILDER_LOG_DIR=folder)
                self.assertEqual(rc, 0, out)
                self.assertEqual(self.chowns(), [f"chown -R -h {uid}:{gid} -- {real}"])

    def test_the_end_of_a_run_is_done_once(self):
        # fuCANCEL ends with exit, which runs the EXIT trap again: one chown, one removal of the limit
        uid, gid = str(os.getuid()), str(os.getgid())
        result = self.bash('trap fuEXIT EXIT\nfuSTOP_CHILDREN () { :; }\nmyLIMIT_SET=1 myIF=eth0\n'
                           'mkdir -p "$myLOGDIR"\nfuCANCEL', SUDO_UID=uid, SUDO_GID=gid)
        self.assertEqual(result.returncode, 130, result.stdout + result.stderr)
        self.assertEqual(self.chowns(), [f"chown -R -h {uid}:{gid} -- {os.path.realpath(self.log)}"])
        self.assertEqual([line for line in self.calls() if line.startswith("tc ")], ["tc qdisc del dev eth0 root"])

    def test_a_signal_during_the_end_of_a_run_does_not_cut_it_short(self):
        """Ctrl+C or a TERM while the end of a run removes the limit: the logs still go back."""
        uid, gid = str(os.getuid()), str(os.getgid())
        self.stub("tc", "#!/bin/sh\necho \"tc $*\" >> \"$HOME/calls\"\n"
                        "case \"$*\" in *'qdisc del'*) kill -INT \"$PPID\"; sleep 0.2 ;; esac\nexit 0\n")
        result = self.bash('trap fuCANCEL INT TERM\nfuSTOP_CHILDREN () { :; }\nmyLIMIT_SET=1 myIF=eth0\n'
                           'mkdir -p "$myLOGDIR"\nfuEXIT\necho "end of the run"', SUDO_UID=uid, SUDO_GID=gid)
        self.assertEqual(self.chowns(), [f"chown -R -h {uid}:{gid} -- {os.path.realpath(self.log)}"], result.stdout + result.stderr)
        self.assertIn("end of the run", result.stdout)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_docker_down_for_a_member_of_the_docker_group(self):
        # the user may use Docker, it does not run: start it, no group to join
        rc, out = self.builder("-y", STUB_USER="tester", STUB_GROUPS="tester docker", STUB_INFO_RC="1")
        self.assertEqual(rc, 3, out)
        self.assertIn("sudo systemctl start docker", out)
        self.assertNotIn("usermod", out)
        # rootless Docker (its socket under /run/user): it is the user's own service
        rc, out = self.builder("-y", STUB_USER="tester", STUB_INFO_RC="1",
                               DOCKER_HOST="unix:///run/user/1000/docker.sock")
        self.assertEqual(rc, 3, out)
        self.assertIn("systemctl --user start docker", out)
        self.assertNotIn("usermod", out)

    def test_rootless_docker_and_the_upload_limit(self):
        """With rootless Docker root talks to another Docker (its builder, cache and login): sudo is no
        way to the upload limit, the hint names -l off only; the menu does not offer to quit for sudo."""
        for extra in ({"DOCKER_HOST": "unix:///run/user/1000/docker.sock"},
                      {"XDG_RUNTIME_DIR": "/home/tester/.run", "DOCKER_HOST": "unix:///home/tester/.run/docker.sock"},
                      {"STUB_ROOTLESS": "1"}):
            with self.subTest(extra=extra):
                self.calls_reset()
                rc, out = self.builder("-p", STUB_USER="tester", **extra)
                self.assertEqual(rc, 3, out)
                self.assertIn("rootless Docker", out)
                self.assertIn("-l off", out)
                self.assertNotIn("sudo", out)
                self.assertEqual(self.builds(), [])
        rc, out = at_terminal([], self.terminal_env(STUB_USER="tester", STUB_ROOTLESS="1"),
                              answers=MenuTest.PUSH_HUB + "2\n")
        self.assertEqual(rc, 0, out)
        self.assertIn("rootless Docker", out)
        self.assertIn("Push without a limit", out)
        self.assertNotIn("sudo", out)
        self.assertEqual(self.builds(), [])


class BuildTest(BuilderHarness):

    def test_builder_is_selected_explicitly(self):
        work = os.path.join(self.home, "elsewhere")
        os.makedirs(work)
        result = self.run_script(BUILDER, "-y", env=self.env(), cwd=work)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        build = [line for line in self.calls() if " build cowrie" in line]
        self.assertEqual(len(build), 1, self.calls())
        # the compose file of the builder from any folder, its .env, and the builder by name
        self.assertIn(f"compose --env-file {os.path.dirname(COMPOSE)}/.env -f {COMPOSE} --progress plain "
                      "build cowrie --builder mybuilder", build[0])
        self.assertNotIn("--push", build[0])
        self.assertFalse(os.path.exists(os.path.join(self.log, "override.yml")))
        with open(os.path.join(self.log, "cowrie.log"), encoding="utf-8") as handle:
            self.assertIn("built", handle.read())

    def test_host_arch_skips_qemu(self):
        for arch, platform in (("x86_64", "linux/amd64"), ("aarch64", "linux/arm64")):
            with self.subTest(arch=arch):
                self.calls_reset()
                rc, out = self.builder("-a", "host", STUB_ARCH=arch, STUB_PLATFORMS=platform)
                self.assertEqual(rc, 0, out)
                self.assertFalse(any("tonistiigi/binfmt" in line for line in self.calls()), self.calls())
                override = self.override()
                self.assertIn("  cowrie:\n    build:\n      platforms: !override\n        - " + platform, override)
                self.assertNotIn("tags:", override)
                self.assertTrue(any(f"-f {self.log}/override.yml" in line and " build cowrie" in line
                                    for line in self.calls()))

    def test_foreign_platform_needs_qemu_and_a_builder_that_has_it(self):
        rc, out = self.builder("-a", "arm64", STUB_ARCH="x86_64", STUB_PLATFORMS="linux/amd64")
        self.assertEqual(rc, 3, out)
        self.assertTrue(any("tonistiigi/binfmt --install all" in line for line in self.calls()))
        self.assertTrue(any("buildx stop mybuilder" in line for line in self.calls()))
        self.assertEqual(self.builds(), [])

    def test_binfmt_is_checked(self):
        os.makedirs(self.binfmt)
        with open(os.path.join(self.binfmt, "register"), "w", encoding="utf-8"):
            pass
        rc, out = self.builder("-y", STUB_ARCH="x86_64")
        self.assertEqual(rc, 3, out)
        self.assertIn("qemu-aarch64", out)
        self.assertEqual(self.builds(), [])
        with open(os.path.join(self.binfmt, "qemu-aarch64"), "w", encoding="utf-8") as out_file:
            out_file.write("enabled\ninterpreter /usr/bin/qemu-aarch64\n")
        rc, out = self.builder("-y", STUB_ARCH="x86_64")
        self.assertEqual(rc, 0, out)
        rc, out = self.builder("-y", STUB_ARCH="x86_64", STUB_BINFMT_RC="1")
        self.assertEqual(rc, 3, out)

    def test_compose_version_is_checked_for_the_override(self):
        rc, out = self.builder("-a", "host", STUB_PLATFORMS="linux/amd64", STUB_COMPOSE_VERSION="2.20.3")
        self.assertEqual(rc, 3, out)
        self.assertIn("2.24.4", out)
        self.assertEqual(self.builds(), [])
        rc, out = self.builder("-a", "host", STUB_PLATFORMS="linux/amd64", STUB_COMPOSE_VERSION="v2.24.4-desktop.1")
        self.assertEqual(rc, 0, out)

    def test_jobs_and_no_cache(self):
        rc, out = self.builder("-n", "-j", "3", STUB_SERVICES="adbhoney cowrie")
        self.assertEqual(rc, 0, out)
        builds = [line for line in self.calls() if " build " in line]
        self.assertEqual(len(builds), 2)
        self.assertTrue(all("--builder mybuilder --no-cache" in line for line in builds), builds)
        self.assertIn("3 builds at a time", out)

    def test_a_failed_build_exits_1(self):
        rc, out = self.builder("-i", "cowrie,adbhoney", STUB_FAIL="adbhoney")
        self.assertEqual(rc, 1, out)
        self.assertIn(f"### [FAILED] - Image adbhoney, see {self.log}/adbhoney.log", out)
        self.assertIn("### [FAILED] - 1 of 2 images built", out)

    def test_version_and_repo_override(self):
        # myUI_VERSION only changes the credits of the banner, never the version that is built
        rc, out = self.builder("-y", myUI_VERSION="1.2.3")
        self.assertEqual(rc, 0, out)
        build = [line for line in self.calls() if " build cowrie" in line][0]
        # the version of the file version, which wins over docker/_builder/.env in compose
        self.assertIn(f"TPOT_VERSION={VERSION} ", build)
        self.assertIn(f"Version {VERSION},", out)
        self.calls_reset()
        rc, out = self.builder("-t", "99.1.0", "--docker-repo", "me", "--ghcr-repo=ghcr.io/me")
        self.assertEqual(rc, 0, out)
        build = [line for line in self.calls() if " build cowrie" in line][0]
        self.assertIn("TPOT_VERSION=99.1.0 TPOT_DOCKER_REPO=me TPOT_GHCR_REPO=ghcr.io/me", build)
        self.assertIn("Version 99.1.0, me and ghcr.io/me", out)

    def test_release_version_order(self):
        """The release version: the file version, else TPOT_VERSION of the environment, else the one of
        docker/_builder/.env; never the .env of the checkout's root (an installed T-Pot's)."""
        with open(ENVFILE, encoding="utf-8") as handle:
            builder_env = re.search(r"^TPOT_VERSION=(\S+)", handle.read(), re.M).group(1)
        root = os.path.join(self.home, "root")
        os.makedirs(root)
        with open(os.path.join(root, ".env"), "w", encoding="utf-8") as out:
            out.write("TPOT_VERSION=7.7.7\n")
        script = f'myREPO="{root}"\nfuRELEASE'
        self.assertEqual(self.bash(script).stdout.strip(), builder_env)
        self.assertEqual(self.bash(script, TPOT_VERSION="8.8.8").stdout.strip(), "8.8.8")
        with open(os.path.join(root, "version"), "w", encoding="utf-8") as out:
            out.write("6.6.6\n")
        self.assertEqual(self.bash(script, TPOT_VERSION="8.8.8").stdout.strip(), "6.6.6")
        self.assertEqual(self.bash(script, myUI_VERSION="1.2.3").stdout.strip(), "6.6.6")
        # -t is the version of one run, over all of them
        self.assertEqual(self.bash(f'myREPO="{root}" myTAG=5.5.5\nfuCONFIG; fuSETTINGS; echo "$myVER"').stdout.strip(),
                         "5.5.5")


class PushTest(BuilderHarness):

    def logins(self):
        return [line.split(" | ")[0] for line in self.calls() if line.startswith("docker login")]

    def test_push_needs_login_noninteractive(self):
        rc, out = self.builder("-p", "-l", "off", STUB_LOGIN_RC="1")
        self.assertEqual(rc, 3, out)
        self.assertIn("Not logged in to Docker Hub", out)
        self.assertIn("Not logged in to GHCR", out)
        self.assertIn("docker login ghcr.io", out)
        self.assertEqual(self.builds(), [])
        self.assertEqual(self.logins(), ["docker login", "docker login ghcr.io"])

    def test_a_login_that_hangs_is_given_up(self):
        started = time.time()
        rc, out = self.builder("--push-hub", "-l", "off", STUB_LOGIN_SLEEP="20", TPOT_BUILDER_LOGIN_TIMEOUT="1")
        self.assertEqual(rc, 3, out)
        self.assertLess(time.time() - started, 15)
        self.assertIn("did not answer within 1 s", out)

    def test_push_targets_per_registry(self):
        rc, out = self.builder("--push-hub", "-l", "off", "--docker-repo", "hub", "--ghcr-repo", "ghcr.io/me")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.logins(), ["docker login"])
        override = self.override()
        self.assertIn("  cowrie:\n    build:\n      tags: !reset []\n", override)
        self.assertNotIn("image:", override)
        self.assertNotIn("platforms", override)
        self.assertTrue(any(" build cowrie --builder mybuilder --push" in line for line in self.calls()))
        self.assertIn("Pushed: Docker Hub (hub)", out)
        os.remove(os.path.join(self.home, "calls"))
        rc, out = self.builder("--push-ghcr", "-l", "off", "--docker-repo", "hub", "--ghcr-repo", "ghcr.io/me")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.logins(), ["docker login ghcr.io"])
        self.assertIn(f"  cowrie:\n    image: ghcr.io/me/cowrie:{VERSION}\n    build:\n      tags: !reset []\n",
                      self.override())
        os.remove(os.path.join(self.home, "calls"))
        shutil.rmtree(self.log)
        rc, out = self.builder("-p", "-l", "off")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.logins(), ["docker login", "docker login ghcr.io"])
        self.assertFalse(os.path.exists(os.path.join(self.log, "override.yml")))
        self.assertNotIn("Remember to push", out)

    def test_upload_limit_only_when_pushing(self):
        rc, out = self.builder("-y")
        self.assertEqual(rc, 0, out)
        self.assertFalse(any(line.startswith(("tc ", "ip ")) for line in self.calls()), self.calls())
        rc, out = self.builder("-p")
        self.assertEqual(rc, 0, out)
        tc = [line for line in self.calls() if line.startswith("tc ")]
        self.assertIn("tc qdisc add dev eth0 root tbf rate 40mbit burst 32kbit latency 400ms", tc)
        self.assertEqual(tc[-1], "tc qdisc del dev eth0 root")
        # removed after the builds, before the summary
        self.assertLess(out.index("Upload limit on eth0 removed"), out.index("### Image Builder"))
        os.remove(os.path.join(self.home, "calls"))
        rc, out = self.builder("-p", "-l", "80mbit")
        self.assertEqual(rc, 0, out)
        self.assertTrue(any("rate 80mbit" in line for line in self.calls()))

    def test_limit_removed_on_failure(self):
        rc, out = self.builder("-p", STUB_FAIL="cowrie")
        self.assertEqual(rc, 1, out)
        self.assertIn("tc qdisc del dev eth0 root", self.calls())

    def test_own_limit_only(self):
        # a limit there is already is not this run's: stop, and leave it
        rc, out = self.builder("-p", STUB_TC_SHOW="qdisc tbf 8001: root refcnt 2 rate 10Mbit burst 4Kb lat 400ms")
        self.assertEqual(rc, 3, out)
        self.assertIn("eth0 has an upload limit (tbf) already", out)
        self.assertFalse(any("qdisc add" in line or "qdisc del" in line for line in self.calls()), self.calls())
        self.assertEqual(self.builds(), [])
        os.remove(os.path.join(self.home, "calls"))
        rc, out = self.builder("-p", STUB_TC_ADD_RC="2")
        self.assertEqual(rc, 3, out)
        self.assertFalse(any("qdisc del" in line for line in self.calls()), self.calls())

    def test_an_interrupt_removes_the_limit_and_exits_130(self):
        environment = dict(os.environ, HOME=self.home, PATH=f"{self.bin}:{os.environ['PATH']}", TPOT_GUM="off",
                           STUB_BUILD_SLEEP="20", **self.env())
        proc = subprocess.Popen(["bash", BUILDER, "-p"], env=environment, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
                                start_new_session=True)
        out = ""
        deadline = time.time() + 20
        while time.time() < deadline and "Building cowrie" not in out:
            line = proc.stdout.readline()
            if not line:
                break
            out += line
        self.assertIn("Building cowrie", out)
        time.sleep(0.3)
        os.killpg(proc.pid, signal.SIGINT)
        rest, _err = proc.communicate(timeout=20)
        self.assertEqual(proc.returncode, 130, out + rest)
        self.assertIn("tc qdisc del dev eth0 root", self.calls())

    def test_a_terminate_stops_the_builds_first(self):
        # SIGTERM to the builder alone (systemd, kill): the builds it started end too, before the upload
        # limit goes, so nothing goes on pushing without the limit
        pids = os.path.join(self.home, "build.pids")
        mark = os.path.join(self.home, "build.finished")
        environment = dict(os.environ, HOME=self.home, PATH=f"{self.bin}:{os.environ['PATH']}", TPOT_GUM="off",
                           STUB_BUILD_SLEEP="4", STUB_BUILD_PIDS=pids, STUB_BUILD_MARK=mark,
                           STUB_SERVICES="adbhoney cowrie", **self.env())
        started = time.time()
        proc = subprocess.Popen(["bash", BUILDER, "-p"], env=environment, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        deadline = time.time() + 20
        while time.time() < deadline:
            if os.path.exists(pids):
                with open(pids, encoding="utf-8") as handle:
                    if len(handle.read().split()) >= 4:
                        break
            time.sleep(0.05)
        with open(pids, encoding="utf-8") as handle:
            stubs = [int(pid) for pid in handle.read().split()]
        self.assertEqual(len(stubs), 4, stubs)
        proc.send_signal(signal.SIGTERM)
        self.assertEqual(proc.wait(timeout=20), 130)
        alive = [pid for pid in stubs if self.alive(pid)]
        self.assertEqual(alive, [], "the builds still run after the builder ended")
        time.sleep(max(0, started + 5 - time.time()))
        self.assertFalse(os.path.exists(mark), "a build finished after the builder ended")
        tc = [line for line in self.calls() if line.startswith("tc ")]
        self.assertEqual(tc[-1], "tc qdisc del dev eth0 root")

    @staticmethod
    def alive(pid):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True,
                               universal_newlines=True).stdout.strip()
        return bool(state) and not state.startswith("Z")

    def test_a_one_platform_push_needs_its_own_tag(self):
        # the release tag carries the images of both platforms, one platform pushed over it would replace them
        for args, arch in ((["-i", "cowrie", "-a", "host", "-p"], "amd64"), (["-a", "arm64", "--push-hub"], "arm64"),
                           (["-a", "amd64", "--push-ghcr", "-l", "off"], "amd64")):
            with self.subTest(args=args):
                rc, out = self.builder(*args)
                self.assertEqual(rc, 2, out)
                self.assertIn("### [ERROR] - ", out)
                self.assertIn("multi-arch", out)
                self.assertIn(f"-t {VERSION}-{arch}", out)
                self.assertEqual(self.calls(), [])
        rc, out = self.builder("-i", "cowrie", "-a", "host", "-p", STUB_ARCH="aarch64")
        self.assertEqual(rc, 2, out)
        self.assertIn(f"-t {VERSION}-arm64", out)
        self.assertEqual(self.calls(), [])
        # with a tag of its own it pushes
        rc, out = self.builder("-i", "cowrie", "-a", "host", "-p", "-t", "9.9.9-amd64", STUB_PLATFORMS="linux/amd64")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.builds(), ["cowrie"])
        build = [line for line in self.calls() if " build cowrie" in line][0]
        self.assertIn("--push", build)
        self.assertIn("TPOT_VERSION=9.9.9-amd64 ", build)
        # one platform without a push, both platforms with one: no tag needed
        os.remove(os.path.join(self.home, "calls"))
        rc, out = self.builder("-a", "host", STUB_PLATFORMS="linux/amd64")
        self.assertEqual(rc, 0, out)
        rc, out = self.builder("-a", "both", "-p")
        self.assertEqual(rc, 0, out)

    def test_a_one_platform_push_of_a_setting_names_the_setting(self):
        # no -a on the command line: the message names where the one platform comes from
        with open(self.local, "w", encoding="utf-8") as handle:
            handle.write("TPOT_BUILDER_ARCH=host\n")
        rc, out = self.builder("-p", "-l", "off")
        self.assertEqual(rc, 2, out)
        self.assertNotIn("(-a host)", out)
        self.assertIn(f"TPOT_BUILDER_ARCH=host of {self.local}", out)
        self.assertIn(f"-t {VERSION}-amd64", out)
        rc, out = self.builder("-p", "-l", "off", TPOT_BUILDER_ARCH="arm64")
        self.assertEqual(rc, 2, out)
        self.assertIn("TPOT_BUILDER_ARCH=arm64 of the environment", out)
        rc, out = self.builder("-p", "-l", "off", "-a", "arm64")
        self.assertEqual(rc, 2, out)
        self.assertIn("(-a arm64)", out)


class SmokeTest(BuilderHarness):

    def setUp(self):
        super().setUp()
        self.tests = os.path.join(self.home, "tests")
        os.makedirs(self.tests)
        for name in ("cowrie", "tpotinit_env", "tanner"):
            self.stub(f"{name}.sh", f"#!/bin/sh\necho \"test {name} $*\" >> \"$HOME/calls\"\n"
                                    f"[ \"${{STUB_TEST_FAIL:-}}\" = {name} ] && exit 1\nexit 0\n", self.tests)

    def test_smoke_test_mapping(self):
        result = self.bash('myHUB=hub myGHCR=ghcr.io/me myVER=1.0\n'
                           'fuSMOKE_PLAN cowrie tpotinit elasticsearch kibana logstash map nginx glutton redis '
                           'phpox tanner adbhoney\n'
                           'printf "N %s\\n" "${mySMOKE_NAMES[@]}"; printf "A %s\\n" "${mySMOKE_ARGS[@]}"\n'
                           'printf "I %s\\n" "${mySMOKE_IMAGES[@]}"; printf "W %s\\n" "${mySMOKE_NOTES[@]}"',
                           TPOT_BUILDER_TESTS_DIR=self.tests)
        lines = result.stdout.splitlines()
        self.assertEqual([line[2:] for line in lines if line.startswith("N ")], ["cowrie", "tpotinit_env"])
        self.assertEqual([line[2:] for line in lines if line.startswith("A ")],
                         ["--image hub/cowrie:1.0", "--image hub/tpotinit:1.0"])
        self.assertEqual([line[2:] for line in lines if line.startswith("I ")], ["cowrie", "tpotinit"])
        notes = [line[2:] for line in lines if line.startswith("W ")]
        self.assertEqual(len(notes), 2, notes)
        self.assertIn("No smoke test for adbhoney", notes[0])
        self.assertIn("Tanner test needs redis, phpox, tanner and snare", notes[1])
        result = self.bash('myHUB=hub myGHCR=ghcr.io/me myVER=1.0 myPUSH_GHCR=1\n'
                           'fuSMOKE_PLAN snare redis tanner phpox\n'
                           'printf "N %s\\n" "${mySMOKE_NAMES[@]}"; printf "A %s\\n" "${mySMOKE_ARGS[@]}"',
                           TPOT_BUILDER_TESTS_DIR=self.tests)
        self.assertEqual(result.stdout.splitlines(), [
            "N tanner",
            "A --redis-image ghcr.io/me/redis:1.0 --phpox-image ghcr.io/me/phpox:1.0 "
            "--tanner-image ghcr.io/me/tanner:1.0 --snare-image ghcr.io/me/snare:1.0"])

    def test_every_test_the_plan_names_is_in_the_repo(self):
        names = set()
        with open(COMPOSE, encoding="utf-8") as handle:
            services = re.findall(r"^  ([a-z0-9][a-z0-9_.-]*):\s*$", handle.read().split("\nservices:", 1)[1], re.M)
        result = self.bash("myHUB=h myGHCR=g myVER=1\nfuSMOKE_PLAN " + " ".join(services) +
                           '\nprintf "%s\\n" "${mySMOKE_NAMES[@]}"\n'
                           '[ "${#mySMOKE_NOTES[@]}" -eq 0 ] || printf "W %s\\n" "${mySMOKE_NOTES[@]}"')
        self.assertEqual(result.returncode, 0, result.stderr)
        for line in result.stdout.splitlines():
            if not line.startswith("W "):
                names.add(line)
        self.assertEqual([line for line in result.stdout.splitlines() if line.startswith("W ")], [])
        for name in names:
            self.assertTrue(os.path.exists(os.path.join(REPO, "docker", "_tests", "tests", name + ".sh")), name)

    def test_smoke_tests_after_build(self):
        rc, out = self.builder("-i", "cowrie,tpotinit", "-T", "--docker-repo", "hub", STUB_TEST_FAIL="tpotinit_env",
                               TPOT_BUILDER_TESTS_DIR=self.tests)
        self.assertEqual(rc, 4, out)
        calls = self.calls()
        load = [i for i, line in enumerate(calls) if "override-load.yml" in line]
        tests = [i for i, line in enumerate(calls) if line.startswith("test ")]
        self.assertEqual(len(load), 1, calls)
        self.assertIn("build cowrie tpotinit --builder mybuilder", calls[load[0]])
        self.assertNotIn("--push", calls[load[0]])
        self.assertTrue(load[0] < tests[0], calls)
        self.assertEqual([calls[i] for i in tests], [f"test cowrie --image hub/cowrie:{VERSION}",
                                                     f"test tpotinit_env --image hub/tpotinit:{VERSION}"])
        override = self.override("override-load.yml")
        self.assertIn(f"  cowrie:\n    image: hub/cowrie:{VERSION}\n    build:\n      platforms: !override\n"
                      "        - linux/amd64\n      tags: !reset []\n", override)
        self.assertIn("### [OK] - Smoke test cowrie", out)
        self.assertIn(f"### [FAILED] - Smoke test tpotinit_env, see {self.log}/test-tpotinit_env.log", out)

    def test_smoke_tests_follow_the_selection_not_the_finish(self):
        # the first image finishes last: the load build and the tests still go in the order of the list
        rc, out = self.builder("-i", "cowrie,tpotinit", "-T", "--docker-repo", "hub", STUB_SLOW="cowrie",
                               TPOT_BUILDER_TESTS_DIR=self.tests)
        self.assertEqual(rc, 0, out)
        self.assertLess(out.index("### [OK] - Image tpotinit"), out.index("### [OK] - Image cowrie"), out)
        load = [line for line in self.calls() if "override-load.yml" in line]
        self.assertEqual(len(load), 1, self.calls())
        self.assertIn("build cowrie tpotinit --builder mybuilder", load[0])
        self.assertEqual([line.split()[1] for line in self.calls() if line.startswith("test ")],
                         ["cowrie", "tpotinit_env"])

    def test_ctrl_c_under_a_spinner_cancels_the_run(self):
        """gum takes Ctrl+C as a key and fuUI_SPIN gives back 130: the run ends as cancelled (130),
        no further smoke test, no "builder failed" (3)."""
        stop = ('fuUI_SPIN () { case "$1" in *"$STOP_AT"*) return 130 ;; esac; shift 2; "$@" > /dev/null; }\n'
                'fuSTOP_CHILDREN () { :; }\nfuLIMIT_OFF () { echo limit-off; }\n')
        result = self.bash(stop + 'myHUB=hub myGHCR=ghcr.io/me myVER=1.0 myHOST=amd64 myPLATFORMS=(linux/amd64)\n'
                           'myLOGDIR="$TPOT_BUILDER_LOG_DIR"; mkdir -p "$myLOGDIR"\n'
                           'fuSMOKE_TESTS cowrie tpotinit; echo "rc=$?"',
                           TPOT_BUILDER_TESTS_DIR=self.tests, STOP_AT="Smoke test cowrie")
        self.assertEqual(result.returncode, 130, result.stdout + result.stderr)
        self.assertNotIn("rc=", result.stdout)
        self.assertIn("limit-off", result.stdout)
        self.assertFalse(any(line.startswith("test ") for line in self.calls()), self.calls())
        result = self.bash(stop + 'myHOST=amd64 myLOG=/dev/null\nfuPREPARE linux/amd64; echo "rc=$?"',
                           STOP_AT="Checking the buildx builder")
        self.assertEqual(result.returncode, 130, result.stdout + result.stderr)
        self.assertNotIn("rc=3", result.stdout)

    def test_a_failed_load_build_skips_the_tests(self):
        rc, out = self.builder("-i", "cowrie", "-T", STUB_LOAD_RC="1", TPOT_BUILDER_TESTS_DIR=self.tests)
        self.assertEqual(rc, 4, out)
        self.assertFalse(any(line.startswith("test ") for line in self.calls()))
        self.assertIn("The build for the smoke tests", out)

    def test_no_load_build_for_host_without_push(self):
        rc, out = self.builder("-i", "cowrie", "-a", "host", "-T", STUB_PLATFORMS="linux/amd64",
                               TPOT_BUILDER_TESTS_DIR=self.tests)
        self.assertEqual(rc, 0, out)
        calls = self.calls()
        self.assertFalse(any("override-load.yml" in line for line in calls), calls)
        self.assertEqual(self.builds(), ["cowrie"])
        hub = re.search(r"TPOT_DOCKER_REPO=(\S+)", [line for line in calls if " build cowrie" in line][0]).group(1)
        self.assertIn(f"test cowrie --image {hub}/cowrie:{VERSION}", calls)

    def test_compose_version_is_checked_for_the_smoke_test_build(self):
        """The build for the smoke tests has an override (platforms: !override, tags: !reset) as well: an
        old compose stops before any build, not after all of them."""
        rc, out = self.builder("-i", "cowrie", "-T", STUB_COMPOSE_VERSION="2.20.3", TPOT_BUILDER_TESTS_DIR=self.tests)
        self.assertEqual(rc, 3, out)
        self.assertIn("### [ERROR] - docker compose 2.20.3 is too old: the smoke test build (-T) needs 2.24.4.", out)
        self.assertFalse(any(" build " in line for line in self.calls()), self.calls())
        # no test for the image, or this host's platform without a push (no load build): no override
        for args in (["-i", "nginx", "-T"], ["-i", "cowrie"]):
            with self.subTest(args=args):
                rc, out = self.builder(*args, STUB_COMPOSE_VERSION="2.20.3", TPOT_BUILDER_TESTS_DIR=self.tests)
                self.assertEqual(rc, 0, out)
        rc, out = self.builder("-i", "cowrie", "-T", STUB_COMPOSE_VERSION="2.20.3", STUB_ARCH="sparc64",
                               TPOT_BUILDER_TESTS_DIR=self.tests)
        self.assertNotIn("too old", out)
        # each reason of this run
        # -T of this host's platform without a push needs no load build, but -a host is an override itself:
        # every run with a smoke test needs 2.24.4, as --check says
        for args, text in ((["-a", "host"], "-a host needs 2.24.4."),
                           (["-a", "host", "-i", "cowrie", "-T"], "-a host needs 2.24.4."),
                           (["-a", "amd64", "-i", "cowrie", "-T"], "-a amd64 needs 2.24.4."),
                           (["--push-hub", "-l", "off"], "the push to one registry needs 2.24.4."),
                           (["-a", "amd64", "--push-ghcr", "-l", "off", "-t", "9.9.9-amd64", "-i", "cowrie", "-T"],
                            "-a amd64, the push to one registry and the smoke test build (-T) need 2.24.4."),
                           (["-a", "arm64", "-i", "cowrie", "-T"],
                            "-a arm64 and the smoke test build (-T) need 2.24.4.")):
            with self.subTest(args=args):
                self.calls_reset()
                rc, out = self.builder(*args, STUB_COMPOSE_VERSION="2.20.3", TPOT_BUILDER_TESTS_DIR=self.tests)
                self.assertEqual(rc, 3, out)
                self.assertIn("docker compose 2.20.3 is too old: " + text, out)
                self.assertFalse(any(" build " in line for line in self.calls()), self.calls())

    def test_old_overrides_and_logs_are_cleared(self):
        # override files of an earlier run go before a run writes the ones it needs, load.log is new
        os.makedirs(self.log)
        for name in ("override.yml", "override-load.yml", "load.log"):
            with open(os.path.join(self.log, name), "w", encoding="utf-8") as out:
                out.write("stale\n")
        rc, out = self.builder("-y")
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.exists(os.path.join(self.log, "override.yml")))
        self.assertFalse(os.path.exists(os.path.join(self.log, "override-load.yml")))
        rc, out = self.builder("-i", "cowrie", "-T", TPOT_BUILDER_TESTS_DIR=self.tests)
        self.assertEqual(rc, 0, out)
        with open(os.path.join(self.log, "load.log"), encoding="utf-8") as handle:
            self.assertNotIn("stale", handle.read())

    def test_tests_of_another_platform_say_what_they_tested(self):
        # -a arm64 on amd64: the smoke tests ran a build for this host, not the arm64 images
        rc, out = self.builder("-i", "cowrie", "-a", "arm64", "-T", STUB_ARCH="x86_64", TPOT_BUILDER_TESTS_DIR=self.tests)
        self.assertEqual(rc, 0, out)
        self.assertIn("### [WARNING] - The smoke tests tested a linux/amd64 build, not the linux/arm64 images", out)
        rc, out = self.builder("-i", "cowrie", "-T", STUB_ARCH="x86_64", TPOT_BUILDER_TESTS_DIR=self.tests)
        self.assertEqual(rc, 0, out)
        self.assertNotIn("tested a", out)

    def test_the_load_build_takes_the_cache(self):
        # -n is for the images of the run; the build for the tests rebuilds them from the cache, on purpose
        rc, out = self.builder("-n", "-i", "cowrie", "-T", TPOT_BUILDER_TESTS_DIR=self.tests)
        self.assertEqual(rc, 0, out)
        load = [line for line in self.calls() if "override-load.yml" in line]
        main = [line for line in self.calls() if " build cowrie --builder" in line and "override-load" not in line]
        self.assertEqual(len(load), 1, self.calls())
        self.assertNotIn("--no-cache", load[0])
        self.assertNotIn("--load", load[0])
        self.assertIn("--no-cache", main[0])

    def test_the_load_build_of_a_platform_the_run_did_not_build(self):
        """-a arm64 -T on amd64: the run built no linux/amd64 image, the load build is a build of its own,
        and with -n it is one without the cache too (no layers of an earlier amd64 build)."""
        rc, out = self.builder("-n", "-i", "cowrie", "-a", "arm64", "-T", STUB_ARCH="x86_64",
                               TPOT_BUILDER_TESTS_DIR=self.tests)
        self.assertEqual(rc, 0, out)
        load = [line for line in self.calls() if "override-load.yml" in line]
        self.assertEqual(len(load), 1, self.calls())
        self.assertIn("--no-cache", load[0])
        self.calls_reset()
        rc, out = self.builder("-i", "cowrie", "-a", "arm64", "-T", STUB_ARCH="x86_64",
                               TPOT_BUILDER_TESTS_DIR=self.tests)
        self.assertEqual(rc, 0, out)
        load = [line for line in self.calls() if "override-load.yml" in line]
        self.assertNotIn("--no-cache", load[0])

    def test_a_failed_image_is_not_tested(self):
        rc, out = self.builder("-i", "cowrie,tpotinit", "-T", "-a", "host", STUB_PLATFORMS="linux/amd64",
                               STUB_FAIL="cowrie", TPOT_BUILDER_TESTS_DIR=self.tests)
        self.assertEqual(rc, 1, out)
        self.assertEqual([line.split()[1] for line in self.calls() if line.startswith("test ")], ["tpotinit_env"])


class SetupTest(BuilderHarness):

    def test_check_with_a_ready_builder(self):
        rc, out = self.builder("--check")
        self.assertEqual(rc, 0, out)
        self.assertIn("### [OK] - It builds linux/arm64", out)
        self.assertFalse(any(" build " in line or "binfmt" in line for line in self.calls()))

    def test_check_without_a_platform_or_a_builder(self):
        rc, out = self.builder("--check", STUB_PLATFORMS="linux/amd64")
        self.assertEqual(rc, 3, out)
        self.assertIn("It does not build linux/arm64", out)
        rc, out = self.builder("--check", STUB_NO_BUILDER="1")
        self.assertEqual(rc, 3, out)
        self.assertIn("--setup sets it up", out)
        os.makedirs(self.binfmt)
        with open(os.path.join(self.binfmt, "register"), "w", encoding="utf-8"):
            pass
        rc, out = self.builder("--check", STUB_ARCH="x86_64")
        self.assertEqual(rc, 3, out)
        self.assertIn("No QEMU handler for linux/arm64", out)

    def test_setup(self):
        rc, out = self.builder("--setup")
        self.assertEqual(rc, 0, out)
        calls = [line.split(" | ")[0] for line in self.calls()]
        self.assertIn("docker buildx inspect mybuilder --bootstrap", calls)
        self.assertIn("docker run --rm --privileged tonistiigi/binfmt --install all", calls)
        self.assertEqual(self.builds(), [])
        rc, out = self.builder("--setup", STUB_NO_BUILDER="1")
        self.assertEqual(rc, 3, out)
        self.assertTrue(any("buildx create --name mybuilder --driver docker-container" in line for line in self.calls()))

    def test_uninstall(self):
        rc, out = self.builder("--uninstall")
        self.assertEqual(rc, 0, out)
        calls = [line.split(" | ")[0] for line in self.calls()]
        self.assertIn("docker buildx rm mybuilder", calls)
        self.assertIn("No QEMU emulation registered, skipped", out)
        # a QEMU handler that stays: the uninstall partly failed
        os.makedirs(self.binfmt)
        with open(os.path.join(self.binfmt, "qemu-aarch64"), "w", encoding="utf-8") as handle:
            handle.write("enabled\n")
        rc, out = self.builder("--uninstall")
        self.assertEqual(rc, 3, out)
        self.assertIn("Could not remove the QEMU emulation", out)

    def test_setup_and_check_need_a_docker_that_answers(self):
        # not root and not in the docker group: Docker decides (rootless Docker, a group of another name)
        for action in ("--check", "--setup", "--uninstall"):
            with self.subTest(action=action):
                rc, out = self.builder(action, STUB_USER="tester", STUB_GROUPS="tester")
                self.assertEqual(rc, 0, out)
                rc, out = self.builder(action, STUB_USER="tester", STUB_GROUPS="tester", STUB_INFO_RC="1")
                self.assertEqual(rc, 3, out)
                self.assertIn("Docker does not answer", out)

    def test_a_failed_qemu_setup_names_rootful_docker(self):
        """binfmt_misc is the host's: tonistiigi/binfmt registers it only with a Docker that runs as root.
        The hint for it only where Docker is rootless; root, or a user of the docker group, has a Docker
        of root already, the log says why it failed."""
        for extra in ({"STUB_USER": "tester", "STUB_ROOTLESS": "1"},
                      {"STUB_USER": "tester", "DOCKER_HOST": "unix:///run/user/1000/docker.sock"}):
            with self.subTest(extra=extra):
                rc, out = self.builder("--setup", STUB_BINFMT_RC="1", **extra)
                self.assertEqual(rc, 3, out)
                self.assertIn("Docker running as root (sudo)", out)
                self.assertIn("rootless Docker cannot register it", out)
        for extra in ({}, {"STUB_USER": "tester", "STUB_GROUPS": "tester docker"}):
            with self.subTest(extra=extra):
                rc, out = self.builder("--setup", STUB_BINFMT_RC="1", **extra)
                self.assertEqual(rc, 3, out)
                self.assertNotIn("sudo", out)
                self.assertNotIn("rootless", out)
                self.assertIn(f"QEMU could not be set up, see {self.log}/builder.log", out)

    def test_check_names_the_smoke_tests_for_compose(self):
        rc, out = self.builder("--check", STUB_COMPOSE_VERSION="2.20.3")
        self.assertEqual(rc, 0, out)
        self.assertIn("### [WARNING] - docker compose 2.20.3: -a, the push to one registry and the smoke tests (-T) "
                      "need 2.24.4", out)


class SettingsTest(BuilderHarness):
    """docker/_builder/.env.local (untracked, TPOT_BUILDER_ENV_LOCAL in the tests): the settings of this
    checkout over docker/_builder/.env, an option or the environment over them."""

    def config(self, *args, **extra):
        rc, out = self.builder("--show-config", *args, **extra)
        self.assertEqual(rc, 0, out)
        found = {}
        for line in out.splitlines():
            match = re.match(r"^(TPOT_\w+)=(\S*)\s+# (.+)$", line)
            if match:
                found[match.group(1)] = (match.group(2), match.group(3))
        return found

    def read_local(self):
        with open(self.local, "rb") as handle:
            return handle.read()

    def test_env_local_is_not_in_git(self):
        self.assertIn("docker/_builder/.env.local", base.read(".gitignore").splitlines())

    def test_set_writes_the_file_and_names_its_origin(self):
        rc, out = self.builder("--set", "TPOT_BUILDER_JOBS=4", "--set", "TPOT_DOCKER_REPO=me")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.calls(), [])
        text = self.read_local().decode()
        self.assertIn("TPOT_BUILDER_JOBS=4\n", text)
        self.assertIn("TPOT_DOCKER_REPO=me\n", text)
        self.assertTrue(text.startswith("#"), text)
        config = self.config()
        self.assertEqual(config["TPOT_BUILDER_JOBS"], ("4", self.local))
        self.assertEqual(config["TPOT_DOCKER_REPO"], ("me", self.local))
        self.assertEqual(config["TPOT_GHCR_REPO"], ("ghcr.io/telekom-security", "docker/_builder/.env"))
        self.assertEqual(config["TPOT_BUILDER_ARCH"], ("both", "built in"))
        self.assertEqual(config["TPOT_BUILDER_LIMIT"], ("40mbit", "built in"))
        self.assertEqual(config["TPOT_VERSION"][0], VERSION)
        self.assertIn("the file version", config["TPOT_VERSION"][1])
        # and the run takes them
        rc, out = self.builder("-y")
        self.assertEqual(rc, 0, out)
        self.assertIn("4 builds at a time", out)
        build = [line for line in self.calls() if " build cowrie" in line][0]
        self.assertIn("TPOT_DOCKER_REPO=me ", build)

    def test_set_keeps_the_file(self):
        # in place: mode, inode, comments and CRLF stay; a key twice becomes one line (the last one counted)
        with open(self.local, "wb") as handle:
            handle.write(b"# mine\r\nTPOT_DOCKER_REPO=old\r\nTPOT_BUILDER_JOBS=3 # three\r\nTPOT_DOCKER_REPO=older\r\n")
        os.chmod(self.local, 0o600)
        inode = os.stat(self.local).st_ino
        self.assertEqual(self.config()["TPOT_DOCKER_REPO"], ("older", self.local))
        self.assertEqual(self.config()["TPOT_BUILDER_JOBS"], ("3", self.local))
        rc, out = self.builder("--set", "TPOT_DOCKER_REPO=new", "--set", "TPOT_BUILDER_LIMIT=off")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.read_local(), b"# mine\r\nTPOT_DOCKER_REPO=new\r\nTPOT_BUILDER_JOBS=3 # three\r\n"
                                            b"TPOT_BUILDER_LIMIT=off\r\n")
        self.assertEqual(os.stat(self.local).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.local).st_ino, inode)

    def test_wrong_keys_and_values_exit_2_and_change_nothing(self):
        with open(self.local, "w", encoding="utf-8") as handle:
            handle.write("TPOT_BUILDER_JOBS=3\n")
        before = self.read_local()
        for args in (["--set", "NOPE=1"], ["--set", "TPOT_VERSION=1.2.3"], ["--set", "TPOT_BUILDER_JOBS=17"],
                     ["--set", "TPOT_BUILDER_JOBS=0"], ["--set", "TPOT_BUILDER_ARCH=sparc"],
                     ["--set", "TPOT_BUILDER_LIMIT=fast"], ["--set", "TPOT_DOCKER_REPO=Not/Valid"],
                     ["--set", "TPOT_GHCR_REPO=a b"], ["--set", "TPOT_DOCKER_REPO=$(id)"], ["--set", "TPOT_BUILDER_JOBS"],
                     ["--set", "TPOT_BUILDER_ARCH=host", "--set", "TPOT_BUILDER_JOBS=99"], ["--unset", "NOPE"],
                     ["--unset", "TPOT_VERSION"], ["--set"], ["--set", "TPOT_BUILDER_JOBS=4", "--show-config"],
                     ["--set", "TPOT_BUILDER_JOBS=4", "--check"]):
            with self.subTest(args=args):
                rc, out = self.builder(*args)
                self.assertEqual(rc, 2, out)
                self.assertIn("### [ERROR] - ", out)
                self.assertEqual(self.read_local(), before)
        rc, out = self.builder("--set", "TPOT_VERSION=1.2.3")
        self.assertIn("file version", out)

    def test_unset(self):
        with open(self.local, "w", encoding="utf-8") as handle:
            handle.write("# mine\nTPOT_BUILDER_JOBS=4\nTPOT_BUILDER_ARCH=host\nTPOT_BUILDER_JOBS=5\n")
        rc, out = self.builder("--unset", "TPOT_BUILDER_JOBS")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.read_local(), b"# mine\nTPOT_BUILDER_ARCH=host\n")
        self.assertEqual(self.config()["TPOT_BUILDER_JOBS"], ("2", "built in"))
        # a key that is not there: nothing to do
        rc, out = self.builder("--unset", "TPOT_GHCR_REPO")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.read_local(), b"# mine\nTPOT_BUILDER_ARCH=host\n")
        # no file: none is made for it
        os.remove(self.local)
        rc, out = self.builder("--unset", "TPOT_GHCR_REPO")
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.exists(self.local))

    def test_option_over_environment_over_env_local_over_env(self):
        with open(self.local, "w", encoding="utf-8") as handle:
            handle.write("TPOT_DOCKER_REPO=local\nTPOT_BUILDER_ARCH=host\nTPOT_BUILDER_JOBS=3\n")

        def build_line(*args, **extra):
            self.calls_reset()
            rc, out = self.builder(*args, **extra)
            self.assertEqual(rc, 0, out)
            return [line for line in self.calls() if " build cowrie" in line][0], out

        line, out = build_line("-y")
        self.assertIn("TPOT_DOCKER_REPO=local ", line)
        self.assertIn(f"-f {self.log}/override.yml", line)
        self.assertIn("3 builds at a time", out)
        line, out = build_line("-y", TPOT_DOCKER_REPO="envrepo", TPOT_BUILDER_JOBS="5")
        self.assertIn("TPOT_DOCKER_REPO=envrepo ", line)
        self.assertIn("5 builds at a time", out)
        line, out = build_line("--docker-repo", "opt", "-a", "both", "-j", "6", TPOT_DOCKER_REPO="envrepo")
        self.assertIn("TPOT_DOCKER_REPO=opt ", line)
        self.assertNotIn("override.yml", line)
        self.assertIn("6 builds at a time", out)
        config = self.config("--docker-repo", "opt", TPOT_BUILDER_JOBS="5")
        self.assertEqual(config["TPOT_DOCKER_REPO"], ("opt", "option"))
        self.assertEqual(config["TPOT_BUILDER_JOBS"], ("5", "environment"))
        self.assertEqual(config["TPOT_BUILDER_ARCH"], ("host", self.local))

    def test_a_wrong_setting_stops_a_run(self):
        with open(self.local, "w", encoding="utf-8") as handle:
            handle.write("TPOT_BUILDER_JOBS=99\n")
        rc, out = self.builder("-y")
        self.assertEqual(rc, 2, out)
        self.assertIn("TPOT_BUILDER_JOBS=99", out)
        self.assertIn(self.local, out)
        self.assertEqual(self.builds(), [])
        # an option over it: not used, no stop; and --unset / --set still repair it
        rc, out = self.builder("-y", "-j", "2")
        self.assertEqual(rc, 0, out)
        rc, out = self.builder("--set", "TPOT_BUILDER_JOBS=8")
        self.assertEqual(rc, 0, out)
        rc, out = self.builder("-y", TPOT_BUILDER_ARCH="sparc")
        self.assertEqual(rc, 2, out)
        self.assertIn("TPOT_BUILDER_ARCH=sparc (environment)", out)
        # the file is read as text, never run: a command or a space in a value is a wrong value
        marker = os.path.join(self.home, "ran")
        for value in (f"$(touch {marker})", f'"`touch {marker}`"', "'a b'", "me ; touch " + marker):
            with self.subTest(value=value):
                with open(self.local, "w", encoding="utf-8") as handle:
                    handle.write(f"TPOT_DOCKER_REPO={value}\n")
                self.calls_reset()
                rc, out = self.builder("-y")
                self.assertEqual(rc, 2, out)
                self.assertIn("Not a repository", out)
                self.assertFalse(os.path.exists(marker))
                self.assertEqual(self.builds(), [])

    def test_a_failed_write_keeps_the_settings(self):
        """The new text is written in place in one go by the shell; where that write fails (a full disk)
        the old text goes back, rc 3, and a signal in the middle waits for the file to be whole."""
        old = b"# mine\nTPOT_DOCKER_REPO=me\n"
        with open(self.local, "wb") as handle:
            handle.write(old)
        # a cat that gives up half way (the way the file was written before): the file stays whole
        self.stub("cat", "#!/bin/sh\nhead -c 10 \"$1\"\nexit 1\n")
        rc, out = self.builder("--set", "TPOT_BUILDER_JOBS=8")
        if rc == 0:
            self.assertEqual(self.read_local(), old + b"TPOT_BUILDER_JOBS=8\n")
        else:
            self.assertEqual(rc, 3, out)
            self.assertEqual(self.read_local(), old)
        os.remove(os.path.join(self.bin, "cat"))
        with open(self.local, "wb") as handle:
            handle.write(old)
        # the write itself fails after half of it: the old text is back
        fail = ('fuWRITE_IN_PLACE () {\n  case "$2" in *JOBS=8*) printf "%s" "${2:0:5}" > "$1"; return 1 ;; esac\n'
                '  printf "%s" "$2" > "$1"\n}\n')
        result = self.bash(fail + 'fuSAVE_SETTINGS TPOT_BUILDER_JOBS=8; echo "rc=$?"')
        self.assertIn("rc=3", result.stdout, result.stdout + result.stderr)
        self.assertIn("Cannot write", result.stderr)
        self.assertEqual(self.read_local(), old)
        # Ctrl+C during the write: the file is whole first, then the run is cancelled (130)
        signal_in = ('fuWRITE_IN_PLACE () { kill -INT "$$"; printf "%s" "${2:0:5}" > "$1"; sleep 0.2;'
                     ' printf "%s" "$2" > "$1"; }\n')
        result = self.bash(signal_in + 'trap fuCANCEL INT TERM\nfuSTOP_CHILDREN () { :; }\n'
                           'fuSAVE_SETTINGS TPOT_BUILDER_JOBS=8; echo "rc=$?"')
        self.assertEqual(result.returncode, 130, result.stdout + result.stderr)
        self.assertNotIn("rc=", result.stdout)
        self.assertEqual(self.read_local(), old + b"TPOT_BUILDER_JOBS=8\n")

    @unittest.skipIf(os.geteuid() == 0, "root writes everywhere")
    def test_a_file_it_cannot_write_names_its_owner(self):
        # a file root left behind (a root login, a run of sudo -i): rc 3 and how to get it back
        with open(self.local, "w", encoding="utf-8") as handle:
            handle.write("TPOT_BUILDER_JOBS=3\n")
        os.chmod(self.local, 0o444)
        self.addCleanup(os.chmod, self.local, 0o644)
        for args in (["--set", "TPOT_BUILDER_JOBS=8"], ["--unset", "TPOT_BUILDER_JOBS"]):
            with self.subTest(args=args):
                rc, out = self.builder(*args, STUB_USER="tester")
                self.assertEqual(rc, 3, out)
                self.assertIn(f"Cannot write {self.local}.", out)
                self.assertIn(f"sudo chown tester:tester {self.local}", out)
                self.assertEqual(self.read_local(), b"TPOT_BUILDER_JOBS=3\n")

    def test_the_environment_over_a_saved_setting_is_named(self):
        # saved, but the environment of this shell wins over it: say so, or the next run surprises
        rc, out = self.builder("--set", "TPOT_BUILDER_JOBS=8", TPOT_BUILDER_JOBS="5")
        self.assertEqual(rc, 0, out)
        self.assertIn("### [WARNING] - TPOT_BUILDER_JOBS=5 of the environment wins over it", out)
        rc, out = self.builder("--unset", "TPOT_BUILDER_JOBS", TPOT_BUILDER_JOBS="5")
        self.assertEqual(rc, 0, out)
        self.assertIn("### [WARNING] - TPOT_BUILDER_JOBS=5 of the environment wins over it", out)
        rc, out = self.builder("--set", "TPOT_BUILDER_JOBS=8")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("WARNING", out)
        # the Settings of the menu: Docker Hub kept, GHCR kept, platforms kept, 4 at a time, limit kept, Save
        rc, out = at_terminal([], self.terminal_env(TPOT_BUILDER_JOBS="5"),
                              answers="3\n1\n1\n1\n4\n1\n1\n" + MenuTest.QUIT)
        self.assertEqual(rc, 0, out)
        self.assertIn("TPOT_BUILDER_JOBS=5 of the environment wins over it", out)

    def test_a_byte_order_mark_hides_no_key(self):
        # an editor of Windows writes a UTF-8 BOM before the first key
        with open(self.local, "wb") as handle:
            handle.write(b"\xef\xbb\xbfTPOT_DOCKER_REPO=me\nTPOT_BUILDER_JOBS=3\n")
        self.assertEqual(self.config()["TPOT_DOCKER_REPO"], ("me", self.local))
        rc, out = self.builder("--set", "TPOT_DOCKER_REPO=you")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.read_local(), b"\xef\xbb\xbfTPOT_DOCKER_REPO=you\nTPOT_BUILDER_JOBS=3\n")
        rc, out = self.builder("--unset", "TPOT_DOCKER_REPO")
        self.assertEqual(rc, 0, out)
        self.assertIn("removed", out)
        self.assertEqual(self.read_local(), b"\xef\xbb\xbfTPOT_BUILDER_JOBS=3\n")
        self.assertEqual(self.config()["TPOT_BUILDER_JOBS"], ("3", self.local))

    def checkout_copy(self):
        """A copy of the files of the checkout the builder reads, so .env.local is at its own place
        (docker/_builder/.env.local of that copy) and can be written."""
        root = os.path.join(self.home, "checkout")
        for path in ("version", "installer/lib/ui.sh", "docker/_builder/builder.sh", "docker/_builder/.env",
                     "docker/_builder/docker-compose.yml"):
            os.makedirs(os.path.dirname(os.path.join(root, path)), exist_ok=True)
            shutil.copy2(os.path.join(REPO, path), os.path.join(root, path))
        return root

    def test_the_settings_questions_fit_the_terminal(self):
        """The question of each setting names it, its value and where that comes from; with the values
        of a release in each place (.env.local, the environment, .env, built in) it fits in 76 columns
        (80 less the ### of the plain questions or the frame of gum)."""
        root = self.checkout_copy()
        builder = os.path.join(root, "docker", "_builder", "builder.sh")
        defaults = {"TPOT_DOCKER_REPO": "dtagdevsec", "TPOT_GHCR_REPO": "ghcr.io/telekom-security",
                    "TPOT_BUILDER_ARCH": "both", "TPOT_BUILDER_JOBS": "16", "TPOT_BUILDER_LIMIT": "800kbit"}
        local = os.path.join(root, "docker", "_builder", ".env.local")
        for case in (".env.local", "environment", ".env and built in"):
            with self.subTest(case=case):
                env = self.terminal_env()
                env.pop("TPOT_BUILDER_ENV_LOCAL")
                if case == ".env.local":
                    with open(local, "w", encoding="utf-8") as handle:
                        handle.write("".join(f"{key}={value}\n" for key, value in defaults.items()))
                elif os.path.exists(local):
                    os.remove(local)
                if case == "environment":
                    env.update(defaults)
                master, slave = pty.openpty()
                fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 120, 0, 0))
                proc = subprocess.Popen(["bash", builder], env=env, stdin=slave, stdout=slave, stderr=slave,
                                        close_fds=True)
                os.close(slave)
                os.write(master, ("3\n" + "1\n" * 5 + MenuTest.QUIT).encode())
                out = b""
                deadline = time.time() + 30
                while time.time() < deadline:
                    ready, _w, _x = select.select([master], [], [], 0.1)
                    if ready:
                        try:
                            data = os.read(master, 65536)
                        except OSError:
                            break
                        if not data:
                            break
                        out += data
                    elif proc.poll() is not None:
                        break
                proc.wait(timeout=5)
                os.close(master)
                text = out.decode("utf-8", "replace").replace("\r\n", "\n")
                self.assertEqual(proc.returncode, 0, text)
                headers = re.findall(r"### ([^#\n]*\(TPOT_\w+\)[^\n]*)$", text, re.M)
                self.assertEqual(len(headers), 5, text)
                for header in headers:
                    self.assertLessEqual(len(header), 76, header)

    def test_set_as_root_with_sudo_gives_a_new_file_back(self):
        uid, gid = str(os.getuid()), str(os.getgid())
        rc, out = self.builder("--set", "TPOT_BUILDER_JOBS=4", SUDO_UID=uid, SUDO_GID=gid)
        self.assertEqual(rc, 0, out)
        # the real path: the temporary folder of macOS is behind a link (/var is /private/var)
        self.assertIn(f"chown -R -h {uid}:{gid} -- {os.path.realpath(self.local)}", self.chowns())
        # a file there already keeps its owner, it is written in place
        self.calls_reset()
        rc, out = self.builder("--set", "TPOT_BUILDER_JOBS=5", SUDO_UID=uid, SUDO_GID=gid)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.chowns(), [])


class MenuTest(BuilderHarness):

    # main menu: build; all images; both platforms; no push to Docker Hub, none to GHCR; with cache;
    # 2 builds at a time (the default, first); no smoke tests; keep the version
    WALK = "1\n1\n1\nn\nn\nn\n1\nn\nn\n"

    # main menu: 1 build, 2 builder setup, 3 settings, 4 quit
    QUIT = "4\n"

    def test_menu_plain_cancel(self):
        # the summary of the menu, then Back: nothing is built, quit ends with 0
        rc, out = at_terminal([], self.terminal_env(), answers=self.WALK + "2\n" + self.QUIT)
        self.assertEqual(rc, 0, out)
        self.assertIn("### T-Pot Image Builder", out)
        self.assertIn("Builder 'mybuilder': running, builds linux/amd64 and linux/arm64", out)
        self.assertIn("### Ready to build", out)
        self.assertIn("### Images: all", out)
        self.assertIn("### [NEXT] - The same without the menu:", out)
        self.assertRegex(out, r"builder\.sh -y\n")
        self.assertEqual(self.builds(), [])
        self.assertFalse(any(" build " in line for line in self.calls()))

    def test_menu_builds_a_group(self):
        # groups: nsm (3); this host only; no push; without cache; 4 at a time; no tests; keep; Build
        answers = "1\n2\n3\n2\nn\nn\ny\n3\nn\nn\n1\n"
        rc, out = at_terminal([], self.terminal_env(STUB_PLATFORMS="linux/amd64"), answers=answers)
        self.assertEqual(rc, 0, out)
        self.assertIn("builder.sh -y -g nsm -a host -n -j 4", out)
        self.assertEqual(sorted(self.builds()), ["p0f", "suricata"])
        self.assertIn("### [OK] - 2 of 2 images built", out)

    def test_menu_one_platform_push_asks_for_a_tag(self):
        # all images; this host only; push to Docker Hub, not to GHCR; the tag; no limit; with cache;
        # 2 at a time; no tests; keep; Build
        answers = "1\n1\n2\ny\nn\n9.9.9-amd64\n2\nn\n1\nn\nn\n1\n"
        rc, out = at_terminal([], self.terminal_env(STUB_PLATFORMS="linux/amd64"), answers=answers)
        self.assertEqual(rc, 0, out)
        self.assertIn("multi-arch", out)
        self.assertIn(f"Tag for this push (i.e. {VERSION}-amd64, enter = no push):", out)
        self.assertIn("### Tag 9.9.9-amd64 for linux/amd64 only", out)
        self.assertIn("builder.sh -y -a host --push-hub -l off -t 9.9.9-amd64", out)
        build = [line for line in self.calls() if " build cowrie" in line][0]
        self.assertIn("--push", build)
        self.assertIn("TPOT_VERSION=9.9.9-amd64 ", build)

    def test_menu_one_platform_push_without_a_tag_does_not_push(self):
        # a bad tag is asked again, enter then means no push: no limit question, the summary, Back, Quit
        answers = "1\n1\n2\ny\ny\nbad tag\n\nn\n1\nn\nn\n2\n" + self.QUIT
        rc, out = at_terminal([], self.terminal_env(STUB_PLATFORMS="linux/amd64"), answers=answers)
        self.assertEqual(rc, 0, out)
        self.assertIn("Not a version tag: bad tag", out)
        self.assertNotIn("Upload limit while pushing", out)
        self.assertRegex(out, r"builder\.sh -y -a host\n")
        self.assertFalse(any(line.startswith("docker login") for line in self.calls()), self.calls())
        self.assertEqual(self.builds(), [])

    def test_menu_end_of_input_cancels(self):
        rc, out = at_terminal([], self.terminal_env(), answers="1\n\x04")
        self.assertEqual(rc, 130, out)
        self.assertEqual(self.builds(), [])

    def test_menu_quit(self):
        rc, out = at_terminal([], self.terminal_env(), answers=self.QUIT)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.builds(), [])

    # build; all images; both platforms; push to Docker Hub, not to GHCR: the question about the limit
    PUSH_HUB = "1\n1\n1\ny\nn\n"

    def test_menu_push_without_root_asks_about_the_limit(self):
        """The upload limit needs root: the menu of a user without it asks to push without one or to quit
        and run it with sudo, it does not decide alone."""
        rc, out = at_terminal([], self.terminal_env(STUB_USER="tester"), answers=self.PUSH_HUB + "2\n")
        self.assertEqual(rc, 0, out)
        self.assertIn("### Upload limit needs root (sudo)", out)
        self.assertIn("Push without a limit", out)
        self.assertIn("Quit, then run it with sudo", out)
        self.assertIn("sudo ", out[out.index("Quit, then run it with sudo"):])
        self.assertEqual(self.builds(), [])
        self.assertFalse(any(line.startswith("docker login") for line in self.calls()), self.calls())
        # off: with cache, 2 at a time, no tests, keep; the summary names -l off; Back, Quit
        rc, out = at_terminal([], self.terminal_env(STUB_USER="tester"),
                              answers=self.PUSH_HUB + "1\nn\n1\nn\nn\n2\n" + self.QUIT)
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"builder\.sh -y --push-hub -l off\n")
        self.assertEqual(self.builds(), [])
        # TPOT_BUILDER_LIMIT=off: nothing to ask
        with open(self.local, "w", encoding="utf-8") as handle:
            handle.write("TPOT_BUILDER_LIMIT=off\n")
        rc, out = at_terminal([], self.terminal_env(STUB_USER="tester"),
                              answers=self.PUSH_HUB + "n\n1\nn\nn\n2\n" + self.QUIT)
        self.assertEqual(rc, 0, out)
        self.assertNotIn("Upload limit needs root", out)
        self.assertRegex(out, r"builder\.sh -y --push-hub\n")

    def test_menu_the_command_line_names_what_the_environment_set(self):
        """The command line of the summary is the same run elsewhere (cron, sudo without the environment):
        a value of the environment is in it, one of .env.local or .env goes without saying."""
        env = self.terminal_env(STUB_PLATFORMS="linux/amd64", TPOT_BUILDER_JOBS="5", TPOT_BUILDER_ARCH="host",
                                TPOT_DOCKER_REPO="envrepo")
        # build; all images; this host only (the default); no push; with cache; 5 at a time (the default);
        # no tests; keep; Back; Quit
        rc, out = at_terminal([], env, answers="1\n1\n1\nn\nn\nn\n1\nn\nn\n2\n" + self.QUIT)
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"builder\.sh -y -a host -j 5 --docker-repo envrepo\n")
        # the same values from .env.local: implied
        with open(self.local, "w", encoding="utf-8") as handle:
            handle.write("TPOT_BUILDER_JOBS=5\nTPOT_BUILDER_ARCH=host\nTPOT_DOCKER_REPO=envrepo\n")
        rc, out = at_terminal([], self.terminal_env(STUB_PLATFORMS="linux/amd64"),
                              answers="1\n1\n1\nn\nn\nn\n1\nn\nn\n2\n" + self.QUIT)
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"builder\.sh -y\n")

    def test_menu_offers_the_default_first(self):
        """enter in gum takes the first item: the default of the settings comes first, for the platforms
        and the builds at a time as for the upload limit."""
        pick_first = ('fuUI_CHOOSE () { local h="$1"; shift; echo "Q $h | $*" >&2; local v="${1##*:}"; echo "$v"; }\n'
                      'fuUI_CONFIRM () { return 1; }\nfuUI_INPUT () { echo; }\n')
        for arch, jobs in (("host", "4"), ("arm64", "8"), ("both", "2"), ("amd64", "5")):
            with self.subTest(arch=arch, jobs=jobs):
                result = self.bash(pick_first + f'myCONF_ARCH={arch} myCONF_JOBS={jobs} myHOST=amd64\n'
                                   'fuMENU_OPTIONS; echo "$myARCH $myJOBS"')
                self.assertEqual(result.stdout.strip().splitlines()[-1], f"{arch} {jobs}", result.stderr)
                questions = [line for line in result.stderr.splitlines() if line.startswith("Q ")]
                self.assertIn("(the default)", questions[0].split(" | ")[1].split(":")[0], questions)

    def test_menu_settings(self):
        """Settings: every key one by one, Keep first; a summary of the changes, then Save or Back."""
        # Docker Hub: another one (me); GHCR: keep; platforms: this host only; 4 at a time; limit: keep;
        # Save; Quit
        answers = "3\n2\nme\n1\n3\n4\n1\n1\n" + self.QUIT
        rc, out = at_terminal([], self.terminal_env(), answers=answers)
        self.assertEqual(rc, 0, out)
        with open(self.local, encoding="utf-8") as handle:
            text = handle.read()
        lines = [line for line in text.splitlines() if line and not line.startswith("#")]
        self.assertEqual(sorted(lines), ["TPOT_BUILDER_ARCH=host", "TPOT_BUILDER_JOBS=4", "TPOT_DOCKER_REPO=me"])
        self.assertIn("### Builder settings", out)
        self.assertIn("--set TPOT_DOCKER_REPO=me", out)
        # Back keeps the file; a setting of the file can be removed again
        rc, out = at_terminal([], self.terminal_env(), answers="3\n1\n1\n2\n1\n1\n2\n" + self.QUIT)
        self.assertEqual(rc, 0, out)
        with open(self.local, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), text)
        # Docker Hub: remove (3); the rest kept; Save
        rc, out = at_terminal([], self.terminal_env(), answers="3\n3\n1\n1\n1\n1\n1\n" + self.QUIT)
        self.assertEqual(rc, 0, out)
        with open(self.local, encoding="utf-8") as handle:
            self.assertNotIn("TPOT_DOCKER_REPO", handle.read())
        # nothing changed: nothing to save
        rc, out = at_terminal([], self.terminal_env(), answers="3\n1\n1\n1\n1\n1\n" + self.QUIT)
        self.assertEqual(rc, 0, out)
        self.assertIn("Nothing changed", out)

    def test_an_option_at_a_terminal_never_asks(self):
        rc, out = at_terminal(["-y"], self.terminal_env())
        self.assertEqual(rc, 0, out)
        self.assertNotIn("Choice", out)
        self.assertEqual(self.builds(), ["cowrie"])
        os.remove(os.path.join(self.home, "calls"))
        # pushing without a login at a terminal: no question either, it stops
        rc, out = at_terminal(["-p", "-l", "off"], self.terminal_env(STUB_LOGIN_RC="1"))
        self.assertEqual(rc, 3, out)
        self.assertEqual(self.builds(), [])


if __name__ == "__main__":
    unittest.main()
