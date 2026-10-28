"""systemctl of the persona with the units a session writes, enables, starts and stops: the texts of
systemd's versions (refs/systemd-telnet.json of round 4, recorded in ubuntu:22.04, ubuntu:24.04, debian:12,
debian:13 and almalinux:9 with systemd as PID 1)."""
import copy
import functools
import json
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

from tests.helpers import DIST, base_tree
from tests.test_patch import FIXTURE, PATCH, FakeCommand, FakeConfig, module_all
from tpot_personas import catalog, system


class FileNotFound(Exception):  # noqa: N818 - as Cowrie's fs.FileNotFound: no OSError
    pass


class SessionFs:
    """The parts of Cowrie's HoneyPotFilesystem systemctl uses: files by path, links as nodes."""

    def __init__(self, files=None, dirs=("/", "/etc", "/etc/systemd", "/etc/systemd/system", "/etc/init.d")):
        # the program the test units run (systemd fails a start whose ExecStart= program is gone)
        self.files = {"/bin/sleep": b"\x7fELF", **(files or {})}
        self.dirs = set(dirs)
        self.links = {}

    def resolve_path(self, path, cwd):
        return posixpath.normpath(posixpath.join(cwd, path))

    def file_contents(self, path):
        if path in self.links:
            path = self.links[path][8]
        if path not in self.files:
            raise FileNotFound(path)
        return self.files[path]

    def exists(self, path):
        return path in self.dirs or path in self.files or (path in self.links and self.links[path][8] in self.files)

    def lexists(self, path):
        return path in self.dirs or path in self.files or path in self.links

    def mkdir(self, path, uid, gid, size, mode):
        self.dirs.add(path)

    def link_entry(self, entry, dirpath):
        assert dirpath in self.dirs, dirpath
        self.links[f"{dirpath}/{entry[0]}"] = entry

    def remove(self, path):
        if path not in self.links and path not in self.files:
            raise OSError(2, "No such file or directory")
        self.links.pop(path, None)
        self.files.pop(path, None)


X_UNIT = (b"[Unit]\nDescription=x test service\n\n[Service]\nExecStart=/bin/sleep 1000\n\n"
          b"[Install]\nWantedBy=multi-user.target\n")


@functools.cache
def patched_service():
    root = Path(tempfile.mkdtemp(prefix="tpot-systemctl-")) / "cowrie"
    shutil.copytree(FIXTURE, root)
    subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(root)], check=True, capture_output=True)
    text = (root / "src/cowrie/commands/service.py").read_text()
    shutil.rmtree(root.parent)
    return text


@functools.cache
def rendered(pid, seed=1):
    from tpot_personas.identity import draw
    from tpot_personas.render import render
    persona = catalog.by_id(pid)
    out = Path(tempfile.mkdtemp(prefix=f"tpot-{pid}-"))
    ident = draw(persona, seed)
    render(persona, ident, base_tree(), out / "r", runtime_dir=out / "r", keys_dir=out / "r" / "keys")
    return out / "r", ident


class Server:
    def __init__(self, processes):
        self.process = processes


class User:
    def __init__(self, processes):
        self.server = Server(processes)


class Session:
    """One login on a rendered persona: systemctl calls on one virtual server."""

    def __init__(self, pid, uid=0, files=None, polkit=None, tty=False):
        self.tty = tty
        out, self.ident = rendered(pid)
        self.out = out
        self.namespace = module_all(patched_service(), {"HoneyPotCommand": FakeCommand, "CowrieConfig": FakeConfig(
            {("shell", "systemctl_units"): str(out / "systemd-units.json"),
             ("shell", "network"): str(out / "network.json")})})
        if polkit is not None:
            # the persona's own state is in its units file; a test asks the other one too
            units = self.namespace["tpot_units"]
            self.namespace["tpot_units"] = lambda: {**units(), "polkit": polkit}
        self.fs = SessionFs(files)
        self.uid = uid
        self.processes = json.loads((out / "cmdoutput.json").read_text())["command"]["ps"]
        self.user = User(self.processes)
        self.boot = time.time() - 3 * 86400

    def __call__(self, *args):
        cmd = FakeCommand(args)
        if self.tty:
            # stdout is the terminal of an interactive session
            cmd.pp = types.SimpleNamespace(targets={1: ("terminal", None)})
        cmd.fs = self.fs
        cmd.user = {"uid": self.uid, "gid": self.uid, "username": "root" if self.uid == 0 else "user"}
        cmd.protocol.boot_time = lambda: self.boot
        cmd.protocol.hostname = self.ident.hostname
        cmd.protocol.user = self.user
        cmd.protocol.cmdstack = []
        code = self.namespace["tpot_systemctl"](cmd, list(args))
        return bytes(cmd.out).decode(), cmd.err, code


