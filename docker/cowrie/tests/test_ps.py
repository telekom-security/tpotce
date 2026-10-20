"""ps of the patched Cowrie (Command_tpot_ps of patch_ps_top): procps-ng and BusyBox, its rows the
same as those of top; the layout checked against what the real programs print (tests/fixtures/ps_refs.json:
procps-ng 3.3.10 / 3.3.17 / 4.0.4 in containers, the BusyBox 1.35.0 of OpenWrt 22.03)."""
import json
import re
from pathlib import Path

from tests.test_top import CPUINFO, MEMINFO, NOW, PS, PatchedTest, Session

REFS = json.loads((Path(__file__).parent / "fixtures" / "ps_refs.json").read_text())
PS_HELP = ("\nUsage:\n ps [options]\n\n Try 'ps --help <simple|list|output|threads|misc|all>'\n"
           "  or 'ps --help <s|l|o|t|m|a>'\n for additional help text.\n\nFor more details see ps(1).\n")
UBUNTU = "OpenSSH_9.6p1 Ubuntu-3ubuntu13.14, OpenSSL 3.0.13 30 Jan 2024"
DEBIAN13 = "OpenSSH_10.0p2 Debian-7, OpenSSL 3.5.4 30 Sep 2025"
CENTOS = "OpenSSH_7.4p1, OpenSSL 1.0.2k-fips  26 Jan 2017"
OPENWRT = {("shell", "top_style"): "busybox", ("shell", "ssh_version"): "Dropbear v2022.82",
           ("shell", "text_version"): "1.35.0", ("shell", "text_libc"): "musl",
           ("shell", "text_banner"): "BusyBox v1.35.0 (2024-07-15 22:25:54 UTC)", ("shell", "kernel_threads"): "0"}
ROUTER = [dict(PS[0], COMMAND="/sbin/procd", VSZ=1580, STAT="S"),
          dict(PS[2], PID=1180, VSZ=1104, COMMAND="/usr/sbin/dropbear -F -P /var/run/dropbear.1.pid -p 22 -K 300 -T 3"),
          dict(PS[5], PID=1520, USER="root", COMMAND="-ash", VSZ=1232, RSS=772)]


class Pipeline:
    """The shell that runs a pipeline: the words of its stages and the one running."""

    def __init__(self, words, index):
        self.tpot_pipeline = (words, index)


class PsTest(PatchedTest):
    def run_ps(self, *args, **session):
        made = Session(self, **session)
        cmd = made.run("Command_tpot_ps", *args)
        return made, cmd

    def ps(self, *args, **session):
        return self.run_ps(*args, **session)[1].out.splitlines()


class ProcpsLayoutTest(PsTest):
    """tpot_ps_layout lays out the cells of the recorded rows as procps does."""

    def test_recorded_lines(self):
        session = Session(self)
        layout, fields = session.base["tpot_ps_layout"], session.base["TPOT_PS_FIELDS"]
        for case in REFS["layout"]:
            specs = [(h, w or case["pid_width"], r) for h, w, r in (fields[name] for name in case["format"])]
            for line in case["lines"]:
                with self.subTest(case=case["id"], line=line):
                    cells = line.split(None, len(specs) - 1)
                    self.assertEqual(layout(cells, specs), line)

    def test_times(self):
        base = Session(self).base
        self.assertEqual(base["tpot_ps_bsdtime"](0.4), "0:00")
        self.assertEqual(base["tpot_ps_bsdtime"](754), "12:34")
        self.assertEqual(base["tpot_ps_time"](3725), "01:02:05")
        self.assertEqual(base["tpot_ps_time"](90061), "1-01:01:01")
        self.assertEqual(base["tpot_bb_size"](1584), " 1584")
        self.assertEqual(base["tpot_bb_size"](187280), " 182m")
        self.assertEqual(base["tpot_bb_size"](0), "    0")


