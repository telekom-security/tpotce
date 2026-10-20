"""systemctl of the persona with the units a session writes, enables, starts and stops: the texts of
systemd's versions (refs/systemd-telnet.json of round 4, recorded in ubuntu:22.04, ubuntu:24.04, debian:12,
debian:13 and almalinux:9 with systemd as PID 1)."""
import functools
import json
import posixpath
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from tests.helpers import DIST, base_tree
from tests.test_patch import FIXTURE, PATCH, FakeCommand, FakeConfig, module_all
from tpot_personas import catalog


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

    def __init__(self, pid, uid=0, files=None):
        out, self.ident = rendered(pid)
        self.out = out
        self.namespace = module_all(patched_service(), {"HoneyPotCommand": FakeCommand, "CowrieConfig": FakeConfig(
            {("shell", "systemctl_units"): str(out / "systemd-units.json"),
             ("shell", "network"): str(out / "network.json")})})
        self.fs = SessionFs(files)
        self.uid = uid
        self.processes = json.loads((out / "cmdoutput.json").read_text())["command"]["ps"]
        self.user = User(self.processes)
        self.boot = time.time() - 3 * 86400

    def __call__(self, *args):
        cmd = FakeCommand(args)
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
        run = self.session("ubuntu-noble", uid=1000)
        self.assertEqual(run("enable", "x"), ("", "Failed to enable unit: Interactive authentication required.\n", 1))
        self.assertEqual(run("start", "x"), ("", "Failed to start x.service: Interactive authentication required.\n"
                                                 "See system logs and 'systemctl status x.service' for details.\n", 4))
        self.assertEqual(run("status", "x")[2], 3)


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
                              "      Users in the 'systemd-journal' group can see all messages. Pass -q to\n"
                              "      turn off this notice.\n"
                              "No journal files were opened due to insufficient permissions.\n")
        out, err, code = self.journal_as("ubuntu", 1000, groups)
        self.assertEqual((err, code, len(out.splitlines())), ("", 0, 3))


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