class SessionUnitTest(unittest.TestCase):
    def session(self, pid, **kwargs):
        run = Session(pid, files={"/etc/systemd/system/x.service": X_UNIT}, **kwargs)
        return run

    def test_enable_and_disable_a_unit_the_session_wrote(self):
        # refs: *-enable-before-reload, *-disable, *-enable-again, *-disable-again
        cases = {"ubuntu-jammy": ("Created symlink /etc/systemd/system/multi-user.target.wants/x.service \u2192 "
                                  "/etc/systemd/system/x.service.\n",
                                  "Removed /etc/systemd/system/multi-user.target.wants/x.service.\n"),
                 "ubuntu-noble": ("Created symlink /etc/systemd/system/multi-user.target.wants/x.service \u2192 "
                                  "/etc/systemd/system/x.service.\n",
                                  'Removed "/etc/systemd/system/multi-user.target.wants/x.service".\n'),
                 "debian-bookworm": ("Created symlink /etc/systemd/system/multi-user.target.wants/x.service \u2192 "
                                     "/etc/systemd/system/x.service.\n",
                                     'Removed "/etc/systemd/system/multi-user.target.wants/x.service".\n'),
                 "debian-trixie": ("Created symlink '/etc/systemd/system/multi-user.target.wants/x.service' \u2192 "
                                   "'/etc/systemd/system/x.service'.\n",
                                   "Removed '/etc/systemd/system/multi-user.target.wants/x.service'.\n"),
                 "rhel-9": ("Created symlink /etc/systemd/system/multi-user.target.wants/x.service \u2192 "
                            "/etc/systemd/system/x.service.\n",
                            'Removed "/etc/systemd/system/multi-user.target.wants/x.service".\n')}
        for pid, (created, removed) in cases.items():
            with self.subTest(pid):
                run = self.session(pid)
                self.assertEqual(run("is-enabled", "x"), ("disabled\n", "", 1))
                self.assertEqual(run("enable", "x"), ("", created, 0))
                self.assertIn("/etc/systemd/system/multi-user.target.wants/x.service", run.fs.links)
                self.assertEqual(run("enable", "x"), ("", "", 0))
                self.assertEqual(run("is-enabled", "x"), ("enabled\n", "", 0))
                self.assertEqual(run("disable", "x"), ("", removed, 0))
                self.assertEqual(run("disable", "x"), ("", "", 0))
                self.assertEqual(run("is-enabled", "x"), ("disabled\n", "", 1))

    def test_status_of_the_system_shows_the_services_of_the_session(self):
        # the CGroup tree of `systemctl status` is that of `systemctl status system.slice`: a service the
        #   session stops leaves both, one it starts is in both
        def services(out):
            return [line.strip()[2:] for line in out.splitlines()
                    if line.strip().endswith(".service") and line.strip()[:2] in ("├─", "└─")]

        for pid, ssh in (("debian-bookworm", "ssh.service"), ("rhel-9", "sshd.service")):
            with self.subTest(pid):
                run = self.session(pid)
                self.assertIn(ssh, services(run("status")[0]))
                self.assertEqual(run("stop", ssh), ("", "", 0))
                self.assertEqual(run("start", "x"), ("", "", 0))
                system, system_slice = services(run("status")[0]), services(run("status", "system.slice")[0])
                self.assertNotIn(ssh, system)
                self.assertIn("x.service", system)
                self.assertEqual(system, system_slice)

    def test_a_unit_without_install_section(self):
        run = Session("ubuntu-noble", files={"/etc/systemd/system/y.service":
                                             b"[Unit]\nDescription=y\n[Service]\nExecStart=/bin/sleep 1\n"})
        out, err, code = run("enable", "y")
        self.assertEqual((out, code), ("", 0))
        self.assertTrue(err.startswith("The unit files have no installation config (WantedBy=, RequiredBy=, "
                                       "UpheldBy=,\nAlso=, or Alias= settings"), err)
        self.assertTrue(err.endswith("  instance name specified.\n"), err)
        self.assertEqual(run("is-enabled", "y"), ("static\n", "", 0))
        run = Session("ubuntu-jammy", files={"/etc/systemd/system/y.service": b"[Service]\nExecStart=/bin/true\n"})
        self.assertIn("This means they are not meant to be enabled using systemctl.\n \n", run("enable", "y")[1])
        self.assertEqual(run("disable", "y"), ("", "", 0))

    def test_start_status_stop_of_a_unit_the_session_wrote(self):
        run = self.session("ubuntu-jammy")
        out, err, code = run("status", "x", "--no-pager")
        # ref ubuntu-22-04-status-before-start
        self.assertEqual((out, err, code), (
            "\u25cb x.service - x test service\n"
            "     Loaded: loaded (/etc/systemd/system/x.service; disabled; vendor preset: enabled)\n"
            "     Active: inactive (dead)\n", "", 3))
        self.assertEqual(run("is-active", "x"), ("inactive\n", "", 3))
        self.assertEqual(run("start", "x"), ("", "", 0))
        self.assertEqual(run("is-active", "x"), ("active\n", "", 0))
        out, _, code = run("status", "x")
        lines = out.splitlines()
        self.assertEqual(code, 0)
        self.assertEqual(lines[0], "\u25cf x.service - x test service")
        self.assertRegex(lines[2], r"^     Active: active \(running\) since \w{3} \d{4}-\d\d-\d\d \d\d:\d\d:\d\d UTC; "
                                   r"\d+(s|ms|us) ago$")
        self.assertRegex(lines[3], r"^   Main PID: (\d+) \(sleep\)$")
        pid = int(lines[3].split()[2])
        self.assertEqual(lines[4], "      Tasks: 1 (limit: %s)" % lines[4].split("limit: ")[1].rstrip(")"))
        self.assertRegex(lines[5], r"^     Memory: \d+\.\dK$")
        self.assertRegex(lines[6], r"^        CPU: \d+us$")
        self.assertEqual(lines[7:9], ["     CGroup: /system.slice/x.service", f"             \u2514\u2500{pid} /bin/sleep 1000"])
        self.assertEqual(lines[9], "")
        self.assertRegex(lines[10], r"^\w{3} \d\d \d\d:\d\d:\d\d \S+ systemd\[1\]: Started x test service\.$")
        self.assertEqual(len(lines), 11)
        # the process is in ps
        self.assertIn(pid, [p["PID"] for p in run.processes])
        self.assertEqual(next(p for p in run.processes if p["PID"] == pid)["COMMAND"], "/bin/sleep 1000")
        self.assertEqual(run("show", "x", "-p", "MainPID", "-p", "ActiveState"),
                         (f"MainPID={pid}\nActiveState=active\n", "", 0))
        self.assertEqual(run("stop", "x"), ("", "", 0))
        self.assertNotIn(pid, [p["PID"] for p in run.processes])
        out, _, code = run("status", "x")
        lines = out.splitlines()
        self.assertEqual(code, 3)
        self.assertEqual(lines[0], "\u25cb x.service - x test service")
        self.assertRegex(lines[2], r"^     Active: inactive \(dead\) since .+ UTC; \d+(s|ms|us) ago$")
        self.assertEqual(lines[3], f"    Process: {pid} ExecStart=/bin/sleep 1000 (code=killed, signal=TERM)")
        self.assertEqual(lines[4], f"   Main PID: {pid} (code=killed, signal=TERM)")
        self.assertRegex(lines[5], r"^        CPU: \d+us$")
        self.assertEqual([line.split(": ", 1)[1] for line in lines[7:]],
                         ["Started x test service.", "Stopping x test service...",
                          "x.service: Deactivated successfully.", "Stopped x test service."])

    def test_timespans_name_days_and_weeks(self):
        # systemd's format_timespan: y, month, w, d, h, min, s (a Duration of a daemon that ran since the boot)
        span = Session("debian-bookworm").namespace["tpot_timespan"]
        self.assertEqual(span(((307 * 60 + 47) * 60 + 32) * 1000 + 998), "1w 5d 19h 47min 32.998s")
        self.assertEqual(span(86400000), "1d")
        self.assertEqual(span(3 * 86400000 + 5000), "3d 5s")
        self.assertEqual(span(400 * 86400000), "1y 1month 4d 7h 30min")
        self.assertEqual(span(2 * 3600000 + 3000), "2h 3s")

    def test_a_start_whose_program_is_gone_fails(self):
        # ref debian-12-t12r2-start-without-binary: 203/EXEC, a forking unit's job fails with exit status 1
        gone = (b"[Unit]\nDescription=gone test service\n\n[Service]\nType=forking\nExecStart=/usr/sbin/gone -q\n")
        run = Session("debian-bookworm", files={"/etc/systemd/system/gone.service": gone})
        out, err, code = run("start", "gone")
        self.assertEqual((out, code), ("", 1))
        self.assertTrue(err.startswith("Job for gone.service failed because the control process exited with error"),
                        err)
        self.assertEqual(run("is-active", "gone"), ("failed\n", "", 3))
        out, _, code = run("status", "gone", "--no-pager")
        self.assertEqual(code, 3)
        self.assertIn("     Active: failed (Result: exit-code) since ", out)
        self.assertRegex(out, r"\n    Process: \d+ ExecStart=/usr/sbin/gone -q \(code=exited, status=203/EXEC\)\n")
        self.assertIn("gone.service: Failed to locate executable /usr/sbin/gone: No such file or directory\n", out)

    def test_status_of_the_versions(self):
        run = self.session("ubuntu-noble")
        run("start", "x")
        out, _, _ = run("status", "x")
        self.assertRegex(out, r"\n     Memory: \d+\.\dK \(peak: \d+\.\dM\)\n")
        self.assertTrue(out.endswith("systemd[1]: Started x.service - x test service.\n"), out)
        run("stop", "x")
        out, _, _ = run("status", "x")
        self.assertRegex(out, r"\n   Duration: \d+ms\n    Process: ")
        self.assertIn("systemd[1]: Stopping x.service - x test service...\n", out)
        run = self.session("debian-trixie")
        run("start", "x")
        out, _, _ = run("status", "x")
        self.assertRegex(out, r"UTC; \d+(s|ms|us) ago\n Invocation: [0-9a-f]{32}\n   Main PID: ")
        self.assertRegex(out, r"\n     Memory: \d+(\.\d)?K \(peak: \d+(\.\d)?M\)\n")
        run("stop", "x")
        out, _, _ = run("status", "x")
        self.assertRegex(out, r"\n   Duration: \d+ms\n Invocation: [0-9a-f]{32}\n    Process: ")
        self.assertRegex(out, r"\n   Mem peak: \d+(\.\d)?M\n        CPU: ")
        run = self.session("rhel-9")
        out, _, _ = run("status", "x")
        self.assertIn("(/etc/systemd/system/x.service; disabled; preset: disabled)", out)
        run("start", "x")
        self.assertRegex(run("status", "x")[0], r"\n     Memory: \d+\.\dK \(peak: \d+\.\dM\)\n")

    def test_enable_now_and_disable_now(self):
        run = self.session("debian-bookworm")
        out, err, code = run("enable", "--now", "x")
        self.assertEqual((out, code), ("", 0))
        self.assertIn("Created symlink ", err)
        self.assertEqual(run("is-active", "x"), ("active\n", "", 0))
        self.assertEqual(run("disable", "--now", "x")[2], 0)
        self.assertEqual(run("is-active", "x"), ("inactive\n", "", 3))
        self.assertEqual(run("is-enabled", "x"), ("disabled\n", "", 1))

    def test_list_units_shows_a_started_unit(self):
        run = self.session("debian-bookworm")
        out, _, _ = run("list-units", "--type=service", "--no-legend")
        self.assertNotIn("x.service", out)
        run("start", "x")
        out, _, _ = run("list-units", "--type=service", "--no-legend")
        self.assertRegex(out, r"\n  x\.service +loaded active running x test service\n")
        run("stop", "x")
        self.assertNotIn("x.service", run("list-units", "--type=service", "--no-legend")[0])
        self.assertRegex(run("list-units", "--type=service", "--all", "--no-legend")[0],
                         r"\n  x\.service +loaded inactive dead +x test service\n")

    def test_errors_of_missing_units(self):
        # refs *-enable-nosuch, *-disable-nosuch, *-start-nosuch, *-stop-nosuch, *-restart-nosuch, *-status-nosuch
        for pid, enable in (("ubuntu-jammy", "Failed to enable unit: Unit file nosuch.service does not exist.\n"),
                            ("debian-trixie", "Failed to enable unit: Unit nosuch.service does not exist\n")):
            with self.subTest(pid):
                run = self.session(pid)
                self.assertEqual(run("enable", "nosuch"), ("", enable, 1))
                self.assertEqual(run("disable", "nosuch"), ("", enable.replace("enable", "disable"), 1))
                self.assertEqual(run("start", "nosuch"),
                                 ("", "Failed to start nosuch.service: Unit nosuch.service not found.\n", 5))
                self.assertEqual(run("stop", "nosuch"),
                                 ("", "Failed to stop nosuch.service: Unit nosuch.service not loaded.\n", 5))
                self.assertEqual(run("restart", "nosuch"),
                                 ("", "Failed to restart nosuch.service: Unit nosuch.service not found.\n", 5))
                self.assertEqual(run("status", "nosuch"), ("", "Unit nosuch.service could not be found.\n", 4))
                self.assertEqual(run("is-active", "nosuch"), ("inactive\n", "", 3))
                self.assertEqual(run("show", "nosuch", "-p", "ActiveState"), ("ActiveState=inactive\n", "", 0))
                self.assertEqual(run("show", "-P", "ActiveState", "nosuch"), ("inactive\n", "", 0))

    def test_changes_need_root(self):
        run = self.session("debian-bookworm", uid=1000)
        self.assertEqual(run("enable", "x"), ("", "Failed to enable unit: Access denied\n", 1))
        self.assertEqual(run("start", "x"), ("", "Failed to start x.service: Access denied\n"
                                                 "See system logs and 'systemctl status x.service' for details.\n", 4))
        self.assertEqual(run("status", "x")[2], 3)
        run = self.session("ubuntu-noble", uid=1000)
        self.assertEqual(run("start", "x")[1:], ("Failed to start x.service: Interactive authentication required.\n"
                                                  "See system logs and 'systemctl status x.service' for details.\n", 1))


NOROOT = json.loads((Path(__file__).parent / "fixtures" / "systemd_noroot.json").read_text(encoding="utf-8"))["images"]
# polkit state per persona (servers.py:1633-1637); ps lists: centos-7 only; the others are the package/fs data or unverified
POLKIT = {"rhel-9": True, "centos-7": True, "raspios": True, "ubuntu-jammy": True, "ubuntu-noble": True,
          "gpu-ubuntu": True, "debian-bookworm": False, "debian-trixie": False}
# devices with systemd: no polkit in their trees (unverified for DSM), the old texts of their release
DEVICE_POLKIT = {"synology-dsm": 219, "ubiquiti-edgerouter-x": 232}
TPOT_PK1 = "The name org.freedesktop.PolicyKit1 was not provided by any .service files"