class ProcpsTest(PsTest):
    def test_aux_header_and_rows(self):
        lines = self.ps("aux", target="capture")
        self.assertEqual(lines[0], "USER         PID %CPU %MEM    VSZ   RSS TTY      STAT START   TIME COMMAND")
        row = next(line for line in lines if line.endswith("/sbin/init"))
        self.assertRegex(row, r"^root           1  0\.0  0\.2 168520  8140 \?        Ss   Sep30 +\d+:\d\d /sbin/init$")
        shell = next(line for line in lines if line.endswith(" -bash"))
        self.assertRegex(shell, r"^ubuntu      3089  0\.0  0\.1   8688  3376 pts/0    Ss   03:50   0:00 -bash$")
        self.assertRegex(lines[-1], r"^ubuntu   +\d+  0\.0  0\.0   7640  3652 pts/0    R\+   04:00   0:00 ps aux$")

    def test_the_width_of_the_terminal_and_of_a_pipe(self):
        """procps cuts at the columns of a terminal, 132 with w, never with ww or into a pipe, a file or
        a session without a pty (stdout is no terminal; COLUMNS is a variable bash does not export)."""
        lines = self.ps("aux", window=(24, 80))
        self.assertEqual(max(len(line) for line in lines), 80)
        self.assertEqual(max(len(line) for line in self.ps("auxw", window=(24, 80))), 132)
        long = "/usr/bin/python3 /usr/share/unattended-upgrades/unattended-upgrade-shutdown --wait-for-signal"
        for kwargs in ({"target": "capture"}, {"target": "file"}, {"window": None}, {"window": (24, 80), "args": "auxww"}):
            args = kwargs.pop("args", "aux")
            with self.subTest(kwargs=kwargs, args=args):
                self.assertTrue(any(line.endswith(long) for line in self.ps(args, **kwargs)))
        lines = self.ps("-ef", window=(24, 200))
        self.assertTrue(any(line.endswith(long) for line in lines))

    def test_pid_width_follows_pid_max(self):
        lines = self.ps("aux", config={("shell", "pid_max"): "32768"}, target="capture")
        self.assertEqual(lines[0], "USER       PID %CPU %MEM    VSZ   RSS TTY      STAT START   TIME COMMAND")
        self.assertEqual(self.ps("-e", config={("shell", "pid_max"): "32768"}, target="capture")[0],
                         "  PID TTY          TIME CMD")
        self.assertEqual(self.ps("-e", target="capture")[0], "    PID TTY          TIME CMD")

    def test_formats(self):
        self.assertEqual(self.ps("-ef", target="capture")[0], "UID          PID    PPID  C STIME TTY          TIME CMD")
        self.assertEqual(self.ps("x", target="capture")[0], "    PID TTY      STAT   TIME COMMAND")
        self.assertEqual(self.ps("-o", "pid,tty,time,cmd", target="capture")[0], "    PID TT           TIME CMD")
        self.assertEqual(self.ps("-eo", "pid,ppid,tty,stat,comm", target="capture")[0],
                         "    PID    PPID TT       STAT COMMAND")
        # without options: the processes of the terminal, comm
        lines = self.ps(target="capture")
        self.assertEqual(lines[0], "    PID TTY          TIME CMD")
        self.assertEqual(lines[1], "   3089 pts/0    00:00:00 bash")
        self.assertRegex(lines[2], r"^ +\d+ pts/0    00:00:00 ps$")
        self.assertEqual(len(lines), 3)
        # headers after =: one per item from procps 3.3.12, the rest of the list before
        self.assertRegex(self.ps("-o", "pid=,comm=", target="capture")[0], r"^ +3089 bash$")
        old = self.ps("-o", "pid=,comm=", config={("shell", "procps_version"): "3.3.10"}, target="capture")
        self.assertEqual(old[0], " ,comm=")
        self.assertEqual(old[1], "   3089")
        lines = self.ps("-p", "1", target="capture")
        self.assertEqual(lines[0], "    PID TTY          TIME CMD")
        self.assertRegex(lines[1], r"^      1 \?        \d\d:\d\d:\d\d init$")
        self.assertEqual(len(lines), 2)
        self.assertRegex(self.ps("h", "p", "1", target="capture")[0], r"^      1 \?        Ss   +\d+:\d\d /sbin/init$")

    def test_errors_and_version(self):
        for args, text in ((("-Q",), "unsupported SysV option"), (("Q",), "unsupported option (BSD syntax)"),
                           (("--foo",), "unknown gnu long option"), (("-o", "foo"), 'unknown user-defined format specifier "foo"'),
                           (("-p",), "list of process IDs must follow -p"), (("q",), "List of process IDs must follow q."),
                           (("-o",), "format specification must follow -o"), (("--sort",), "long sort specification must follow --sort"),
                           (("-C",), "list of command names must follow -C")):
            with self.subTest(args=args):
                _, cmd = self.run_ps(*args, target="capture")
                self.assertEqual((cmd.out, cmd.err), ("", f"error: {text}\n{PS_HELP}"))
                self.assertEqual(cmd.exit_code, 1)
        _, cmd = self.run_ps("-u", target="capture")
        self.assertEqual((cmd.out, cmd.exit_code), ("USER         PID %CPU %MEM    VSZ   RSS TTY      STAT START   TIME COMMAND\n", 1))
        self.assertEqual(self.ps("--version"), ["ps from procps-ng 4.0.4"])
        self.assertEqual(self.ps("-V", config={("shell", "procps_version"): "3.3.10"}), ["procps-ng version 3.3.10"])
        _, cmd = self.run_ps("--help")
        self.assertEqual((cmd.out, cmd.exit_code), (PS_HELP, 0))

    def test_time_is_minutes_and_seconds(self):
        """BSD TIME as procps prints it (0:00, 12:34), not seconds as a float."""
        for line in self.ps("aux", target="capture")[1:]:
            self.assertRegex(line.split(None, 10)[9], r"^\d+:\d\d$")
        for line in self.ps("-ef", target="capture")[1:]:
            self.assertRegex(line.split(None, 7)[6], r"^\d\d:\d\d:\d\d$")

    def test_every_call_is_a_new_process(self):
        session = Session(self, target="capture")
        first = int(session.run("Command_tpot_ps").out.splitlines()[-1].split()[0])
        second = int(session.run("Command_tpot_ps").out.splitlines()[-1].split()[0])
        self.assertGreater(second, first)
        top = session.run("Command_top", "-bn1")
        self.assertGreater(top.pid, second)


class EveryProcessTest(PsTest):
    def test_every_process_of_every_persona(self):
        """ps -ef, ps aux and ps -e list every process of the persona's list (its daemons too), so
        ps -ef | grep <daemon> finds it; BusyBox ps lists them without options."""
        from tpot_personas import catalog
        from tpot_personas.identity import draw
        for persona in catalog.PERSONAS:
            ident = draw(persona, 4)
            processes = persona.builder(persona, ident).processes
            content = persona.builder(persona, ident)
            busybox = content.top_style == "busybox"
            from tests.helpers import tmpdir
            from tpot_personas import kthreads, render, system
            listed = tmpdir(self) / "k.json"
            listed.write_text(json.dumps(kthreads.kernel_threads(persona, ident, processes, content.filesystems)))
            # the persona's own keys: top_style, procps / BusyBox version, libc, kernel threads
            config = {("shell", k): str(v) for k, v in render.top_keys(persona, ident, content).items()}
            config.update({("shell", k): str(v) for k, v in system.text_keys(persona, ident).items()})
            config[("shell", "ssh_version")] = ident.release.ssh_version
            config[("shell", "kernel_thread_list")] = str(listed)
            for args in ((),) if busybox else (("-ef",), ("aux",), ("-e",)):
                session = Session(self, config=config, processes=processes, username=persona.user, target="capture")
                out = session.run("Command_tpot_ps", *args).out
                with self.subTest(persona=persona.id, args=args):
                    for entry in processes:
                        self.assertRegex(out, rf"(?m)^\S* *{entry['PID']} ")
                        if args != ("-e",):
                            self.assertIn(entry["COMMAND"][:30], out)


