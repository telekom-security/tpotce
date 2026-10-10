"""Content builders shared by servers and devices.

Every value that varies per start comes from the Identity: host name, release,
hardware, dates and PIDs. Random choices only use generators seeded from the
identity, so the same identity always renders the same persona.
"""
import math
import random
import re
import time

from .identity import DAY

TTY_PREFIXES = ("pts/", "tty")


def rng_for(ident, purpose):
    """A generator of its own per purpose, seeded by the identity (str seeds are
    hashed with sha512 by `random`, independent of PYTHONHASHSEED)."""
    return random.Random(f"{ident.seed}/{purpose}")


def gm(ts, fmt):
    return time.strftime(fmt, time.gmtime(ts))


def deb_arch(persona):
    return {"x86_64": "amd64", "aarch64": "arm64", "armv7l": "armhf"}.get(
        persona.hardware_platform, persona.hardware_platform)


# --- accounts -----------------------------------------------------------------

def shadow_days(ident):
    """(system accounts, root, persona user): days since the epoch. System
    accounts were made at installation, the passwords changed since."""
    rng = rng_for(ident, "shadow")
    system = ident.installed_at // DAY
    last = ident.now // DAY
    return system, rng.randint(system, last), rng.randint(system, last)


def server_passwd(user, uid, gid, gecos=None, shell="/bin/bash"):
    gecos = gecos or user
    return f"""root:x:0:0:root:/root:/bin/bash
daemon:x:1:1:daemon:/usr/sbin:/usr/sbin/nologin
bin:x:2:2:bin:/bin:/usr/sbin/nologin
sys:x:3:3:sys:/dev:/usr/sbin/nologin
sync:x:4:65534:sync:/bin:/bin/sync
games:x:5:60:games:/usr/games:/usr/sbin/nologin
man:x:6:12:man:/var/cache/man:/usr/sbin/nologin
lp:x:7:7:lp:/var/spool/lpd:/usr/sbin/nologin
mail:x:8:8:mail:/var/mail:/usr/sbin/nologin
news:x:9:9:news:/var/spool/news:/usr/sbin/nologin
uucp:x:10:10:uucp:/var/spool/uucp:/usr/sbin/nologin
proxy:x:13:13:proxy:/bin:/usr/sbin/nologin
www-data:x:33:33:www-data:/var/www:/usr/sbin/nologin
backup:x:34:34:backup:/var/backups:/usr/sbin/nologin
list:x:38:38:Mailing List Manager:/var/list:/usr/sbin/nologin
irc:x:39:39:ircd:/run/ircd:/usr/sbin/nologin
nobody:x:65534:65534:nobody:/nonexistent:/usr/sbin/nologin
systemd-network:x:998:998:systemd Network Management:/:/usr/sbin/nologin
systemd-resolve:x:997:997:systemd Resolver:/:/usr/sbin/nologin
messagebus:x:100:102::/nonexistent:/usr/sbin/nologin
sshd:x:101:65534::/run/sshd:/usr/sbin/nologin
{user}:x:{uid}:{gid}:{gecos}:/home/{user}:{shell}
"""


def server_group(user, gid):
    return f"""root:x:0:
daemon:x:1:
bin:x:2:
sys:x:3:
adm:x:4:syslog,{user}
tty:x:5:
disk:x:6:
lp:x:7:
mail:x:8:
news:x:9:
uucp:x:10:
man:x:12:
proxy:x:13:
kmem:x:15:
dialout:x:20:{user}
cdrom:x:24:{user}
sudo:x:27:{user}
audio:x:29:{user}
dip:x:30:{user}
www-data:x:33:
backup:x:34:
operator:x:37:
list:x:38:
irc:x:39:
shadow:x:42:
utmp:x:43:
video:x:44:{user}
plugdev:x:46:{user}
staff:x:50:
games:x:60:
users:x:100:
nogroup:x:65534:
systemd-network:x:998:
systemd-resolve:x:997:
messagebus:x:102:
ssh:x:103:
{user}:x:{gid}:
"""


_SERVER_SYSTEM_ACCOUNTS = ("daemon", "bin", "sys", "sync", "games", "man", "lp", "mail", "news", "uucp",
                           "proxy", "www-data", "backup", "list", "irc", "nobody", "systemd-network",
                           "systemd-resolve", "messagebus", "sshd")


def server_shadow(user, ident):
    system, root, own = shadow_days(ident)
    lines = [f"root:*:{root}:0:99999:7:::"]
    lines += [f"{name}:*:{system}:0:99999:7:::" for name in _SERVER_SYSTEM_ACCOUNTS]
    lines.append(f"{user}:*:{own}:0:99999:7:::")
    return "\n".join(lines) + "\n"