class RefusedTest(unittest.TestCase):
    """A session user without root: the texts and exit statuses of the release's systemd (refs systemd_noroot.json),
    without polkit or with it."""

    def run_case(self, name, entry, polkit):
        files = {"/etc/init.d/ssh": b"#!/bin/sh\n"} if entry["persona"] != "rhel-9" else {}
        run = Session(entry["persona"], uid=1000, files=files, polkit=polkit, tty=entry["tty"])
        self.assertEqual(run.namespace["tpot_units"]()["version"], entry["systemd"], name)
        return run

    @staticmethod
    def answer(run, entry, cmd):
        """The answer of a call; a recorded tty merges stderr into the first field."""
        out, err, code = run(*cmd.split()[1:])
        return (out + err, "", code) if entry["tty"] else (out, err, code)

    def test_every_release_with_and_without_polkit(self):
        checked = 0
        for name, entry in NOROOT.items():
            for cmd, (out, err, code) in entry["cases"].items():
                if cmd.startswith("service "):
                    continue
                with self.subTest(name, cmd=cmd):
                    run = self.run_case(name, entry, entry["polkit"])
                    self.assertEqual(self.answer(run, entry, cmd), (out, err, code))
                    checked += 1
        self.assertGreaterEqual(checked, 170)

    def test_a_persona_answers_with_its_own_polkit_state(self):
        for pid, polkit in POLKIT.items():
            units = Session(pid, uid=1000).namespace["tpot_units"]()
            self.assertEqual(units["polkit"], polkit, pid)
            stop = "systemctl stop " + ("sshd" if pid in ("rhel-9", "centos-7") else "ssh")
            # the recorded images of the persona's release, family and polkit state
            recorded = [e for e in NOROOT.values() if e["systemd"] == units["version"] and e["polkit"] == polkit
                        and not e["tty"] and stop in e["cases"]]
            self.assertTrue(recorded, pid)
            for entry in recorded:
                for cmd in (stop, "systemctl daemon-reload", "systemctl reboot"):
                    with self.subTest(pid, image=entry["image"], cmd=cmd):
                        run = Session(pid, uid=1000)
                        self.assertEqual(run(*cmd.split()[1:]), tuple(entry["cases"][cmd]))

    def test_devices_answer_with_the_texts_of_their_release(self):
        # 219 without polkit is recorded (centos_7_nopolkit); 232 reads as 219 (derived, not recorded)
        for pid, version in DEVICE_POLKIT.items():
            run = Session(pid, uid=1000)
            self.assertEqual(run.namespace["tpot_units"]()["version"], version)
            self.assertFalse(run.namespace["tpot_units"]()["polkit"], pid)
            unit = "sshd" if pid == "synology-dsm" else "ssh"
            self.assertEqual(run("stop", unit), ("", f"Failed to stop {unit}.service: {TPOT_PK1}\n"
                                                  f"See system logs and 'systemctl status {unit}.service' for details.\n", 1))
            self.assertEqual(run("daemon-reload"), ("", "Failed to execute operation: " + TPOT_PK1 + "\n", 1))

    def test_a_terminal_without_polkit_colours_the_errors_of_252_on(self):
        # derived from the recorded tty with polkit, not recorded without it
        text = "Failed to stop ssh.service: Access denied\nSee system logs and 'systemctl status ssh.service' for details.\n"
        coloured = "".join(f"\x1b[0;1;31m{line}\x1b[0m\n" for line in text.splitlines())
        self.assertEqual(Session("debian-bookworm", uid=1000, tty=True)("stop", "ssh"), ("", coloured, 4))
        self.assertEqual(Session("ubuntu-jammy", uid=1000, polkit=False, tty=True)("stop", "ssh"), ("", text, 4))

    def test_the_sysv_lines_of_a_terminal_stay_plain(self):
        run = Session("debian-bookworm", uid=1000, files={"/etc/init.d/ssh": b"#!/bin/sh\n"}, tty=True)
        err = run("disable", "ssh")[1]
        self.assertTrue(err.startswith("Synchronizing state of ssh.service"), err)
        self.assertTrue(err.endswith("update-rc.d: error: Permission denied\n"), err)

    def test_a_user_still_reads(self):
        run = Session("ubuntu-jammy", uid=1000)
        self.assertEqual(run("is-active", "ssh"), ("active\n", "", 0))
        self.assertEqual(run("status", "ssh", "--no-pager")[2], 0)

    def test_root_changes_stay_silent(self):
        run = Session("debian-bookworm", uid=0)
        self.assertEqual(run("daemon-reload"), ("", "", 0))
        self.assertEqual(run("restart", "ssh"), ("", "", 0))

    def test_calls_without_a_unit_name(self):
        run = Session("debian-bookworm", uid=1000)
        for verb in ("stop", "kill", "enable", "mask", "isolate"):
            with self.subTest(verb):
                self.assertEqual(run(verb), ("", "Too few arguments.\n", 1))

    def test_derived_texts(self):
        # not recorded (derived from systemctl.c): halt, reset-failed, unmask, a unit without an init script
        run = Session("debian-bookworm", uid=1000)
        self.assertEqual(run("halt"), ("", "Call to Halt failed: Access denied\n", 1))
        self.assertEqual(run("unmask", "x"), ("", "Failed to unmask unit: Access denied\n", 1))
        self.assertEqual(run("enable", "x"), ("", "Failed to enable unit: Access denied\n", 1))
        self.assertEqual(run("reset-failed", "ssh"),
                         ("", "Failed to reset failed state of unit ssh.service: Access denied\n", 1))
        self.assertEqual(run("reset-failed"), ("", "Failed to reset failed state of all units: Access denied\n", 1))
        old = Session("centos-7", uid=1000)
        self.assertEqual(old("halt")[1].splitlines()[:2],
                         ["Failed to execute operation: Interactive authentication required.",
                          "Failed to start halt.target: Interactive authentication required."])


STOPS = json.loads((Path(__file__).parent / "fixtures" / "systemd_stops.json").read_text(encoding="utf-8"))["images"]
ONESHOT = json.loads((Path(__file__).parent / "fixtures" / "systemd_oneshot.json").read_text(encoding="utf-8"))["images"]


class StopWarningTest(unittest.TestCase):
    """Stopping a service whose socket stays active: the stderr of the recorded stops (refs systemd_stops.json)."""

    @staticmethod
    def recorded(image, unit):
        for case, (_, err, _) in STOPS[image]["cases"].items():
            if f"systemctl stop {unit}" in case:
                yield err

    def test_ssh_of_noble_names_its_socket(self):
        errs = list(self.recorded("ubuntu_24.04", "ssh.service"))
        self.assertEqual(len(errs), 2)
        for err in errs:
            self.assertEqual(Session("ubuntu-noble")("stop", "ssh.service")[1:], (err, 0))
        run = Session("ubuntu-noble")
        self.assertEqual(run("stop", "ssh")[1], errs[0])
        # derived, not recorded: the warning is for a socket that is still active
        run = Session("ubuntu-noble")
        run("stop", "ssh.socket")
        self.assertEqual(run("stop", "ssh"), ("", "", 0))

    def test_a_socket_of_each_release_is_named_as_that_release_does(self):
        # the personas have no syslog.socket: it is added to their units; the versions are those of the personas
        for image, pid, version in (("ubuntu_22.04", "ubuntu-jammy", 249), ("debian_12", "debian-bookworm", 252),
                                    ("ubuntu_24.04", "ubuntu-noble", 255), ("debian_13", "debian-trixie", 257)):
            with self.subTest(image):
                run = Session(pid)
                data = copy.deepcopy(run.namespace["tpot_units"]())
                self.assertEqual(int(data["version"]), version)
                self.add_socket(data)
                run.namespace["tpot_units"] = lambda data=data: data
                (err,) = self.recorded(image, "rsyslog.service")
                self.assertEqual(run("stop", "rsyslog.service")[1:], (err, 0))

    def test_the_text_changes_with_systemd_255(self):
        # derived from the systemd sources v254 / v255, not recorded: 254 still prints the old text
        run = Session("ubuntu-jammy")
        data = copy.deepcopy(run.namespace["tpot_units"]())
        data["version"] = 254
        self.add_socket(data)
        run.namespace["tpot_units"] = lambda data=data: data
        self.assertEqual(run("stop", "rsyslog.service")[1],
                         "Warning: Stopping rsyslog.service, but it can still be activated by:\n  syslog.socket\n")

    @staticmethod
    def add_socket(data):
        if not any(unit["name"] == "rsyslog.service" for unit in data["units"]):
            # Debian has no rsyslog: a unit shaped like cron.service stands in for it
            cron = next(unit for unit in data["units"] if unit["name"] == "cron.service")
            data["units"].append(dict(copy.deepcopy(cron), name="rsyslog.service", alias=[], pid=0))
        for unit in data["units"]:
            if unit["name"] == "rsyslog.service":
                unit["triggered_by"] = "syslog.socket"
        data["units"].append({"name": "syslog.socket", "description": "Syslog Socket", "state": "enabled",
                              "sub": "running", "path": "/usr/lib/systemd/system/syslog.socket"})