class SessionRowsTest(PsTest):
    """The session's processes in ps -ef: the listener of the ps list, OpenSSH's privileged monitor
    ([priv], root) for a user other than root and for root too from OpenSSH 9.8, the session's sshd,
    the shell, ps (as recorded in containers: refs/procs.json ssh:*)."""

    def rows(self, **session):
        lines = self.ps("-ef", target="capture", **session)
        return {int(line.split()[1]): line for line in lines[1:]}

    def test_a_user_other_than_root(self):
        rows = self.rows(config={("shell", "ssh_version"): UBUNTU})
        self.assertRegex(rows[3077], r"^root        3077    1391  0 03:50 \?        00:00:00 sshd: ubuntu \[priv\]$")
        self.assertRegex(rows[3088], r"^ubuntu      3088    3077  0 03:50 \?        00:00:00 sshd: ubuntu@pts/0$")
        self.assertRegex(rows[3089], r"^ubuntu      3089    3088  0 03:50 pts/0    00:00:00 -bash$")
        ps = max(rows)
        self.assertRegex(rows[ps], r" 3089  0 04:00 pts/0    00:00:00 ps -ef$")
        aux = self.ps("aux", target="capture", config={("shell", "ssh_version"): UBUNTU})
        self.assertIn("root        3077  0.0  0.1  17972  9848 ?        Ss   03:50   0:00 sshd: ubuntu [priv]", aux)
        self.assertIn("ubuntu      3088  0.0  0.1  18232  8764 ?        S    03:50   0:00 sshd: ubuntu@pts/0", aux)

    def test_the_shell_is_the_session_users_from_the_login(self):
        """The login shell of the list belongs to who logged in, started at the login (as its sshd)."""
        lines = self.ps("aux", target="capture", username="root")
        self.assertTrue(any(line.startswith("root        3089 ") and line.endswith("Ss   03:50   0:00 -bash")
                            for line in lines), lines)

    def test_root(self):
        processes = [dict(p, USER="root") if p["PID"] == 3089 else p for p in PS]
        rows = self.rows(config={("shell", "ssh_version"): UBUNTU}, processes=processes, username="root")
        self.assertRegex(rows[3088], r"^root        3088    1391  0 03:50 \?        00:00:00 sshd: root@pts/0$")
        self.assertNotIn("[priv]", " ".join(rows.values()))
        # OpenSSH 9.8 and later: sshd-session, with its monitor for root too
        rows = self.rows(config={("shell", "ssh_version"): DEBIAN13}, processes=processes, username="root")
        self.assertRegex(rows[3080], r"^root        3080    1391  0 03:50 \?        00:00:00 sshd-session: root \[priv\]$")
        self.assertRegex(rows[3088], r"^root        3088    3080 .* sshd-session: root@pts/0$")
        # OpenSSH 7.4 of CentOS 7: the monitor two PIDs before the session
        rows = self.rows(config={("shell", "ssh_version"): CENTOS})
        self.assertRegex(rows[3086], r" sshd: ubuntu \[priv\]$")

    def test_ss_keeps_the_session_process(self):
        session = Session(self, config={("shell", "ssh_version"): UBUNTU}, target="capture")
        session.run("Command_tpot_ps", "aux")
        self.assertEqual(session.protocol.tpot_session_pid, 3088)
        self.assertEqual(session.base["tpot_session"](session.protocol, "ubuntu")["title"], "sshd: ubuntu@pts/0")

    def test_top_shows_the_same_rows(self):
        session = Session(self, config={("shell", "ssh_version"): UBUNTU}, target="capture")
        top = session.run("Command_top", "-bn1").out
        self.assertRegex(top, r"\n +3077 root .* sshd\n")
        self.assertRegex(top, r"\n +3088 ubuntu .* sshd\n")


class KernelThreadsTest(PsTest):
    def kernel_session(self, tmp, **kwargs):
        threads = [{"PID": 2, "PPID": 0, "COMMAND": "[kthreadd]", "STAT": "S", "START": "Sep30"},
                   {"PID": 3, "COMMAND": "[rcu_gp]", "STAT": "I<", "START": "Sep30"},
                   {"PID": 14, "COMMAND": "[ksoftirqd/0]", "STAT": "S", "START": "Sep30"},
                   {"PID": 512, "COMMAND": "[kworker/0:2-events]", "STAT": "I", "START": "Sep30"}]
        path = tmp / "kernel_threads.json"
        path.write_text(json.dumps(threads))
        config = {("shell", "kernel_thread_list"): str(path), ("shell", "kernel_threads"): str(len(threads))}
        config.update(kwargs.pop("config", {}))
        return Session(self, config=config, target="capture", **kwargs)

    def test_listed_in_brackets_and_counted_by_top(self):
        from tests.helpers import tmpdir
        tmp = tmpdir(self)
        for ssh in (UBUNTU, CENTOS):
            session = self.kernel_session(tmp, config={("shell", "ssh_version"): ssh})
            lines = session.run("Command_tpot_ps", "aux").out.splitlines()
            with self.subTest(ssh=ssh):
                self.assertTrue(any(re.fullmatch(r"root           2  0\.0  0\.0      0     0 \?        S    Sep30   0:0\d "
                                                 r"\[kthreadd\]", line) for line in lines), lines)
                self.assertTrue(any(re.search(r" I<   Sep30   0:0\d \[rcu_gp\]$", line) for line in lines))
                every = session.run("Command_tpot_ps", "-e").out.splitlines()
                self.assertTrue(any(re.fullmatch(r"      3 \?        00:00:0\d rcu_gp", line) for line in every))
                top = session.run("Command_top", "-bn1").out
                tasks = int(re.search(r"Tasks: *(\d+) total", top).group(1))
                # ps -e and top: the same processes, each with itself
                self.assertEqual(len(every) - 1, tasks)
                ef = session.run("Command_tpot_ps", "-ef").out.splitlines()
                self.assertRegex(next(line for line in ef if line.endswith("[rcu_gp]")), r"^root +3 +2 ")
                self.assertRegex(next(line for line in ef if line.endswith("[kthreadd]")), r"^root +2 +0 ")