def embedded_passwd(user, uid, gid, shell="/bin/sh", gecos=None):
    gecos = gecos or user
    return f"""root:x:0:0:root:/root:/bin/sh
daemon:x:1:1:daemon:/var:/bin/false
bin:x:2:2:bin:/bin:/bin/false
nobody:x:65534:65534:nobody:/var:/bin/false
sshd:x:22:22:sshd:/var/empty:/bin/false
{user}:x:{uid}:{gid}:{gecos}:/home/{user}:{shell}
"""


def embedded_group(user, gid):
    return f"""root:x:0:
daemon:x:1:
bin:x:2:
adm:x:4:{user}
tty:x:5:
disk:x:6:
wheel:x:10:{user}
audio:x:29:
users:x:100:
nobody:x:65534:
sshd:x:22:
{user}:x:{gid}:
"""


def embedded_shadow(user, ident):
    system, root, own = shadow_days(ident)
    return f"""root:$1$root$A3qH7I7U2vW0nLJ9rKaNl/:{root}:0:99999:7:::
daemon:*:{system}:0:99999:7:::
bin:*:{system}:0:99999:7:::
nobody:*:{system}:0:99999:7:::
sshd:*:{system}:0:99999:7:::
{user}:*:{own}:0:99999:7:::
"""


def hosts(hostname):
    return f"""127.0.0.1 localhost
127.0.1.1 {hostname}

::1     localhost ip6-localhost ip6-loopback
ff02::1 ip6-allnodes
ff02::2 ip6-allrouters
"""


def home_files(user):
    return {
        f"home/{user}/.bash_logout": """# ~/.bash_logout: executed by bash(1) when login shell exits.
if [ "$SHLVL" = 1 ]; then
    [ -x /usr/bin/clear_console ] && /usr/bin/clear_console -q
fi
""",
        f"home/{user}/.bashrc": """# ~/.bashrc: executed by bash(1) for non-login shells.
case $- in
    *i*) ;;
      *) return;;
esac

HISTCONTROL=ignoreboth
shopt -s histappend
HISTSIZE=1000
HISTFILESIZE=2000
""",
        f"home/{user}/.profile": """# ~/.profile: executed by the command interpreter for login shells.
if [ "$BASH" ]; then
    [ -f ~/.bashrc ] && . ~/.bashrc
fi
""",
    }


def home_dir(persona):
    return "root" if persona.user == "root" else f"home/{persona.user}"


# --- kernel, CPU, memory ----------------------------------------------------------

def kernel_line(release):
    builder = release.extra.get("kernel_builder", "root@buildhost")
    return f"Linux version {release.kernel_version} ({builder}) {release.kernel_build}"


def proc_version(release):
    return kernel_line(release) + "\n"


def dmesg(release, hardware_platform, cmdline="console=ttyS0 root=/dev/sda1 ro quiet"):
    return (
        f"[    0.000000] {kernel_line(release)}\n"
        f"[    0.000000] Command line: {cmdline}\n"
        f"[    0.000000] CPU: {hardware_platform} processor initialized\n"
        "[    0.120000] VFS: Mounted root filesystem readonly\n"
        "[    1.420000] random: crng init done\n"
    )


def uptime_text(seconds):
    """The `up` part of procps top/uptime: "8 days,  4:22" or "1 day, 17 min"."""
    days, rest = divmod(int(seconds), DAY)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    text = f"{days} day{'s' if days != 1 else ''}, " if days else ""
    return text + (f"{hours:2d}:{minutes:02d}" if hours else f"{minutes} min")


def cpu_mhz(hw):
    match = re.search(r"@\s*([\d.]+)\s*GHz", hw.cpu_model)
    return float(match.group(1)) * 1000 if match else 2399.998


def bogomips(persona, hw):
    if persona.hardware_platform == "x86_64":
        return f"{cpu_mhz(hw) * 2:.2f}"
    if persona.hardware_platform == "mips":
        return "385.84"
    return "50.00"


_X86_FLAGS = ("fpu vme de pse tsc msr pae mce cx8 apic sep mtrr pge mca cmov pat pse36 clflush mmx fxsr "
              "sse sse2 ht syscall nx rdtscp lm constant_tsc rep_good nopl xtopology cpuid tsc_known_freq "
              "pni pclmulqdq ssse3 fma cx16 pcid sse4_1 sse4_2 x2apic movbe popcnt aes xsave avx f16c "
              "rdrand hypervisor lahf_lm abm")


