"""docker/_builder/builder.sh: options for unattended runs, the menu, the builder setup, the upload
limit, the push login and the smoke tests after a build.

The script runs with stubs for docker, whoami, id, ip, tc and uname (every call in $HOME/calls), its
logs in a temporary TPOT_BUILDER_LOG_DIR; it never calls the real docker and never writes the checkout.
"""

import fcntl
import os
import pty
import re
import select
import shutil
import signal
import struct
import subprocess
import termios
import time
import unittest

from tpotctl.tests import test_scripts as base

REPO = base.REPO
BUILDER = os.path.join(REPO, "docker", "_builder", "builder.sh")
SETUP = os.path.join(REPO, "docker", "_builder", "setup_builder.sh")
COMPOSE = os.path.join(REPO, "docker", "_builder", "docker-compose.yml")
VERSION = base.read("version").strip()

DOCKER = r"""#!/bin/sh
echo "docker $* | TPOT_VERSION=${TPOT_VERSION:-} TPOT_DOCKER_REPO=${TPOT_DOCKER_REPO:-} TPOT_GHCR_REPO=${TPOT_GHCR_REPO:-}" >> "$HOME/calls"
case "$*" in
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
    "whoami": "#!/bin/sh\necho \"${STUB_USER:-root}\"\n",
    "id": "#!/bin/sh\necho \"${STUB_GROUPS:-root}\"\n",
    "uname": "#!/bin/sh\necho \"${STUB_ARCH:-x86_64}\"\n",
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

    def stub(self, name, text, folder=None):
        path = os.path.join(folder or self.bin, name)
        with open(path, "w", encoding="utf-8") as out:
            out.write(text)
        os.chmod(path, 0o755)
        return path

    def env(self, **extra):
        env = {"TPOT_BUILDER_LOG_DIR": self.log, "TPOT_BINFMT_DIR": self.binfmt}
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
                     "--uninstall", "-h, --help"):
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

    def calls_reset(self):
        path = os.path.join(self.home, "calls")
        if os.path.exists(path):
            os.remove(path)

    def test_setup_wrapper_maps_old_flags(self):
        folder = os.path.join(self.home, "builder")
        os.makedirs(folder)
        shutil.copy(SETUP, folder)
        self.stub("builder.sh", "#!/usr/bin/env bash\necho \"ARGS $*\"\n", folder)
        for args, expected in ((["-y"], "ARGS --setup"), (["-u"], "ARGS --uninstall"), ([], "ARGS -h"),
                               (["-x"], "ARGS -h")):
            with self.subTest(args=args):
                result = self.run_script(os.path.join(folder, "setup_builder.sh"), *args)
                self.assertEqual(result.stdout.strip(), expected)
        result = self.run_script(SETUP)
        self.assertEqual(result.returncode, 0)
        self.assertIn("T-Pot Image Builder", result.stdout)

    @unittest.skipUnless(shutil.which("shellcheck"), "no shellcheck")
    def test_shellcheck(self):
        result = subprocess.run(["shellcheck", "-S", "warning", BUILDER, SETUP], capture_output=True,
                                universal_newlines=True, cwd=REPO, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class RightsTest(BuilderHarness):

    def test_without_root_or_docker_group_it_stops(self):
        rc, out = self.builder("-y", STUB_USER="tester", STUB_GROUPS="tester staff")
        self.assertEqual(rc, 3, out)
        self.assertIn("root or the docker group", out)
        self.assertIn("usermod -aG docker", out)
        self.assertEqual(self.builds(), [])

    def test_docker_group_suffices_without_push(self):
        rc, out = self.builder("-y", STUB_USER="tester", STUB_GROUPS="tester docker")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.builds(), ["cowrie"])

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
        locked = os.path.join(self.home, "locked")
        os.makedirs(locked)
        os.chmod(locked, 0o500)
        self.addCleanup(os.chmod, locked, 0o700)
        rc, out = self.builder("-y", TPOT_BUILDER_LOG_DIR=os.path.join(locked, "log"))
        self.assertEqual(rc, 3, out)
        self.assertIn("Cannot write the logs", out)
        self.assertIn("chown", out)
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

    def calls_reset(self):
        path = os.path.join(self.home, "calls")
        if os.path.exists(path):
            os.remove(path)

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
        rc, out = self.builder("-y")
        self.assertEqual(rc, 0, out)
        build = [line for line in self.calls() if " build cowrie" in line][0]
        # the version of the file version, which wins over docker/_builder/.env in compose
        self.assertIn(f"TPOT_VERSION={VERSION} ", build)
        self.calls_reset()
        rc, out = self.builder("-t", "99.1.0", "--docker-repo", "me", "--ghcr-repo=ghcr.io/me")
        self.assertEqual(rc, 0, out)
        build = [line for line in self.calls() if " build cowrie" in line][0]
        self.assertIn("TPOT_VERSION=99.1.0 TPOT_DOCKER_REPO=me TPOT_GHCR_REPO=ghcr.io/me", build)
        self.assertIn("Version 99.1.0, me and ghcr.io/me", out)


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

    def test_setup_and_check_need_rights(self):
        for action in ("--check", "--setup", "--uninstall"):
            with self.subTest(action=action):
                rc, out = self.builder(action, STUB_USER="tester", STUB_GROUPS="tester")
                self.assertEqual(rc, 3, out)


class MenuTest(BuilderHarness):

    # main menu: build; all images; both platforms; no push to Docker Hub, none to GHCR; with cache;
    # 2 builds at a time; no smoke tests; keep the version
    WALK = "1\n1\n1\nn\nn\nn\n2\nn\nn\n"

    def test_menu_plain_cancel(self):
        # the summary of the menu, then Back: nothing is built, quit ends with 0
        rc, out = at_terminal([], self.terminal_env(), answers=self.WALK + "2\n3\n")
        self.assertEqual(rc, 0, out)
        self.assertIn("### T-Pot Image Builder", out)
        self.assertIn("Builder 'mybuilder': running, linux/amd64, linux/arm64", out)
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
        answers = "1\n1\n2\ny\nn\n9.9.9-amd64\n2\nn\n2\nn\nn\n1\n"
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
        answers = "1\n1\n2\ny\ny\nbad tag\n\nn\n2\nn\nn\n2\n3\n"
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
        rc, out = at_terminal([], self.terminal_env(), answers="3\n")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.builds(), [])

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