class PipelineTest(PsTest):
    def test_top_counts_the_commands_of_its_pipeline(self):
        """top -bn1 | head: the head row and the task count (refs/procs.json ubuntu-24.04:top:*)."""
        session = Session(self, target="capture", config={("shell", "kernel_threads"): "0"})
        session.protocol.cmdstack.append(Pipeline([["top", "-bn1"], ["head", "-15"]], 0))
        top = session.run("Command_top", "-bn1")
        self.assertRegex(top.out, rf"\n +{top.pid + 1} ubuntu .* head\n")
        tasks = int(re.search(r"Tasks: *(\d+) total", top.out).group(1))
        session.protocol.cmdstack[:] = [Pipeline([["ps", "-e"], ["wc", "-l"]], 0)]
        every = session.run("Command_tpot_ps", "-e").out.splitlines()
        self.assertEqual(len(every) - 1, tasks)

    def test_the_commands_after_ps_in_its_pipeline(self):
        session = Session(self, target="capture")
        session.protocol.cmdstack.append(Pipeline([["ps", "aux"], ["grep", "sleep"], ["wc", "-l"]], 0))
        lines = session.run("Command_tpot_ps", "aux").out.splitlines()
        ps, grep, wc = lines[-3:]
        self.assertRegex(ps, r"^ubuntu +(\d+) .* R\+   04:00   0:00 ps aux$")
        pid = int(ps.split()[1])
        self.assertRegex(grep, rf"^ubuntu +{pid + 1} +0\.0  0\.0   3136  1524 pts/0    S\+   04:00   0:00 grep sleep$")
        self.assertTrue(wc.endswith(" wc -l"))
        self.assertEqual(int(wc.split()[1]), pid + 2)

    def test_grep_as_typed(self):
        """The grep row is the command that runs, as typed: the shell expands no alias (Cowrie's
        alias does nothing), also where the persona's .bashrc has one."""
        bashrc = b"    alias grep='grep --color=auto'\n"
        for interactive in (True, False):
            session = Session(self, target="capture", files={"/home/ubuntu/.bashrc": bashrc}, interactive=interactive)
            session.protocol.cmdstack.append(Pipeline([["ps", "aux"], ["grep", "sleep"]], 0))
            with self.subTest(interactive=interactive):
                self.assertTrue(session.run("Command_tpot_ps", "aux").out.splitlines()[-1].endswith("0:00 grep sleep"))

class BusyboxTest(PsTest):
    def bb(self, *args, config=None, **kwargs):
        values = dict(OPENWRT)
        values.update(config or {})
        made = Session(self, config=values, processes=kwargs.pop("processes", ROUTER), username="root", **kwargs)
        return made.run("Command_tpot_ps", *args)

    def test_rows_as_recorded(self):
        """The rows of the BusyBox ps of OpenWrt 22.03 (BusyBox 1.35.0, refs bb:*): VSZ in 5 cells, the
        state in 3 and two spaces, the line one column short of the terminal, also in a pipe."""
        lines = self.bb(target="capture").out.splitlines()
        self.assertEqual(lines[0], "  PID USER       VSZ STAT COMMAND")
        self.assertEqual(lines[1], "    1 root      1580 S    /sbin/procd")
        self.assertIn(" 1180 root      1104 S    /usr/sbin/dropbear -F -P /var/run/dropbear.1.pid -p ", lines)
        self.assertIn(" 1180 root      1104 S    /usr/sbin/dropbear -F -P /var/run/dropbear.1.pid -p 22 -K 300 -T 3",
                      self.bb("ww", target="capture").out.splitlines())
        self.assertIn(" 1520 root      1232 S    -ash", lines)
        self.assertRegex(lines[-1], r"^ +\d+ root      1232 R    ps$")
        ref = REFS["busybox"]
        for name, args in (("ps", ()), ("psw", ("w",)), ("psww", ("ww",))):
            with self.subTest(name=name):
                row = [dict(ROUTER[0], PID=7, VSZ=1264, COMMAND=ref["long"])]
                lines = self.bb(*args, processes=row, target="capture").out.splitlines()
                self.assertEqual(lines[1], ref[name])

    def test_kernel_threads(self):
        from tests.helpers import tmpdir
        path = tmpdir(self) / "k.json"
        path.write_text(json.dumps([{"PID": 2, "PPID": 0, "COMMAND": "[kthreadd]", "STAT": "S"},
                                    {"PID": 9, "COMMAND": "[kworker/0:0H]", "STAT": "S<"}]))
        lines = self.bb(config={("shell", "kernel_thread_list"): str(path)}, target="capture").out.splitlines()
        self.assertIn("    2 root         0 SW   [kthreadd]", lines)
        self.assertIn("    9 root         0 SW<  [kworker/0:0H]", lines)

    def test_options_of_each_firmware(self):
        cmd = self.bb("aux", target="capture")
        self.assertEqual((cmd.out, cmd.err, cmd.exit_code), ("", REFS["busybox"]["aux"], 1))
        cmd = self.bb("-ef", config={("shell", "text_version"): "1.19.2", ("shell", "text_libc"): "uclibc",
                                     ("shell", "text_banner"): "BusyBox v1.19.2 (2022-08-16 12:08:08 CST)"})
        self.assertEqual(cmd.err, "ps: invalid option -- e\nBusyBox v1.19.2 (2022-08-16 12:08:08 CST) multi-call binary.\n"
                                  "\nUsage: ps \n\nShow list of processes\n\n\tw\tWide output\n\n")
        cmd = self.bb("x", config={("shell", "text_version"): "1.14.1", ("shell", "text_libc"): "uclibc",
                                   ("shell", "text_banner"): "BusyBox v1.14.1 (2016-06-28 10:53:08 CST)"})
        self.assertEqual(cmd.err, "ps: invalid option -- x\nBusyBox v1.14.1 (2016-06-28 10:53:08 CST) multi-call binary\n"
                                  "\nUsage: ps \n\nReport process status\n\nOptions:\n\tw\tWide output\n\n")
        cmd = self.bb("-o", "pid", config={("shell", "text_version"): "1.24.1", ("shell", "text_libc"): "glibc",
                                           ("shell", "text_banner"): "BusyBox v1.24.1 (2023-01-01 00:00:00 CST)"})
        self.assertTrue(cmd.err.startswith("ps: invalid option -- 'o'\n"))
        self.assertTrue(cmd.err.endswith("\nUsage: ps \n\nShow list of processes\n\n\tw\tWide output\n"))
        # 1.14: the whole room for the command (later one byte less)
        row = [dict(ROUTER[0], PID=7, VSZ=1264, COMMAND=REFS["busybox"]["long"])]
        old = self.bb(processes=row, target="capture", config={("shell", "text_version"): "1.14.1"}).out.splitlines()
        self.assertEqual(len(old[1]), 79)