def cpuinfo_x86(persona, hw):
    vendor = "AuthenticAMD" if "AMD" in hw.cpu_model else "GenuineIntel"
    blocks = []
    for i in range(hw.cpus):
        blocks.append(
            f"processor\t: {i}\n"
            f"vendor_id\t: {vendor}\n"
            "cpu family\t: 6\n"
            "model\t\t: 63\n"
            f"model name\t: {hw.cpu_model}\n"
            "stepping\t: 2\n"
            f"cpu MHz\t\t: {cpu_mhz(hw):.3f}\n"
            "cache size\t: 30720 KB\n"
            "physical id\t: 0\n"
            f"siblings\t: {hw.cpus}\n"
            f"core id\t\t: {i}\n"
            f"cpu cores\t: {hw.cpus}\n"
            f"apicid\t\t: {i}\n"
            "fpu\t\t: yes\n"
            "fpu_exception\t: yes\n"
            "cpuid level\t: 13\n"
            "wp\t\t: yes\n"
            f"flags\t\t: {_X86_FLAGS}\n"
            f"bogomips\t: {bogomips(persona, hw)}\n"
            "clflush size\t: 64\n"
            "cache_alignment\t: 64\n"
            "address sizes\t: 46 bits physical, 48 bits virtual\n"
            "power management:\n"
        )
    return "\n".join(blocks) + "\n"


def cpuinfo_arm(persona, hw):
    blocks = []
    for i in range(hw.cpus):
        blocks.append(
            f"processor\t: {i}\n"
            f"model name\t: {hw.cpu_model}\n"
            f"BogoMIPS\t: {bogomips(persona, hw)}\n"
            "Features\t: half thumb fastmult vfp edsp neon vfpv3 tls vfpd32\n"
            "CPU implementer\t: 0x41\n"
            "CPU architecture: 7\n"
            "CPU variant\t: 0x4\n"
            "CPU part\t: 0xc09\n"
            "CPU revision\t: 1\n"
        )
    tail = ""
    if hw.board:
        tail = f"\nHardware\t: {hw.board}\nRevision\t: 0000\nSerial\t\t: 0000000000000000\n"
    return "\n".join(blocks) + tail


def cpuinfo_mips(persona, hw):
    head = f"system type\t\t: {hw.board}\n" if hw.board else ""
    blocks = []
    for i in range(hw.cpus):
        blocks.append(
            f"processor\t\t: {i}\n"
            f"cpu model\t\t: {hw.cpu_model}\n"
            f"BogoMIPS\t\t: {bogomips(persona, hw)}\n"
            "wait instruction\t: yes\n"
            "microsecond timers\t: yes\n"
            "tlb_entries\t\t: 32\n"
            "extra interrupt vector\t: yes\n"
            "isa\t\t\t: mips1 mips2 mips32r1 mips32r2\n"
            "ASEs implemented\t: mips16 dsp\n"
            "shadow register sets\t: 1\n"
            f"core\t\t\t: {i}\n"
            "VCED exceptions\t\t: not available\n"
            "VCEI exceptions\t\t: not available\n"
        )
    return head + "\n".join(blocks) + "\n"


def cpuinfo(persona, hw):
    if persona.hardware_platform == "x86_64":
        return cpuinfo_x86(persona, hw)
    if persona.hardware_platform == "mips":
        return cpuinfo_mips(persona, hw)
    return cpuinfo_arm(persona, hw)


def mem_stats(ident):
    """Memory figures in kB; /proc/meminfo, free and top all read these."""
    rng = random.Random(ident.seed)
    total = ident.hardware.mem_kb
    free, available = sorted(int(total * rng.uniform(0.20, 0.70)) for _ in range(2))
    buffers = int(total * rng.uniform(0.01, 0.05))
    cached = available - free + int(total * rng.uniform(0.0, 0.02))
    return {"total": total, "free": free, "available": available, "buffers": buffers,
            "cached": cached, "shmem": int(total * rng.uniform(0.001, 0.01)), "swap_total": 0, "swap_free": 0}


def meminfo(ident):
    m = mem_stats(ident)
    # Cowrie's free formats MemTotal MemFree MemAvailable Buffers Cached Shmem SwapTotal SwapFree
    rows = [("MemTotal", m["total"]), ("MemFree", m["free"]), ("MemAvailable", m["available"]),
            ("Buffers", m["buffers"]), ("Cached", m["cached"]), ("SwapCached", 0),
            ("SwapTotal", m["swap_total"]), ("SwapFree", m["swap_free"]), ("Shmem", m["shmem"])]
    return "".join(f"{name + ':':<16}{value:>8} kB\n" for name, value in rows)