class StopExitTest(unittest.TestCase):
    """A daemon after `systemctl stop` or `kill`: the status the recorded stops print (refs systemd_stops.json, every
    image a persona is built from; the units a container skips or cannot start are left out)."""

    PERSONAS = {"debian_12": "debian-bookworm", "debian_13": "debian-trixie", "ubuntu_22.04": "ubuntu-jammy",
                "ubuntu_24.04": "ubuntu-noble", "rockylinux_9": "rhel-9", "centos_7": "centos-7"}

    maxDiff = None

    @staticmethod
    def digits(text):
        text = re.sub(r"\[\d+\.\d+\]", "[EPOCH]", text)
        text = re.sub(r'swVersion="[^"]*"', 'swVersion="V"', text)
        return re.sub(r"\bPID\b|\d+", "N", text)

    @classmethod
    def shape(cls, text):
        """The unit block and the journal from the stop on (the daemon's line before it too), without the figures
        that change from run to run; a kill that logs no signal keeps the daemon's last line."""
        block, _, journal = text.partition("\n\n")
        block = re.sub(r"since [^;\n]+; [^\n]*ago", "since T; N ago", block)
        block = re.sub(r"^(\s*(?:Duration|CPU|Tasks|Memory|Mem peak|Invocation)): .*$", r"\1: N", block, flags=re.M)
        lines = [re.sub(r"^\w{3} (?:[ \d]\d|DD) (?:[\d:]{8}|HH:MM:SS) \S+ ", "", line) for line in journal.splitlines()]
        # the run before the last start is the harness's own (it stops and starts a unit the recording only kills)
        lines = lines[max((i + 1 for i, line in enumerate(lines) if "]: Started " in line), default=0):]
        start = next((i for i, line in enumerate(lines) if "Stopping" in line or "Sent signal" in line), None)
        if start is None:
            kept = lines[-1:] if lines and "Received signal 15" in lines[-1] else []
        else:
            kept = lines[start - 1 if start and re.search(r"Received signal 15|exiting", lines[start - 1]) else start:]
        return cls.digits(block), [cls.digits(line) for line in kept]

    @classmethod
    def unit_files(cls, pid):
        """The unit files of the persona that name an ExecStartPre= or ExecStop= (the harness file system has none)."""
        from tpot_personas import servers
        out, ident = rendered(pid)
        persona = catalog.by_id(pid)
        files = {}
        for unit in json.loads((out / "systemd-units.json").read_text())["units"]:
            if unit.get("path") and unit["name"].endswith(".service"):
                try:
                    text = servers.unit_file(persona, ident, unit)
                except KeyError:  # a unit of the persona the short unit files do not cover (network.service)
                    continue
                if "ExecStop=" in text or "ExecStartPre=" in text:
                    files[unit["path"]] = text.encode()
                    for program in re.findall(r"^Exec(?:Start|StartPre|Stop)=[-@:+!]*(/\S+)", text, flags=re.M):
                        files.setdefault(program, b"\x7fELF")
        return files

    @staticmethod
    def clone(data, recorded):
        """A disabled service of the recorded text (name, description, path, preset, docs) on the shape of a running
        daemon of the persona: the packages the recording installed (httpd, nginx, ...) are not on the persona."""
        lines = recorded.split("\n")
        name, _, description = lines[0][2:].partition(" - ")
        path, preset = re.search(r"\(([^;]+); disabled; (?:vendor )?preset: (\w+)\)", lines[1]).groups()
        docs = [re.sub(r"^\s*(?:Docs: )?", "", line) for line in lines[2:] if re.match(r"\s+(?:Docs: )?(?:man:|https?:)", line)]
        base = next(u for u in data["units"] if u.get("comm") in ("cron", "crond") and not u.get("triggered_by"))
        comm = "master" if name == "postfix.service" else name.split(".")[0]
        return dict(copy.deepcopy(base), name=name, description=description, path=path, state="disabled", preset=preset,
                    docs=docs, alias=[], wants=[], comm=comm)

    def session(self, pid, recorded, fresh=None):
        """`fresh`: the unit has run since a start of the session, as in the recordings where it had been stopped."""
        run = Session(pid, files=self.unit_files(pid))
        data = copy.deepcopy(run.namespace["tpot_units"]())
        name = recorded.split(" ")[1]
        if ("disabled;" in recorded.split("\n")[1] and not name.endswith(".socket")
                and all(unit["name"] != name for unit in data["units"])):
            data["units"].append(self.clone(data, recorded))
            run.namespace["tpot_units"] = lambda data=data: data
        if fresh:
            run("stop", fresh)
            run("start", fresh)
        return run

    @classmethod
    def cases(cls):
        for image, persona in cls.PERSONAS.items():
            for case, (out, err, _) in STOPS[image]["cases"].items():
                if not re.fullmatch(r"systemctl (?:start \S+; sleep 2; systemctl stop \S+; |kill \S+; sleep 1; )"
                                    r"systemctl status \S+ --no-pager -l", case):
                    continue
                active = out.split("Active:")[1].split("\n")[0]
                # a socket starts the unit again (rsyslog), a container cannot start it (condition, failed)
                if "Condition:" not in out and "failed" not in active and not active.strip().startswith("act"):
                    yield image, persona, case, out, err

    def test_the_recorded_stops_of_the_daemons_of_the_personas(self):
        seen = []
        disabled = []
        for image, persona, case, out, err in self.cases():
            unit = case.split()[2].rstrip(";")
            if not case.startswith("systemctl start ") or unit in ("kdump.service", "auditd.service", "telnet.socket") \
                    or (unit.endswith(".socket") and "disabled;" in out.split("\n")[1]):
                continue
            # Ubuntu 24.04 starts sshd from its socket: the recorded run is a fresh one
            run = self.session(persona, out, unit if (image, unit) == ("ubuntu_24.04", "ssh.service") else None)
            stopped = run("stop", unit)
            shown = run("status", unit, "--no-pager", "-l")
            if shown[2] == 4:
                continue
            with self.subTest(image=image, unit=unit):
                seen.append((image, unit))
                disabled += [unit] if "disabled;" in out.split("\n")[1] else []
                self.assertEqual(shown[2], 3)
                if not err:
                    self.assertEqual(stopped[1], "")
                self.assertEqual(self.shape(shown[0]), self.shape(out))
        self.assertGreater(len(seen), 25)
        # the disabled services of the recordings (httpd, nginx, mariadb, redis, postfix) are compared too
        self.assertGreaterEqual(len(disabled), 4)

    def test_the_recorded_kills_end_the_main_process_like_a_stop(self):
        seen = []
        for image, persona, case, out, err in self.cases():
            match = re.fullmatch(r"systemctl kill (\S+); sleep 1; .*", case)
            if not match:
                continue
            run = self.session(persona, out, match.group(1))
            with self.subTest(image=image, unit=match.group(1)):
                self.assertEqual(run("kill", match.group(1)), ("", "", 0))
                shown = run("status", match.group(1), "--no-pager", "-l")
                seen.append(image)
                self.assertEqual(shown[2], 3)
                self.assertEqual(self.shape(shown[0]), self.shape(out))
        self.assertEqual(len(seen), 12)

    def test_a_kill_with_a_signal_of_its_own_leaves_the_unit_running(self):
        # derived, not recorded: only the default signal is modelled (a kill of another signal does nothing)
        run = Session("debian-bookworm")
        self.assertEqual(run("kill", "--signal=SIGHUP", "ssh.service"), ("", "", 0))
        self.assertIn("Active: active (running)", run("status", "ssh.service", "--no-pager")[0])

    def test_the_order_of_the_sshd_line_follows_each_image(self):
        # recorded: before "Stopping" on Rocky 9 and Ubuntu 24.04, after it on Debian 12 / 13, Ubuntu 22.04, CentOS 7
        for image, persona in self.PERSONAS.items():
            case = next(case for case in STOPS[image]["cases"]
                        if re.match(r"systemctl start ssh?d?\.service; sleep 2; systemctl stop", case)
                        and STOPS[image]["cases"][case][0].count("Received signal 15"))
            out = STOPS[image]["cases"][case][0]
            lines = out.partition("\n\n")[2].splitlines()
            at = [i for i, line in enumerate(lines) if "Received signal 15" in line][0]
            before = at < [i for i, line in enumerate(lines) if "Stopping" in line][0]
            with self.subTest(image=image):
                unit = case.split()[2].rstrip(";")
                run = self.session(persona, out)
                run("stop", unit)
                mine = run("status", unit, "--no-pager", "-l")[0].partition("\n\n")[2].splitlines()
                self.assertEqual([i for i, line in enumerate(mine) if "Received signal 15" in line][0]
                                 < [i for i, line in enumerate(mine) if "Stopping" in line][0], before)

    def test_a_unit_that_is_disabled_and_has_no_trigger_is_dropped_after_its_stop(self):
        # systemd forgets what ran of it: Active has no time, no Duration, Process, Main PID or CPU
        for image, persona, case, out, err in self.cases():
            if case.split()[2].rstrip(";") != "telnet.socket":
                continue
            with self.subTest(image=image):
                run = Session(persona)
                data = copy.deepcopy(run.namespace["tpot_units"]())
                for entry in data["units"]:
                    if entry["name"] == "telnet.socket":
                        entry["state"] = "disabled"
                run.namespace["tpot_units"] = lambda data=data: data
                run("stop", "telnet.socket")
                self.assertEqual(self.shape(run("status", "telnet.socket", "--no-pager", "-l")[0]), self.shape(out))

    def test_snapd_ends_with_status_42(self):
        # recorded on jammy and noble; the daemons that end on TERM with 0 are not touched
        for image in ("ubuntu_22.04", "ubuntu_24.04"):
            with self.subTest(image=image):
                run = Session(self.PERSONAS[image])
                run("stop", "snapd.service")
                self.assertEqual(run("status", "snapd.service", "--no-pager", "-l")[0].count("status=42)"), 2)

    def test_auditd_refuses_a_manual_stop_and_restart(self):
        for image in ("rockylinux_9", "centos_7"):
            cases = ONESHOT[image]["cases"]
            restart = cases["systemctl restart auditd; echo rc=$?"]
            reload_ = cases["systemctl reload auditd; echo rc=$?"]
            refusal = restart[1].split("Failed to restart", 1)[1]
            with self.subTest(image=image):
                run = Session(self.PERSONAS[image])
                self.assertEqual(run("restart", "auditd.service")[1:],
                                 ("Failed to restart" + refusal, int(restart[0].split("rc=")[1])))
                # derived, not recorded: stop and the other verbs that stop it say the same, with the status of restart
                for verb in ("stop", "try-restart", "reload-or-restart"):
                    self.assertEqual(run(verb, "auditd.service")[1:],
                                     (f"Failed to {verb}" + refusal, int(restart[0].split("rc=")[1])), verb)
                self.assertEqual(run("reload", "auditd.service")[1:],
                                 (reload_[1].split("\n", 1)[1] if "setlocale" in reload_[1] else reload_[1],
                                  int(reload_[0].split("rc=")[1])))
                self.assertIn("Active: active (running)", run("status", "auditd.service", "--no-pager")[0])

    def test_auditd_of_another_family_stops_as_any_daemon(self):
        # derived, not recorded: only the units of Red Hat's family refuse
        run = Session("rhel-9")
        data = copy.deepcopy(run.namespace["tpot_units"]())
        data["family"] = "debian"
        run.namespace["tpot_units"] = lambda data=data: data
        self.assertEqual(run("stop", "auditd.service"), ("", "", 0))

    def test_a_oneshot_unit_keeps_its_main_process_after_its_stop(self):
        # recorded (systemd_oneshot.json): status shows the Main PID of an exited oneshot before and after its stop
        line = "Main PID: PID (code=exited, status=0/SUCCESS)"
        for image in ("debian_12", "rockylinux_9", "centos_7"):
            for case, (out, _, _) in ONESHOT[image]["cases"].items():
                if case.startswith(("status ", "stop ")) and "Condition:" not in out and "Main PID" in out:
                    self.assertIn(line, [row.strip() for row in out.splitlines()], (image, case))
        files = {"/etc/systemd/system/o.service": b"[Unit]\nDescription=o test service\n\n[Service]\nType=oneshot\n"
                                                  b"RemainAfterExit=yes\nExecStart=/bin/sleep 1\n"}
        for persona, unit, extra in (("rhel-9", "kdump.service", {}), ("centos-7", "kdump.service", {}),
                                     ("debian-bookworm", "o.service", files), ("rhel-9", "o.service", files)):
            with self.subTest(persona=persona, unit=unit):
                run = Session(persona, files=extra)
                run("start", unit)
                before = [row for row in run("status", unit, "--no-pager")[0].splitlines() if "Main PID" in row]
                run("stop", unit)
                after = [row for row in run("status", unit, "--no-pager")[0].splitlines() if "Main PID" in row]
                self.assertEqual([self.digits(row) for row in after], [self.digits(row) for row in before])
                self.assertEqual(len(after), 1)
                self.assertIn("(code=exited, status=0/SUCCESS)", after[0])

    def test_fd_store_is_shown_while_a_unit_runs_too(self):
        # derived from the order of the stopped unit's lines (Main PID, FD Store), not recorded
        run = Session("ubuntu-noble")
        out = run("status", "snapd.service", "--no-pager")[0]
        self.assertRegex(out, r"Main PID: \d+ \(snapd\)\n\s+FD Store: 0 \(limit: 1024\)\n\s+Tasks: ")

    def test_rsyslog_of_centos_is_the_version_of_its_package(self):
        run = Session("centos-7")
        unit = next(u for u in run.namespace["tpot_units"]()["units"] if u["name"] == "rsyslog.service")
        from tpot_personas import system
        self.assertEqual(unit["sw_version"], system.centos_installed(run.ident)["rsyslog"].split(":")[-1])