AGETTY = {"USER": "root", "PID": 640, "CPU": 0.0, "MEM": 0.0, "VSZ": 5832, "RSS": 1864, "TTY": "tty1", "STAT": "Ss+",
          "START": "Sep30", "TIME": 0.0, "COMMAND": "/sbin/agetty -o -p -- \\u --noclear - linux"}
INETD = {"USER": "root", "PID": 700, "CPU": 0.0, "MEM": 0.0, "VSZ": 4440, "RSS": 1520, "TTY": "?", "STAT": "Ss",
         "START": "Sep30", "TIME": 0.0, "COMMAND": "/usr/sbin/inetd"}
PASSWD = (b"root:x:0:0:root:/root:/bin/bash\nmessagebus:x:101:102::/nonexistent:/usr/sbin/nologin\n"
          b"cloud-user:x:1000:1000::/home/cloud-user:/bin/bash\nubuntu:x:1001:1001::/home/ubuntu:/bin/bash\n")


class FixRoundOneTest(PsTest):
    """Fix round 1 of track C: the getty is no login shell, the telnet session's parent, the chain
    contract, bounded output, selection by full user name and uid, the remaining procps formats."""

    def test_recorded_cells(self):
        base = Session(self).base
        for case in REFS["cells"]:
            specs = base["tpot_ps_specs"](case["format"], case["pid_width"], (4, 0, 4))
            for cells, line in case["lines"]:
                with self.subTest(case=case["id"], line=line):
                    self.assertEqual(base["tpot_ps_row"](cells, specs), line)

    def test_a_getty_is_no_login_shell(self):
        """agetty on tty1 waits from boot on (root, its START, no session leader of ours); the login
        shell is the one on pts/; $$, the session's PIDs, top and ps agree."""
        processes = PS[:5] + [AGETTY] + PS[5:]
        session = Session(self, processes=processes, target="capture")
        lines = session.run("Command_tpot_ps", "aux").out.splitlines()
        getty = next(line for line in lines if "agetty" in line)
        self.assertRegex(getty, r"^root         640  0\.0  0\.0   5832  1864 tty1     Ss\+  Sep30 ")
        self.assertEqual(session.protocol.tpot_session_pid, 3088)
        self.assertEqual(session.base["tpot_login_entry"](AGETTY), False)
        self.assertEqual(session.base["tpot_login_entry"](PS[5]), True)
        top = session.run("Command_top", "-bn1").out
        self.assertRegex(top, r"\n +640 root .* agetty\n")
        # the persona package: the same rule
        from tpot_personas import common, kthreads
        self.assertFalse(common.is_login_shell(AGETTY))
        self.assertTrue(common.is_login_shell(PS[5]))

    def test_telnet_session_parent(self):
        """in.telnetd is a child of inetd (xinetd, a telnetd), else of init; never of sshd."""
        for processes, parent in ((PS[:4] + [INETD] + PS[4:], 700), (PS, 1)):
            session = Session(self, processes=processes, telnet=True, target="capture")
            rows = {int(line.split()[1]): line for line in session.run("Command_tpot_ps", "-ef").out.splitlines()[1:]}
            telnetd = next(pid for pid, line in rows.items() if "in.telnetd" in line)
            with self.subTest(parent=parent):
                self.assertEqual(int(rows[telnetd].split()[2]), parent)
                self.assertRegex(rows[telnetd + 1], rf" {telnetd} .* login -- ubuntu$")

    def test_login_leads_its_session(self):
        """login is Ss in every recording (refs systemd-telnet *-session-ps-aux); inetutils before 2.6 runs
        /bin/login -p on pts/0, so login leads the terminal's session and -bash is no leader there,
        elsewhere -bash leads its own."""
        for style, login, shell in (("inetutils", "/bin/login -p -h 198.51.100.7", "S"),
                                    ("inetutils-2.6", "login -- ubuntu", "Ss"), ("netkit", "login -- ubuntu", "Ss")):
            processes = PS[:4] + [INETD] + PS[4:]
            session = Session(self, processes=processes, telnet=True, target="capture",
                              config={("shell", "telnet_server"): style})
            chain = session.base["tpot_session"](session.protocol, "ubuntu")["chain"]
            with self.subTest(style=style):
                self.assertEqual([entry["stat"] for entry in chain], ["Ss", "Ss"])
                lines = session.run("Command_tpot_ps", "aux").out.splitlines()
                row = next(line for line in lines if line.endswith(f" {login}"))
                self.assertEqual(row.split()[7], "Ss", row)
                bash = next(line for line in lines if line.endswith(" -bash"))
                self.assertEqual(bash.split()[7], shell, bash)

    def test_chain_entries_without_offset_and_with_a_tty(self):
        """tpot_session's contract: an entry without offset counts by its index; an entry's tty is its row's."""
        session = Session(self, target="capture")
        tpot_session = session.base["tpot_session"]

        def chain(protocol, user="root"):
            return {"pid": 5000, "name": "in.telnetd", "title": "in.telnetd: x", "user": "root", "telnet": True,
                    "chain": [{"pid": 5000, "user": "root", "command": "in.telnetd: x", "virt": 1, "res": 1,
                               "stat": "Ss"},
                              {"pid": 5001, "user": "root", "command": "/bin/login -p -h x", "virt": 2, "res": 2,
                               "stat": "S", "tty": "pts/0"}]}
        session.base["tpot_session"] = chain
        lines = session.run("Command_tpot_ps", "-ef").out.splitlines()
        self.assertTrue(any(re.search(r" 5001 +5000 .* pts/0 +00:00:00 /bin/login -p -h x$", line) for line in lines), lines)
        session.base["tpot_session"] = tpot_session
        # the chain of tpot_session itself has the pid of each entry; one without offset counts by index
        source = (self.root / "src/cowrie/commands/base.py").read_text()
        self.assertIn('entry.get("offset", index)', source)
        self.assertIn('use entry["pid"]', source)

    def test_output_is_bounded(self):
        _, cmd = self.run_ps("-eo", ",".join(["args"] * 3000), target="capture")
        self.assertLessEqual(len(cmd.out), (1 << 20) + 4096)
        _, cmd = self.run_ps("-eo", ",".join(["pid"] * 30), target="capture")
        self.assertEqual(len(cmd.out.splitlines()[0]), 30 * 8 - 1)

    def test_users_by_full_name_and_uid(self):
        processes = [dict(p, USER="cloud-u+") if p["PID"] == 3089 else p for p in PS]
        processes.append(dict(PS[1], PID=900, USER="message+", COMMAND="@dbus-daemon --system"))
        files = {"/proc/meminfo": b"MemTotal: 1 kB\n", "/etc/passwd": PASSWD}
        for args, column, wanted in ((("-u", "cloud-user"), 0, {3089}), (("-fu", "cloud-user"), 1, {3089}),
                                     (("-u", "0"), 0, {1, 344, 1391, 1651, 1700}), (("-u", "1000"), 0, {3089}),
                                     (("-U", "messagebus", "-o", "user,pid"), 1, {900}),
                                     (("--user", "cloud-user"), 0, {3089})):
            session = Session(self, processes=processes, files=files, username="cloud-user", target="capture")
            out = session.run("Command_tpot_ps", *args).out.splitlines()[1:]
            pids = {int(line.split()[column]) for line in out}
            with self.subTest(args=args):
                self.assertTrue(wanted <= pids, out)
                if args[0] in ("-u", "--user") and args[1] in ("cloud-user", "1000"):
                    self.assertNotIn(1, pids)
        out = Session(self, processes=processes, files=files, username="cloud-user", target="capture").run(
            "Command_tpot_ps", "-fu", "cloud-user").out.splitlines()
        self.assertTrue(any(line.startswith("cloud-u+    3089    3088 ") for line in out), out)

    def test_a_name_without_an_account_has_no_uid(self):
        processes = [dict(p, USER="cloud-u+") if p["PID"] == 3089 else p for p in PS]
        processes.append(dict(PS[1], PID=910, USER="nosuchus", COMMAND="/usr/sbin/nosuchd"))
        files = {"/proc/meminfo": b"MemTotal: 1 kB\n", "/etc/passwd": PASSWD}

        def run(*args):
            session = Session(self, processes=processes, files=files, username="cloud-user", target="capture")
            return session.run("Command_tpot_ps", *args).out.splitlines()[1:]

        pids = {int(line.split()[0]) for line in run("-u", "1000", "-o", "pid")}
        self.assertIn(3089, pids)
        self.assertNotIn(910, pids)
        self.assertEqual({int(line.split()[0]) for line in run("-u", "nosuchus", "-o", "pid")}, {910})
        self.assertIn("910 -", [" ".join(line.split()) for line in run("-e", "-o", "pid,uid")])

    def test_pid_list_syntax(self):
        for args in (("-p", "1,abc"), ("-p", "abc"), ("p", "x1")):
            _, cmd = self.run_ps(*args, target="capture")
            with self.subTest(args=args):
                self.assertEqual((cmd.out, cmd.err, cmd.exit_code), ("", f"error: process ID list syntax error\n{PS_HELP}", 1))

    def test_d_l_F_and_el(self):
        lines = self.ps("-d", target="capture")
        # all but the session leaders (Ss): the login shell and the daemons leading their sessions are gone
        self.assertNotIn("bash", " ".join(lines[1:]))
        self.assertTrue(any(line.endswith(" ps") for line in lines))
        # the session's user has its account in /etc/passwd (an unresolved name has no uid)
        lines = self.ps("-l", target="capture", files={"/proc/meminfo": MEMINFO, "/proc/cpuinfo": CPUINFO,
                                                       "/etc/passwd": PASSWD})
        self.assertEqual(lines[0], "F S   UID     PID    PPID  C PRI  NI ADDR SZ WCHAN  TTY          TIME CMD")
        self.assertRegex(lines[1], r"^[04] S  \d+    3089    3088  0  80   0 -  +\d+ do_wai pts/0    00:00:00 bash$")
        self.assertRegex(lines[-1], r"^[04] R  +\d+ +\d+    3089  0  80   0 -  +\d+ -      pts/0    00:00:00 ps$")
        lines = self.ps("-F", target="capture")
        self.assertEqual(lines[0], "UID          PID    PPID  C    SZ   RSS PSR STIME TTY          TIME CMD")
        self.assertRegex(lines[1], r"^ubuntu      3089    3088  0  2172  3376 +\d+ 03:50 pts/0    00:00:00 -bash$")
        lines = self.ps("-el", target="capture")
        self.assertRegex(lines[1], r"^4 S     0       1       0  0  80   0 - 42130 \S+ +\? +\d\d:\d\d:\d\d init$")
        self.assertEqual(len(lines), len(self.ps("-e", target="capture")))

    def test_start_lstart_and_widths(self):
        lines = self.ps("-o", "pid,start,lstart,cmd", target="capture")
        self.assertEqual(lines[0], "    PID  STARTED                  STARTED CMD")
        shell = next(line for line in lines if line.endswith("-bash"))
        # the login 600 s before NOW (04:00:00 UTC)
        self.assertEqual(shell, "   3089 03:50:00 Sat Oct  3 03:50:00 2026 -bash")
        lines = self.ps("-o", "user:20,pid,cmd", target="capture")
        self.assertEqual(lines[0], "USER                     PID CMD")
        lines = self.ps("-eo", "pid,args,comm", target="capture")
        self.assertEqual(lines[0], "    PID COMMAND                     COMMAND")
        self.assertTrue(any(line == "   1651 /usr/bin/python3 /usr/share unattended-upgr" for line in lines), lines)

    def test_elapsed_and_start_of_the_session(self):
        session = Session(self, target="capture")
        session.protocol.logintime = NOW - 2 * 86400 - 300
        lines = session.run("Command_tpot_ps", "-eo", "pid,start_time,etime,args").out.splitlines()
        shell = next(line for line in lines if line.endswith("-bash"))
        self.assertEqual(shell, "   3089 Oct01  2-00:05:00 -bash")
        priv = next(line for line in lines if line.endswith("sshd: ubuntu@pts/0"))
        self.assertIn(" 2-00:05:00 ", priv)

    def test_proc_loadavg_counts_as_top(self):
        session = Session(self, target="capture", config={("shell", "ssh_version"): UBUNTU})
        top = session.run("Command_top", "-bn1").out
        tasks = int(re.search(r"Tasks: *(\d+) total", top).group(1))
        loadavg = session.protocol.proc_loadavg().decode()
        self.assertEqual(int(loadavg.split()[3].split("/")[1]), tasks)