def lscpu(persona, hw, op_modes=None, with_bogomips=True):
    lines = [f"Architecture:          {persona.hardware_platform}"]
    if op_modes:
        lines.append(f"CPU op-mode(s):        {op_modes}")
    lines += ["Byte Order:            Little Endian",
              f"CPU(s):                {hw.cpus}",
              f"Model name:            {hw.cpu_model}"]
    if with_bogomips:
        lines.append(f"BogoMIPS:              {bogomips(persona, hw)}")
    return "\n".join(lines) + "\n"


def nproc(hw):
    return f"{hw.cpus}\n"


def comm(command):
    """The process name top shows: "/usr/sbin/cron -f" -> cron, "-bash" -> bash."""
    return command.split()[0].rstrip(":").rsplit("/", 1)[-1].lstrip("-")[:15]


def _load(rng):
    return [rng.uniform(0.0, 0.12) for _ in range(3)]


def server_top(ident, processes, tasks):
    rng = rng_for(ident, "top")
    m = mem_stats(ident)
    mib = lambda kb: kb / 1024  # noqa: E731
    used = m["total"] - m["free"] - m["buffers"] - m["cached"]
    l1, l5, l15 = _load(rng)
    idle = rng.uniform(98.0, 99.6)
    lines = [
        f"top - {gm(ident.now, '%H:%M:%S')} up {uptime_text(ident.boot_offset)},  1 user,  "
        f"load average: {l1:.2f}, {l5:.2f}, {l15:.2f}",
        f"Tasks: {tasks:3d} total,   1 running, {tasks - 1:3d} sleeping,   0 stopped,   0 zombie",
        f"%Cpu(s): {(100 - idle) / 2:4.1f} us, {(100 - idle) / 2:4.1f} sy,  0.0 ni, {idle:4.1f} id,  "
        "0.0 wa,  0.0 hi,  0.0 si,  0.0 st",
        f"MiB Mem : {mib(m['total']):8.1f} total, {mib(m['free']):8.1f} free, {mib(used):8.1f} used, "
        f"{mib(m['buffers'] + m['cached']):8.1f} buff/cache",
        f"MiB Swap: {mib(m['swap_total']):8.1f} total, {mib(m['swap_free']):8.1f} free, "
        f"{0.0:8.1f} used. {mib(m['available']):8.1f} avail Mem",
        "",
        "    PID USER      PR  NI    VIRT    RES    SHR S  %CPU  %MEM     TIME+ COMMAND",
    ]
    for p in processes:
        lines.append(f"{p['PID']:7d} {p['USER']:<8}  20   0 {p['VSZ']:7d} {p['RSS']:6d} {p['RSS'] // 2:6d} "
                     f"{p['STAT'][0]} {p['CPU']:5.1f} {p['MEM']:5.1f}   0:00.00 {comm(p['COMMAND'])}")
    return "\n".join(lines) + "\n"


def device_top(ident, processes):
    """BusyBox top: used + free is MemTotal."""
    rng = rng_for(ident, "top")
    m = mem_stats(ident)
    used = m["total"] - m["free"]
    l1, l5, l15 = _load(rng)
    lines = [
        f"Mem: {used}K used, {m['free']}K free, 0K shrd, {m['buffers']}K buff, {m['cached']}K cached",
        "CPU:   1% usr   2% sys   0% nic  96% idle   0% io   0% irq   1% sirq",
        f"Load average: {l1:.2f} {l5:.2f} {l15:.2f} 1/{len(processes) + 40} {processes[-1]['PID']}",
        "  PID USER     STATUS   VSZ %VSZ %CPU COMMAND",
    ]
    for p in processes:
        share = round(p["VSZ"] * 100 / m["total"])
        lines.append(f"{p['PID']:5d} {p['USER']:<8} {p['STAT'][0]:<6} {p['VSZ']:5d} {share:3d}%   0% "
                     f"{p['COMMAND']}")
    return "\n".join(lines) + "\n"


# --- disks ------------------------------------------------------------------

def _df_row(fs, size, used, avail, mount):
    pct = math.ceil(used * 100 / (used + avail)) if used + avail else 0
    return f"{fs:<14}{size:>10}{used:>8}{avail:>10}{pct:>4}% {mount}"