class PersonaUnitTest(unittest.TestCase):
    def test_show_of_the_ssh_unit(self):
        # refs *-show-ssh-id: the service's own properties first, then those of every unit
        run = Session("ubuntu-jammy")
        pid = next(p["PID"] for p in run.processes if p["COMMAND"].startswith("sshd:"))
        args = ("show", "ssh", "-p", "Id", "-p", "LoadState", "-p", "ActiveState", "-p", "SubState", "-p",
                "UnitFileState", "-p", "FragmentPath", "-p", "MainPID")
        self.assertEqual(run(*args), (f"MainPID={pid}\nId=ssh.service\nLoadState=loaded\nActiveState=active\n"
                                      "SubState=running\nFragmentPath=/lib/systemd/system/ssh.service\n"
                                      "UnitFileState=enabled\n", "", 0))
        self.assertEqual(run("show", "sshd", "-p", "Id"), ("Id=ssh.service\n", "", 0))
        self.assertEqual(run("show", "--value", "-p", "MainPID", "ssh"), (f"{pid}\n", "", 0))
        self.assertEqual(run("show", "-P", "ActiveState", "ssh"), ("active\n", "", 0))
        run = Session("rhel-9")
        self.assertEqual(run("show", "ssh", "-p", "Id", "-p", "LoadState", "-p", "FragmentPath", "-p", "MainPID"),
                         ("MainPID=0\nId=ssh.service\nLoadState=not-found\nFragmentPath=\n", "", 0))

    def test_getty_and_the_host_units(self):
        # getty@tty1 runs as on a host (Type=idle: systemd logs Started only); Docs: of the unit file
        run = Session("rhel-9")
        out, _, code = run("status", "getty@tty1")
        self.assertEqual(code, 0)
        self.assertIn("\n       Docs: man:agetty(8)\n             man:systemd-getty-generator(8)\n", out)
        self.assertNotIn("Starting getty", out)
        # (Red Hat's 252 names the description alone)
        self.assertTrue(out.endswith(" systemd[1]: Started Getty on tty1.\n"), out)
        self.assertIn("\ngetty@.service ", run("list-unit-files", "--no-legend")[0])
        for pid, unit in (("rhel-9", "tuned"), ("rhel-9", "rhsmcertd"), ("debian-bookworm", "systemd-timesyncd")):
            self.assertEqual(Session(pid)("is-active", unit), ("active\n", "", 0), (pid, unit))
        out = Session("debian-bookworm")("status", "user.slice")[0]
        self.assertTrue(out.startswith("\u25cf user.slice - User and Session Slice\n     Loaded: loaded "
                                       "(/lib/systemd/system/user.slice; static)\n     Active: active since "), out)
        self.assertIn("\n       Docs: man:systemd.special(7)\n", out)
        self.assertEqual(Session("debian-bookworm")("status", "ssh", "-n", "007")[2], 0)

    def test_is_enabled_of_an_alias(self):
        # ref ubuntu-22-04-is-enabled-sshd
        self.assertEqual(Session("ubuntu-jammy")("is-enabled", "sshd"), ("alias\n", "", 0))

    def test_failed_and_is_system_running(self):
        # refs ubuntu-24-04-failed, almalinux-9-failed (a host whose units all run)
        self.assertEqual(Session("ubuntu-noble")("--failed", "--no-pager"),
                         ("  UNIT LOAD ACTIVE SUB DESCRIPTION\n\n0 loaded units listed.\n", "", 0))
        self.assertEqual(Session("rhel-9")("--failed"), ("  UNIT LOAD ACTIVE SUB DESCRIPTION\n0 loaded units listed.\n",
                                                         "", 0))
        self.assertEqual(Session("ubuntu-noble")("is-system-running"), ("running\n", "", 0))

    def test_legend_of_the_versions(self):
        # refs *-list-units-service: the old legend up to 252 (also Red Hat's), the arrows from 255 on
        out = Session("debian-bookworm")("list-units", "--type=service")[0]
        self.assertIn("\n\nLOAD   = Reflects whether the unit definition was properly loaded.\n"
                      "ACTIVE = The high-level unit activation state, i.e. generalization of SUB.\n"
                      "SUB    = The low-level unit activation state, values depend on unit type.\n"
                      "%d loaded units listed. Pass --all" % out.count(".service "), out)
        out = Session("ubuntu-noble")("list-units", "--type=service")[0]
        self.assertIn("        SUB    \u2192 The low-level unit activation state, values depend on unit type.\n\n"
                      "%d loaded units listed. Pass --all" % out.count(".service "), out)

    def test_stop_and_start_a_daemon_of_the_persona(self):
        run = Session("debian-bookworm")
        old = next(p["PID"] for p in run.processes if p["COMMAND"].startswith("/usr/sbin/cron"))
        self.assertEqual(run("stop", "cron"), ("", "", 0))
        self.assertNotIn(old, [p["PID"] for p in run.processes])
        out, _, code = run("status", "cron")
        self.assertEqual(code, 3)
        # Debian's cron has no handler of SIGTERM (ref final-wave debian-12-ffw-stop-status-cron)
        self.assertIn(f"   Main PID: {old} (code=killed, signal=TERM)\n", out)
        self.assertTrue(out.endswith("systemd[1]: Stopped cron.service - Regular background program processing "
                                     "daemon.\n"), out)
        run("start", "cron")
        row = next(p for p in run.processes if p["COMMAND"].startswith("/usr/sbin/cron"))
        self.assertGreater(row["PID"], old)
        self.assertIn(f"   Main PID: {row['PID']} (cron)\n", run("status", "cron")[0])
        self.assertEqual(run.namespace["tpot_sd_state"].__call__ and run.user.server.tpot_systemd["pids"][old],
                         row["PID"])


class JournalTest(unittest.TestCase):
    def journal(self, pid, *args):
        run = Session(pid)
        cmd = FakeCommand(args)
        cmd.fs = run.fs
        cmd.user = {"uid": 0, "gid": 0, "username": "root"}
        cmd.protocol.boot_time = lambda: run.boot
        cmd.protocol.hostname = run.ident.hostname
        cmd.protocol.user = run.user
        code = run.namespace["tpot_journalctl"](cmd, list(args))
        return bytes(cmd.out).decode(), cmd.err, code

    def test_journal_of_a_unit(self):
        # refs *-journalctl-n0005-ssh, *-journalctl-nosuch
        out, err, code = self.journal("debian-bookworm", "-n", "0005", "-u", "ssh")
        lines = out.splitlines()
        self.assertEqual((err, code, len(lines)), ("", 0, 4))
        self.assertRegex(lines[0], r"^\w{3} \d\d \d\d:\d\d:\d\d \S+ systemd\[1\]: Starting ssh\.service - OpenBSD Secure "
                                   r"Shell server\.\.\.$")
        self.assertRegex(lines[1], r" sshd\[\d+\]: Server listening on 0\.0\.0\.0 port 22\.$")
        self.assertTrue(lines[3].endswith(" systemd[1]: Started ssh.service - OpenBSD Secure Shell server."))
        self.assertEqual(self.journal("debian-bookworm", "-n", "5", "-u", "nosuch"), ("-- No entries --\n", "", 0))
        self.assertEqual(len(self.journal("debian-bookworm", "-u", "ssh", "-n2", "--no-pager")[0].splitlines()), 2)
        self.assertEqual(self.journal("debian-bookworm", "-n", "x")[1:], ("Failed to parse lines 'x'\n", 1))
        out = self.journal("centos-7", "-u", "sshd")[0]
        self.assertRegex(out, r"^-- Logs begin at \w{3} .+ UTC, end at \w{3} .+ UTC\. --\n")