class FixRoundTwoTest(PsTest):
    """Fix round 2 of track C: memory bound of wide formats, full user names, an idle getty."""

    def test_wide_formats_stay_in_memory_bounds(self):
        import tracemalloc
        for item in ("pid:9999", "args:9999"):
            session = Session(self, target="capture")
            tracemalloc.start()
            cmd = session.run("Command_tpot_ps", "-eo", ",".join([item] * 4096))
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            with self.subTest(item=item):
                self.assertLess(peak, 24 << 20)
                self.assertLessEqual(len(cmd.out), (1 << 20) + 4096)

    def test_selection_by_the_full_name_of_the_list(self):
        processes = PS + [dict(PS[1], PID=420, USER="systemd+", USERNAME="systemd-network",
                               COMMAND="/usr/lib/systemd/systemd-networkd"),
                          dict(PS[1], PID=430, USER="systemd+", USERNAME="systemd-resolve",
                               COMMAND="/usr/lib/systemd/systemd-resolved")]
        passwd = (b"root:x:0:0:root:/root:/bin/bash\nsystemd-network:x:998:998::/:/usr/sbin/nologin\n"
                  b"systemd-resolve:x:996:996::/:/usr/sbin/nologin\nubuntu:x:1000:1000::/home/ubuntu:/bin/bash\n")
        files = {"/proc/meminfo": b"MemTotal: 1 kB\n", "/etc/passwd": passwd}
        for name, pid in (("systemd-resolve", 430), ("systemd-network", 420), ("996", 430)):
            session = Session(self, processes=processes, files=files, target="capture")
            lines = session.run("Command_tpot_ps", "-u", name).out.splitlines()[1:]
            with self.subTest(name=name):
                self.assertEqual([int(line.split()[0]) for line in lines], [pid])
        session = Session(self, processes=processes, files=files, target="capture")
        lines = session.run("Command_tpot_ps", "-eo", "user:20,pid,uid").out.splitlines()
        self.assertIn("systemd-resolve          430   996", lines)
        self.assertIn("systemd-network          420   998", lines)

    def test_the_persona_keeps_full_user_names(self):
        from tpot_personas import catalog
        from tpot_personas.identity import draw
        persona = catalog.by_id("ubuntu-noble")
        processes = persona.builder(persona, draw(persona, 1)).processes
        names = {p["COMMAND"].rsplit("/", 1)[-1].split()[0]: p for p in processes}
        self.assertEqual((names["systemd-resolved"]["USER"], names["systemd-resolved"]["USERNAME"]),
                         ("systemd+", "systemd-resolve"))
        self.assertEqual(names["@dbus-daemon"]["USERNAME"], "messagebus")
        self.assertTrue(all(p["USER"] == (p["USERNAME"] if len(p["USERNAME"]) <= 8 else p["USERNAME"][:7] + "+")
                            for p in processes))

    def test_a_getty_idles(self):
        processes = PS[:5] + [AGETTY] + PS[5:]
        lines = Session(self, processes=processes, target="capture").run("Command_tpot_ps", "-eo", "time,args").out
        self.assertRegex(lines, r"\n00:00:00 /sbin/agetty ")
        top = Session(self, processes=processes, target="capture").run("Command_top", "-bn1").out
        self.assertRegex(top, r"\n +640 root .* 0:00\.\d\d agetty\n")