def server_df(ident, root_dev="/dev/sda1", run_lock=True):
    rng = rng_for(ident, "df")
    mem, disk = ident.hardware.mem_kb, ident.hardware.disk_kb
    udev, run, shm = int(mem * 0.4854), mem // 10, mem // 2
    run_used = rng.randint(600, 1300)
    used = int(disk * rng.uniform(0.15, 0.45))
    avail = int(disk * 0.948) - used
    rows = ["Filesystem     1K-blocks    Used Available Use% Mounted on",
            _df_row("udev", udev, 0, udev, "/dev"),
            _df_row("tmpfs", run, run_used, run - run_used, "/run"),
            _df_row(root_dev, disk, used, avail, "/"),
            _df_row("tmpfs", shm, 0, shm, "/dev/shm")]
    if run_lock:
        rows.append(_df_row("tmpfs", 5120, 0, 5120, "/run/lock"))
    return "\n".join(rows) + "\n"


def server_mount(ident, root_dev="/dev/sda1", run_lock=True):
    run = ident.hardware.mem_kb // 10
    text = (f"{root_dev} on / type ext4 (rw,relatime,errors=remount-ro)\n"
            "proc on /proc type proc (rw,nosuid,nodev,noexec,relatime)\n"
            "sysfs on /sys type sysfs (rw,nosuid,nodev,noexec,relatime)\n"
            f"tmpfs on /run type tmpfs (rw,nosuid,nodev,noexec,relatime,size={run}k,mode=755)\n")
    if run_lock:
        text += "tmpfs on /run/lock type tmpfs (rw,nosuid,nodev,noexec,relatime,size=5120k)\n"
    return text


ULIMIT = """core file size          (blocks, -c) 0
data seg size           (kbytes, -d) unlimited
scheduling priority             (-e) 0
file size               (blocks, -f) unlimited
open files                      (-n) 1024
stack size              (kbytes, -s) 8192
cpu time               (seconds, -t) unlimited
max user processes              (-u) 1024
virtual memory          (kbytes, -v) unlimited
"""

PROC_MOUNTS = "rootfs / rootfs rw 0 0\nproc /proc proc rw,nosuid,nodev,noexec,relatime 0 0\n"


# --- processes ------------------------------------------------------------------

def ps_user(name):
    """procps cuts user names longer than 8 characters: cloud-user -> cloud-u+."""
    return name if len(name) <= 8 else name[:7] + "+"


def ps_entry(user, pid, command, cpu=0.0, mem=0.1, vsz=0, rss=0, stat="S", tty="?", start=None):
    return {
        "USER": ps_user(user),
        "PID": pid,
        "CPU": cpu,
        "MEM": mem,
        "VSZ": vsz,
        "RSS": rss,
        "TTY": tty,
        "STAT": stat,
        "START": start or "",
        "TIME": 0.0,
        "COMMAND": command,
    }


def is_login_shell(entry):
    return entry["TTY"].startswith(TTY_PREFIXES)


def assign_processes(ident, entries):
    """PID 1 first, increasing PIDs drawn from pid_seed. Everything started at
    boot; a login shell (on a tty) at some time between boot and now."""
    rng = random.Random(ident.pid_seed)
    boot = gm(ident.booted_at, "%b%d")
    pid = 0
    for entry in entries:
        if is_login_shell(entry):
            pid += rng.randint(200, 20000)
            entry["START"] = gm(rng.randint(ident.booted_at, ident.now), "%b%d")
        else:
            pid = 1 if pid == 0 else pid + (rng.randint(150, 450) if pid == 1 else rng.randint(8, 220))
            entry["START"] = boot
        entry["PID"] = pid
    return entries


# --- the parts every persona has -------------------------------------------------

def base_files(persona, ident, passwd, group, shadow):
    files = {
        "etc/passwd": passwd,
        "etc/group": group,
        "etc/shadow": shadow,
        "etc/hostname": f"{ident.hostname}\n",
        "etc/hosts": hosts(ident.hostname),
        "proc/version": proc_version(ident.release),
        "proc/mounts": PROC_MOUNTS,
        "proc/cpuinfo": cpuinfo(persona, ident.hardware),
        "proc/meminfo": meminfo(ident),
    }
    if persona.kind == "server" and persona.user != "root":
        files.update(home_files(persona.user))
    else:
        files[f"{home_dir(persona)}/.profile"] = "export PATH=/bin:/sbin:/usr/bin:/usr/sbin\n"
    return files


def base_txtcmds(persona, ident, df, mount, lscpu_text, top):
    return {
        "bin/df": df,
        "bin/dmesg": dmesg(ident.release, persona.hardware_platform),
        "bin/mount": mount,
        "bin/ulimit": ULIMIT,
        "usr/bin/lscpu": lscpu_text,
        "usr/bin/nproc": nproc(ident.hardware),
        "usr/bin/top": top,
    }