class FixRoundTest(unittest.TestCase):
    def journal(self, pid, *args, run=None):
        return JournalTest.journal(self, pid, *args) if run is None else None

    def test_journal_of_an_alias_is_empty(self):
        # refs ubuntu-22-04-journalctl-n5-sshd, debian-12-journalctl-n0005-sshd: the journal knows ssh.service only
        for pid in ("ubuntu-jammy", "debian-bookworm", "debian-trixie", "raspios"):
            with self.subTest(pid):
                self.assertEqual(JournalTest.journal(self, pid, "-n", "5", "-u", "sshd"), ("-- No entries --\n", "", 0))
                self.assertEqual(JournalTest.journal(self, pid, "-u", "openbsd-inetd"), ("-- No entries --\n", "", 0))
                self.assertIn("Started", JournalTest.journal(self, pid, "-u", "ssh")[0])

    def test_journal_takes_clustered_flags(self):
        for args in (["-xe"], ["-xeu", "ssh"], ["-fu", "ssh"], ["-eu", "ssh"], ["-xen", "3"], ["-n5"]):
            with self.subTest(args):
                out, err, code = JournalTest.journal(self, "debian-bookworm", *args)
                self.assertEqual((err, code), ("", 0))
                self.assertTrue(out)
        self.assertIn("ssh.service", JournalTest.journal(self, "debian-bookworm", "-xeu", "ssh")[0])
        self.assertEqual(len(JournalTest.journal(self, "debian-bookworm", "-xen", "3")[0].splitlines()), 3)
        self.assertEqual(JournalTest.journal(self, "debian-bookworm", "-xu")[1:],
                         ("journalctl: option requires an argument -- 'u'\n", 1))

    def test_red_hat_names_the_description_in_its_journal(self):
        # refs almalinux-9-status-sshd, -status-socket: Red Hat's 252 logs the description only
        run = Session("rhel-9")
        out = run("status", "sshd")[0]
        self.assertIn(" systemd[1]: Starting OpenSSH server daemon...\n", out)
        self.assertTrue(out.endswith(" systemd[1]: Started OpenSSH server daemon.\n"), out)
        self.assertTrue(run("status", "telnet.socket")[0].endswith(
            " systemd[1]: Listening on Telnet Server Activation Socket.\n"))
        self.assertIn("systemd[1]: Started OpenSSH server daemon.", JournalTest.journal(self, "rhel-9", "-u", "sshd")[0])

    def test_is_enabled_of_a_missing_unit_from_255(self):
        # refs ubuntu-24-04-is-enabled-nosuch, debian-13-is-enabled-nosuch
        for pid in ("ubuntu-noble", "debian-trixie"):
            self.assertEqual(Session(pid)("is-enabled", "nosuch"), ("not-found\n", "", 4))
        self.assertEqual(Session("ubuntu-noble")("is-enabled", "sshd"), ("not-found\n", "", 4))
        self.assertEqual(Session("debian-bookworm")("is-enabled", "nosuch"),
                         ("", "Failed to get unit file state for nosuch.service: No such file or directory\n", 1))

    def test_process_titles_are_quoted_from_249(self):
        # ref ubuntu-22-04-status-sshd
        out = Session("ubuntu-jammy")("status", "ssh")[0]
        self.assertRegex(out, r'\u2514\u2500\d+ "sshd: /usr/sbin/sshd -D \[listener\] 0 of 10-100 startups"\n')

    def test_slices_and_targets(self):
        for pid in ("ubuntu-noble", "debian-trixie", "rhel-9"):
            out = Session(pid)("status", "user.slice")[0]
            self.assertRegex(out, r"\n     Memory: [\d.]+[KMG]? \(peak: [\d.]+[KMG]\)\n", pid)
        run = Session("debian-bookworm")
        out, _, code = run("status", "system.slice")
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("\u25cf system.slice - System Slice\n"), out)
        out, _, code = run("status", "multi-user.target")
        self.assertEqual(code, 0)
        self.assertIn("     Active: active since ", out)
        self.assertTrue(out.startswith("\u25cf multi-user.target - Multi-User System\n"), out)
        self.assertEqual(run("is-active", "sockets.target", "network-online.target", "getty.target"),
                         ("active\nactive\nactive\n", "", 0))

    def test_template_of_telnet_is_a_unit_file(self):
        run = Session("rhel-9")
        self.assertIn("\ntelnet@.service ", run("list-unit-files", "--no-legend")[0])
        self.assertNotIn("telnet@", run("list-units", "--all", "--no-legend")[0])

    def test_stopping_a_socket_unit_closes_its_listener(self):
        from tests.test_telnet_network import TelnetNetworkTest
        from tpot_personas import network
        run = Session("rhel-9")
        data = json.loads((run.out / "network.json").read_text())
        self.assertEqual(run("stop", "telnet.socket"), ("", "", 0))
        session = type("P", (), {})()
        session.user = run.user
        test = TelnetNetworkTest("run_cmd")
        out = test.run_cmd(data, "ss", ["-tlnp"], protocol=session)[0]
        self.assertNotIn(":23 ", out)
        run("start", "telnet.socket")
        self.assertIn(":23 ", test.run_cmd(data, "ss", ["-tlnp"], protocol=session)[0])
        run = Session("ubuntu-noble")
        data = json.loads((run.out / "network.json").read_text())
        session.user = run.user
        run("stop", "ssh.socket")
        out = test.run_cmd(data, "ss", ["-tlnp"], protocol=session)[0]
        self.assertNotIn('("systemd",pid=1', out)


class JournalReaderTest(unittest.TestCase):
    def journal_as(self, user, uid, groups):
        run = Session("ubuntu-noble", files={"/etc/group": groups})
        cmd = FakeCommand(["-n", "3"])
        cmd.fs = run.fs
        cmd.user = {"uid": uid, "gid": uid, "username": user}
        cmd.protocol.boot_time = lambda: run.boot
        cmd.protocol.hostname = run.ident.hostname
        cmd.protocol.user = run.user
        code = run.namespace["tpot_journalctl"](cmd, ["-n", "3"])
        return bytes(cmd.out).decode(), cmd.err, code

    def test_journal_of_a_user_outside_its_groups(self):
        groups = b"root:x:0:\nadm:x:4:syslog,ubuntu\nsudo:x:27:ubuntu\neve:x:1001:\n"
        out, err, code = self.journal_as("eve", 1001, groups)
        # refs track-b j2-*: the hint, then no journal file (rc 1)
        self.assertEqual((out, code), ("", 1))
        self.assertEqual(err, "Hint: You are currently not seeing messages from other users and the system.\n"
                              "      Users in groups 'adm', 'systemd-journal' can see all messages.\n"
                              "      Pass -q to turn off this notice.\n"
                              "No journal files were opened due to insufficient permissions.\n")
        out, err, code = self.journal_as("ubuntu", 1000, groups)
        self.assertEqual((err, code, len(out.splitlines())), ("", 0, 3))


JOURNAL = json.loads((Path(__file__).parent / "fixtures" / "systemd_journal.json").read_text(encoding="utf-8"))["images"]