class IntegrationTest(PsTest):
    def test_a_process_started_in_the_session_has_used_next_to_no_cpu(self):
        # a unit started by systemctl in the session: its START is now (ps and top count from it)
        started = dict(PS[1], PID=2900, RSS=200000, START="04:00", COMMAND="/bin/sleep 600")
        processes = PS + [started]
        lines = Session(self, processes=processes, target="capture").run("Command_tpot_ps", "-eo", "pid,time,args").out
        self.assertRegex(lines, r"\n   2900 00:00:00 /bin/sleep 600\n")
        top = Session(self, processes=processes, target="capture").run("Command_top", "-bn1").out
        self.assertRegex(top, r"\n +2900 root .* 0:00\.\d\d sleep\n")
        # one started at boot keeps its share of the uptime
        self.assertNotRegex(lines, r"\n +344 00:00:00 ")


class ProcSelfTest(PsTest):
    def test_dollar_dollar_is_the_shell_of_ps(self):
        source = (self.root / "src/cowrie/shell/bashparse.py").read_text()
        self.assertIn("# T-Pot: $$ is the PID of the login shell", source)
        import os
        namespace = {"os": os}
        exec(compile(source.split("# >>> tpot shell pid")[1].split("# <<< tpot shell pid")[0], "<pid>", "exec"),
             namespace)
        session = Session(self)

        class Shell:
            protocol = session.protocol
        self.assertEqual(namespace["tpot_shell_pid"](Shell()), "3089")
        session.protocol.user.server.process = PS[:3]
        self.assertEqual(namespace["tpot_shell_pid"](Shell()), str(session.protocol.tpot_session_pid + 1))


class TopKeysTest(PsTest):
    def test_ctrl_d_and_the_end_of_input_leave_the_screen(self):
        """top reads its terminal raw: Ctrl-D is a key it ignores; a session's input that ends (the
        channel's EOF) does not end the screen either."""
        session = Session(self)
        top = session.run("Command_top")
        top.tpot_key(b"\x04")
        top.eofReceived()
        self.assertFalse(top.exited)
        self.assertTrue(session.reactor.pending())
        top.tpot_key(b"q")
        self.assertTrue(top.exited)

    def test_piped_stdin(self):
        """procps top needs the terminal on stdin (echo x | top: 'failed tty get'); BusyBox top reads
        its keys from the pipe and runs on without a q (refs top-pipe:*)."""
        session = Session(self)
        top = session.base["Command_top"](session.protocol)
        top.input_data = b"x\n"
        top.start()
        self.assertEqual((top.out, top.err, top.code), ("", "top: failed tty get\n", 1))
        session = Session(self)
        top = session.base["Command_top"](session.protocol, "-b", "-n1")
        top.input_data = b"x\n"
        top.start()
        self.assertEqual(top.code, 0)
        self.assertIn("Tasks:", top.out)
        for data, ends in ((b"x\n", False), (b"xq\n", True)):
            session = Session(self, config={("shell", "top_style"): "busybox"})
            top = session.base["Command_top"](session.protocol)
            top.input_data = data
            top.start()
            top.eofReceived()
            with self.subTest(data=data):
                self.assertIn("\x1b[7m  PID  PPID USER", top.out)
                self.assertEqual(top.exited, ends)


class PipelineWordsTest(PsTest):
    def test_the_shell_keeps_the_words_of_its_stages(self):
        """patch_pipeline_words: the words of each stage from the source (quotes off, redirections out),
        set before each stage runs and dropped when the pipeline ends."""
        import functools
        import re as regex
        source = (self.root / "src/cowrie/shell/honeypot.py").read_text()
        self.assertIn("                self.tpot_pipeline = (words, index)\n                d = self.run_child(", source)
        self.assertIn("            self.tpot_pipeline = None\n            self._advance()\n\n"
                      "        words = tpot_stage_words(stages)\n        run_from(0)\n", source)
        start = source.index("def tpot_stage_words(")
        # the module code after it (command-not-found) is defined with it
        namespace = {"re": regex, "functools": functools}
        exec(compile(source[start:source.index("\n\n\nclass HoneyPotShell:")], "<words>", "exec"), namespace)

        class Meta:
            def __init__(self, start, end):
                self.start_pos, self.end_pos, self.empty = start, end, False

        class Word:
            def __init__(self, start, end):
                self.meta = Meta(start, end)

        class Command:
            def __init__(self, line, spans):
                self.line = line
                self.items = [item if isinstance(item, str) else Word(*item) for item in spans]

        first = "ps aux "
        second = """grep "sl"ee'p' 2> /dev/null"""
        stages = [Command(first, [(0, 2), (3, 6)]), Command(second, [(0, 4), (5, 14), "2>", (18, 27)]), object()]
        self.assertEqual(namespace["tpot_stage_words"](stages), [["ps", "aux"], ["grep", "sleep"], ["bash"]])
        # the arguments as the shell passes them: a backslash stays inside single quotes, inside double quotes
        #   it only takes $ ` " \\ and the newline, outside quotes it takes any character
        for text, word in (("'x\\|y'", "x\\|y"), ('"a\\$b\\c\\\\"', "a$b\\c\\"), ("a\\ b\\'c", "a b'c"),
                           ("'it'\\''s'", "it's"), ('"x\'y"', "x'y"), ("'a\"b'", 'a"b')):
            with self.subTest(text=text):
                stage = Command(f"grep {text}", [(0, 4), (5, 5 + len(text))])
                self.assertEqual(namespace["tpot_stage_words"]([stage]), [["grep", word]])


if __name__ == "__main__":
    import unittest
    unittest.main()