class JournalOptionsTest(unittest.TestCase):
    """journalctl -b, --list-boots, --disk-usage, --vacuum-*, --rotate and the hint of a user outside the groups
    that read the journal: the recorded texts of every image a persona is built from (refs systemd_journal.json)."""

    maxDiff = None
    PERSONAS = StopExitTest.PERSONAS

    @staticmethod
    def shape(text):
        text = re.sub(r"[0-9a-f]{32}", "HEX32", text)
        text = re.sub(r"\w{3} \d{4}-\d\d-\d\d \d\d:\d\d:\d\d UTC", "Day YYYY-MM-DD HH:MM:SS UTC", text)
        return re.sub(r"\b\d+(?:\.\d+)?([BKMGT])\b", r"N\1", text)

    def run_as(self, persona, user, *args):
        files = {"/etc/group": b"root:x:0:\nrec:x:1000:\n"} if user else None
        run = Session(persona, files=files)
        cmd = FakeCommand(list(args))
        cmd.fs = run.fs
        cmd.user = {"uid": 1000, "gid": 1000, "username": "rec"} if user else {"uid": 0, "gid": 0, "username": "root"}
        cmd.protocol.boot_time = lambda: run.boot
        cmd.protocol.hostname = run.ident.hostname
        cmd.protocol.user = run.user
        code = run.namespace["tpot_journalctl"](cmd, list(args))
        return bytes(cmd.out).decode(), cmd.err, code

    def test_the_recorded_options(self):
        checked = 0
        for image, persona in self.PERSONAS.items():
            for user in (False, True):
                for key, (out, err, code) in JOURNAL[image + ("_user" if user else "")]["cases"].items():
                    redirected = key.endswith(">/dev/null")
                    args = key.removeprefix("journalctl ").removesuffix(" >/dev/null").split()
                    if args[:1] == ["-n"] and not user:
                        continue  # the lines of the persona's own journal
                    with self.subTest(image=image, user=user, key=key):
                        shown = self.run_as(persona, user, *args)
                        checked += 1
                        if err.startswith("Vacuuming"):
                            # the container's readdir gave the order of the directories, and left a deleted file
                            # behind on one image
                            self.assertEqual((self.vacuumed(shown[1]), shown[2]), (self.vacuumed(err), code))
                            continue
                        self.assertEqual((self.shape(shown[1]), shown[2]), (self.shape(err), code))
                        if redirected:
                            continue
                        if args == ["-b1"] and out:
                            self.assertTrue(shown[0])  # the journal itself, cut to its first line in the fixture
                        else:
                            self.assertEqual(self.shape(shown[0]), self.shape(out))
        self.assertGreaterEqual(checked, 150)

    def vacuumed(self, text):
        rows = (re.sub(r"freed N[BKMGT]", "freed N", row) for row in self.shape(text).splitlines())
        return sorted(row for row in rows if row.startswith("Vacuuming"))

    def test_the_vacuum_options_of_old_and_new_journalctl(self):
        # a value that does not parse (derived from journalctl.c, not recorded)
        self.assertEqual(self.run_as("debian-bookworm", False, "--vacuum-time=x")[1:], ("Failed to parse vacuum time: x\n", 1))
        self.assertEqual(self.run_as("debian-bookworm", False, "--vacuum-size=x")[1:], ("Failed to parse vacuum size: x\n", 1))
        self.assertEqual(self.run_as("debian-bookworm", False, "--vacuum-files=x")[1:], ("Failed to parse vacuum files: x\n", 1))

    def test_a_vacuum_time_is_read_in_linear_time(self):
        for value in ("1" * 5000 + "x", "1" * 40 + "x", "1 " * 3000 + "x", "1." * 2000 + "x", "1" + " " * 5000 + "x",
                      "1s" * 2000 + "x"):
            started = time.monotonic()
            shown = self.run_as("debian-bookworm", False, f"--vacuum-time={value}")[1:]
            self.assertEqual(shown, (f"Failed to parse vacuum time: {value}\n", 1))
            self.assertLess(time.monotonic() - started, 0.1, value[:12])
        for value in ("1d 2h", "1.5weeks", "30", "2 months", "1h30min", " 5s "):
            self.assertEqual(self.run_as("debian-bookworm", False, f"--vacuum-time={value}")[2], 0, value)

    def test_long_option_lists_are_read_in_linear_time(self):
        for args in (["-" + "x" * 1000000], ["-q"] * 300000, ["-" + "q" * 1000000 + "u", "ssh"]):
            started = time.monotonic()
            self.assertEqual(self.run_as("debian-bookworm", False, *args)[2], 0)
            self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(self.run_as("debian-bookworm", False, "-xqu", "ssh", "--vacuum-time", "1d")[2], 0)

    def test_a_boot_given_apart_from_the_option(self):
        # systemd takes the offset after -b as its argument
        self.assertEqual(self.run_as("debian-bookworm", False, "-b", "-1")[1:],
                         ("Data from the specified boot (-1) is not available: No such boot ID in journal\n", 1))
        self.assertEqual(self.run_as("debian-bookworm", False, "--boot=-1")[2], 1)
        self.assertEqual(self.run_as("debian-bookworm", False, "-b", "0", "-n", "1")[2], 0)
        self.assertEqual(self.run_as("rhel-9", False, "-b", "1")[1:],
                         ("Specifying boot ID or boot offset has no effect, no persistent journal was found.\n", 0))


class DeviceTest(unittest.TestCase):
    def test_systemctl_of_edgeos(self):
        run = Session("ubiquiti-edgerouter-x")
        out, err, code = run("--version")
        self.assertEqual((err, code), ("", 0))
        self.assertTrue(out.startswith("systemd 232\n+PAM +AUDIT "), out)
        out, _, code = run("status", "ssh")
        self.assertEqual(code, 0)
        self.assertIn("   Loaded: loaded (/lib/systemd/system/ssh.service; enabled; vendor preset: enabled)\n", out)
        sshd = next(p["PID"] for p in run.processes if p["COMMAND"] == "/usr/sbin/sshd -D")
        self.assertIn(f" Main PID: {sshd} (sshd)\n", out)
        self.assertEqual(run("is-active", "gui_telnet"), ("active\n", "", 0))
        self.assertIn("vyatta-router.service", run("list-units", "--no-legend")[0])
        # the image's unit file (403 bytes)
        node = __import__("tpot_personas.tree", fromlist=["tree"])
        fs = node.load_pickle(run.out / "fs.pickle")
        self.assertEqual(len(node.node_get(node.find_node(fs, "usr/lib/systemd/system/ssh.service"),
                                           node.A_CONTENTS)), 403)

    def test_sudo_of_edgeos(self):
        from tpot_personas import system
        rules = system.sudo_list(catalog.by_id("ubiquiti-edgerouter-x"))
        self.assertEqual(rules["defaults"], ["env_reset", "timestamp_timeout=0", "syslog_goodpri=info",
                                             "env_keep+=VYATTA_*", "env_keep+=UBNT_*",
                                             "secure_path=/usr/sbin\\:/usr/bin\\:/sbin\\:/bin"])
        self.assertEqual(rules["rules"]["ubnt"], ["(root) NOPASSWD: ALL", "(root) NOPASSWD: /opt/vyatta/bin/sudo-users/"])
        persona = catalog.by_id("ubiquiti-edgerouter-x")
        self.assertEqual(system.python_answers(persona), ("sudo", "systemctl"))
        self.assertIn("bin/systemctl", system.programs(persona, rendered("ubiquiti-edgerouter-x")[1]))

    def test_systemctl_of_dsm(self):
        run = Session("synology-dsm")
        self.assertTrue(run("--version")[0].startswith("systemd 219\n"))
        out, _, code = run("status", "sshd")
        self.assertEqual(code, 0)
        self.assertIn("   Active: active (running) since ", out)


if __name__ == "__main__":
    unittest.main()


class FinalWaveTest(unittest.TestCase):
    """Final wave: the whole journal names each line once, system.slice has the journal of the session's services,
    a unit's runs are those of its last start, and ps reads START against the day its list was made."""

    def journal(self, run, *args):
        cmd = FakeCommand(args)
        cmd.fs = run.fs
        cmd.user = {"uid": 0, "gid": 0, "username": "root"}
        cmd.protocol.boot_time = lambda: run.boot
        cmd.protocol.hostname = run.ident.hostname
        cmd.protocol.user = run.user
        code = run.namespace["tpot_journalctl"](cmd, list(args))
        return bytes(cmd.out).decode(), cmd.err, code

    def test_the_whole_journal_names_each_line_once(self):
        for pid in ("debian-bookworm", "ubuntu-noble", "rhel-9"):
            with self.subTest(pid):
                run = Session(pid)
                out, err, code = self.journal(run)
                lines = out.splitlines()
                self.assertEqual((err, code), ("", 0))
                started = [line for line in lines if " systemd[1]: Started " in line]
                self.assertTrue(started)
                self.assertEqual(len(started), len(set(started)), started)
                self.assertEqual(len(lines), len(set(lines)))

    def test_system_slice_has_the_journal_of_the_sessions_services(self):
        run = Session("debian-bookworm", files={"/etc/systemd/system/x.service": X_UNIT})
        self.assertEqual(run("start", "x"), ("", "", 0))
        out = run("status", "system.slice")[0]
        self.assertIn("systemd[1]: Started x.service - x test service.", out)

    def test_a_oneshot_unit_names_the_stop_of_its_last_run(self):
        unit = (b"[Unit]\nDescription=o\n\n[Service]\nType=oneshot\nRemainAfterExit=yes\nExecStart=/bin/sleep 1\n"
                b"ExecStop=/bin/sleep 1\n")
        run = Session("debian-bookworm", files={"/etc/systemd/system/o.service": unit})
        self.assertEqual(run("start", "o")[2], 0)
        for _ in range(4):
            self.assertEqual(run("restart", "o")[2], 0)
        self.assertEqual(run("stop", "o")[2], 0)
        out = run("status", "o")[0]
        self.assertEqual(out.count("ExecStop="), 1, out)

    def test_ps_reads_start_against_the_day_of_its_list(self):
        import calendar
        from tests.test_patch import FakeConfig, module_parts
        root = Path(tempfile.mkdtemp(prefix="tpot-ps-")) / "cowrie"
        self.addCleanup(shutil.rmtree, root.parent, True)
        shutil.copytree(FIXTURE, root)
        subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(root)], check=True, capture_output=True)
        source = (root / "src/cowrie/commands/base.py").read_text()
        months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
        for rendered, now, start, earliest in (
                # a list made at 23:50, a process of 23:40, ps ten minutes after midnight
                ("2026-10-19 23:50", "2026-10-20 00:10", "23:40", "2026-10-19 23:40"),
                # made on New Year's Eve, a process of Dec30, ps on New Year's Day
                ("2026-12-31 22:00", "2027-01-01 01:00", "Dec30", "2026-12-30 00:00")):
            with self.subTest(start=start):
                def epoch(text):
                    return calendar.timegm(time.strptime(text, "%Y-%m-%d %H:%M"))

                boot = epoch(rendered) - 10 * 86400
                names = module_parts(source, ["tpot_ps_stamp", "tpot_ps_rendered", "tpot_ps_start"], {
                    "CowrieConfig": FakeConfig({("honeypot", "boot_offset"): str(epoch(rendered) - boot)}),
                    "TPOT_PS_MONTHS": months})
                stamp = names["tpot_ps_stamp"](start, boot, epoch(now), "host", 4242)
                self.assertGreaterEqual(stamp, epoch(earliest))
                self.assertLess(stamp, epoch(earliest) + 86400)
                self.assertLess(stamp, epoch(now) - 60)


SYSTEMD_219 = json.loads((Path(__file__).parent / "fixtures" / "systemd_219.json").read_text(encoding="utf-8"))[
    "images"]["centos_7"]["cases"]


class Systemd219Test(unittest.TestCase):
    """The texts of systemd 219 (CentOS 7) as recorded as root (refs systemd_219.json centos_7; the recording
    has no polkit, the persona keeps its own polkit texts for uid 1000)."""

    maxDiff = None

    @staticmethod
    def shape(text, host="HOST"):
        text = text.replace(host, "HOST")
        text = re.sub(r"(?i:since):? [^;\n]+; [^\n]*ago", "since T; N ago", text)
        text = re.sub(r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun|Day) [\w-]+ [\w:]+ UTC", "T", text)
        text = re.sub(r"^\w{3} (?:\d\d|DD) (?:[\d:]{8}|HH:MM:SS) \S+ ", "", text, flags=re.M)
        return re.sub(r"\bPID\b|\d+", "N", text)

    def test_the_answers_for_a_unit_that_is_not_there(self):
        run = Session("centos-7")
        for key, args in (("systemctl is-active sshd crond nosuch", ("is-active", "sshd", "crond", "nosuch")),
                          ("systemctl is-enabled sshd nosuch", ("is-enabled", "sshd", "nosuch")),
                          ("systemctl stop nosuch", ("stop", "nosuch")),
                          ("systemctl start nosuch", ("start", "nosuch")),
                          ("systemctl enable nosuch", ("enable", "nosuch"))):
            with self.subTest(key=key):
                self.assertEqual(run(*args), tuple(SYSTEMD_219[key]))

    def test_other_versions_keep_their_texts(self):
        # recorded for 219 only; the others are the recordings of the noroot wave (debian_12, rockylinux_9)
        for pid in ("debian-bookworm", "rhel-9"):
            run = Session(pid)
            self.assertEqual(run("is-active", "nosuch"), ("inactive\n", "", 3))
            self.assertEqual(run("start", "nosuch")[1], "Failed to start nosuch.service: Unit nosuch.service not found.\n")
            self.assertEqual(run("enable", "nosuch")[1], "Failed to enable unit: Unit file nosuch.service does not exist.\n")

    def test_the_status_of_a_service_and_the_overview(self):
        run = Session("centos-7")
        shown = run("status", "sshd")
        self.assertEqual(self.shape(shown[0]), self.shape(SYSTEMD_219["systemctl status sshd"][0]))
        self.assertEqual(self.shape(run("status", "sshd")[0]), self.shape(SYSTEMD_219["service sshd status"][0]))
        # the overview: the first lines (the state and the control group tree are those of the container)
        head = run("status", "--no-pager")[0].splitlines()[:6]
        want = SYSTEMD_219["systemctl status | head -12"][0].splitlines()[:6]
        for ours, theirs in zip(head, want):
            if "State:" not in ours and "Failed:" not in ours:
                self.assertEqual(self.shape(ours, run.ident.hostname), self.shape(theirs))

    def test_the_header_of_the_journal_of_a_unit(self):
        run = Session("centos-7")
        cmd = FakeCommand(["-u", "sshd", "-n", "2", "--no-pager"])
        cmd.fs = run.fs
        cmd.user = {"uid": 0, "gid": 0, "username": "root"}
        cmd.protocol.boot_time = lambda: run.boot
        cmd.protocol.hostname = run.ident.hostname
        cmd.protocol.user = run.user
        self.assertEqual(run.namespace["tpot_journalctl"](cmd, ["-u", "sshd", "-n", "2", "--no-pager"]), 0)
        self.assertEqual(self.shape(bytes(cmd.out).decode(), run.ident.hostname),
                         self.shape(SYSTEMD_219["journalctl -u sshd -n 2 --no-pager"][0]))

    def test_stop_and_kill_of_sshd_and_the_lists(self):
        for key, verb in (("systemctl stop sshd; systemctl status sshd", "stop"),
                          ("systemctl kill sshd; sleep 1; systemctl status sshd", "kill")):
            with self.subTest(key=key):
                run = Session("centos-7")
                self.assertEqual(run(verb, "sshd"), ("", "", 0))

                def lines(text):
                    # the CGroup line of the recording is that of its container's cgroup layout
                    return [row for row in self.shape(text).splitlines() if "CGroup" not in row]

                self.assertEqual(lines(run("status", "sshd")[0]), lines(SYSTEMD_219[key][0]))
        run = Session("centos-7")
        for key, verb in (("systemctl list-units --no-pager | head -5", "list-units"),
                          ("systemctl list-unit-files --no-pager | head -5", "list-unit-files")):
            with self.subTest(key=key):
                self.assertEqual(run(verb, "--no-pager")[0].splitlines()[0].split(),
                                 SYSTEMD_219[key][0].splitlines()[0].split())

    def test_service_of_a_unit_that_is_not_there(self):
        run = Session("centos-7")
        service = run.namespace["Command_service"](["nosuch", "stop"])
        service.fs, service.user, service.exit_code = run.fs, {"uid": 0, "gid": 0, "username": "root"}, 0
        service.protocol.boot_time = lambda: run.boot
        service.protocol.hostname = run.ident.hostname
        service.protocol.user = run.user
        service.protocol.cmdstack = []
        service.call()
        out, err, code = SYSTEMD_219["service nosuch stop"]
        self.assertEqual((bytes(service.out).decode(), service.err, service.exit_code), (out, err, code))


CHKCONFIG = json.loads((Path(__file__).parent / "fixtures" / "chkconfig_219.json").read_text(encoding="utf-8"))["cases"]
NOTE = CHKCONFIG["chkconfig --list"][1]
# the two scripts of the initscripts package (derived from chkconfig.c: "%-15s" and "\t%d:on|off" per level, not recorded:
#   the recorded image has no initscripts); the levels are those of the rc links of the persona (netconsole K50, network S10)
NETCONSOLE = "netconsole" + " " * 5 + "".join(f"\t{level}:off" for level in range(7)) + "\n"
NETWORK = "network" + " " * 8 + "\t0:off\t1:off\t2:on\t3:on\t4:on\t5:on\t6:off\n"


class ChkconfigTest(unittest.TestCase):
    """chkconfig of the CentOS 7 persona (refs chkconfig_219.json: centos:7 with chkconfig 1.7.6, run in Docker)."""

    @staticmethod
    def chkconfig(*args, uid=0, pid="centos-7"):
        run = Session(pid, uid=uid)
        cmd = FakeCommand(args)
        cmd.fs = run.fs
        cmd.user = {"uid": uid, "gid": uid, "username": "root" if uid == 0 else "user"}
        code = run.namespace["tpot_chkconfig"](cmd, list(args))
        return bytes(cmd.out).decode(), cmd.err, code

    def test_the_recorded_answers_for_a_script_that_is_not_there(self):
        checked = 0
        for key, (out, err, code) in CHKCONFIG.items():
            args = key.split()[1:]
            if "network" in args or args in ([], ["--list"]):
                continue  # the persona has the network script, the recorded image has none
            with self.subTest(key=key):
                self.assertEqual(self.chkconfig(*args), (out, err, code))
            checked += 1
        self.assertEqual(checked, 6)

    def test_the_list_has_the_scripts_of_the_persona_and_the_recorded_note(self):
        for args in ((), ("--list",)):
            self.assertEqual(self.chkconfig(*args), (NETCONSOLE + NETWORK, NOTE, 0))
        self.assertEqual(self.chkconfig("--list", "network"), (NETWORK, NOTE, 0))
        self.assertEqual(self.chkconfig("--list", "netconsole", uid=1000), (NETCONSOLE, NOTE, 0))
        out, err, code = self.chkconfig("--list", "network", "sshd")
        self.assertEqual((out, code), ("", 1))
        self.assertEqual(err, CHKCONFIG["chkconfig --list nosuch other"][1])

    def test_a_change_needs_root_and_a_query_answers_by_status(self):
        # derived from chkconfig.c, not recorded: the text of a user without privileges, the status of a query
        self.assertEqual(self.chkconfig("--level", "3", "network", "on"), ("", "", 0))
        self.assertEqual(self.chkconfig("network", "off", uid=1000), (
            "", "You do not have enough privileges to perform this operation.\n", 1))
        self.assertEqual(self.chkconfig("network")[2], 0)
        self.assertEqual(self.chkconfig("netconsole")[2], 1)

    def test_add_del_override_and_a_level_query(self):
        # derived from chkconfig.c, not recorded: nothing changes, the missing script is named before the privilege check
        for option in ("--add", "--del", "--override"):
            with self.subTest(option):
                self.assertEqual(self.chkconfig(option, "network"), ("", "", 0))
                self.assertEqual(self.chkconfig(option, "nosuch", uid=1000), (
                    "", "error reading information on service nosuch: No such file or directory\n", 1))
                self.assertEqual(self.chkconfig(option, "network", uid=1000), (
                    "", "You do not have enough privileges to perform this operation.\n", 1))
        self.assertEqual(self.chkconfig("--level", "3", "network"), ("", "", 0))
        self.assertEqual(self.chkconfig("--level", "3", "netconsole"), ("", "", 1))

    def test_only_the_centos_7_persona_has_the_command(self):
        for pid, has in (("centos-7", True), ("rhel-9", False), ("debian-bookworm", False)):
            with self.subTest(pid):
                self.assertEqual("chkconfig" in system.python_answers(catalog.by_id(pid)), has)
        self.assertEqual(system.command_path("chkconfig"), "usr/sbin/chkconfig")


class UnitsDataTest(unittest.TestCase):
    """The keys of systemd-units.json the stops and chkconfig read."""

    def test_family_names_the_persona_family_and_the_scripts_are_centos_7s(self):
        for pid in ("centos-7", "rhel-9", "debian-bookworm", "ubuntu-noble"):
            with self.subTest(pid):
                data = json.loads((rendered(pid)[0] / "systemd-units.json").read_text())
                self.assertEqual(data["family"], catalog.by_id(pid).family)
                if pid == "centos-7":
                    self.assertEqual(data["sysv"], {"netconsole": [False] * 7,
                                                    "network": [False, False, True, True, True, True, False]})
                else:
                    self.assertNotIn("sysv", data)


class KillOptionTest(unittest.TestCase):
    """systemctl kill ends the main process like a stop unless a signal is named: -s, -sSIG, --signal, --signal=SIG;
    --kill-whom names the processes, not the signal (systemctl(1))."""

    def test_only_the_signal_options_keep_the_service_running(self):
        for options, stops in ((("-s", "KILL"), False), (("-sKILL",), False), (("--signal", "HUP"), False),
                               (("--signal=HUP",), False), (("--kill-whom=main",), True), (("--system",), True), ((), True)):
            with self.subTest(options=options):
                run = Session("debian-bookworm")
                self.assertEqual(run("kill", *options, "cron.service")[2], 0)
                self.assertEqual(run("is-active", "cron.service")[0], "active\n" if not stops else "inactive\n")
