#!/usr/bin/env python3

import argparse
import copy
import datetime
import json
import os
import pickle
import random
import shutil
import stat
import time
from pathlib import Path


MIN_PICKLE_SIZE = 1_000_000
PERSONAS_DIRNAME = "personas"
DEFAULT_BOOTSTRAP_PERSONA = "debian-bookworm-vuln"
FORBIDDEN_MARKERS = [
    "phil",
    "Ubuntu 22.04",
    "2.6.26-2-686",
    "2.6.26-19lenny",
    "com/ubuntu/upstart",
    "dannf@debian.org",
]
TXTCMD_PATHS = [
    "bin/df",
    "bin/dmesg",
    "bin/mount",
    "bin/ulimit",
    "usr/bin/lscpu",
    "usr/bin/nproc",
    "usr/bin/top",
]
RANDOM = random.SystemRandom()

A_NAME = 0
A_TYPE = 1
A_UID = 2
A_GID = 3
A_SIZE = 4
A_MODE = 5
A_CTIME = 6
A_CONTENTS = 7
A_TARGET = 8
A_REALFILE = 9

T_LINK = 0
T_DIR = 1
T_FILE = 2


COMMON_REMOVE_PATHS = [
    "home/phil",
    "home/ubuntu",
    "etc/lsb-release",
    "etc/redhat-release",
    "etc/fedora-release",
    "etc/openwrt_release",
    "etc/openwrt_version",
    "etc.defaults/VERSION",
    "etc/config/uLinux.conf",
    "etc/default_config/uLinux.conf",
    "etc_ro/version",
    "etc_ro/product.ini",
    "etc/ubnt/version",
    "etc/zyxel/model",
    "firmware/mnt/info/fwversion",
]


COMMON_DIRS = [
    "bin",
    "dev",
    "etc",
    "home",
    "lib",
    "proc",
    "root",
    "sbin",
    "tmp",
    "usr",
    "usr/bin",
    "usr/sbin",
    "var",
    "var/log",
]


def server_passwd(user, uid, gid, gecos=None):
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
{user}:x:{uid}:{gid}:{gecos}:/home/{user}:/bin/bash
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


def server_shadow(user):
    return f"""root:*:19276:0:99999:7:::
daemon:*:19276:0:99999:7:::
bin:*:19276:0:99999:7:::
sys:*:19276:0:99999:7:::
sync:*:19276:0:99999:7:::
games:*:19276:0:99999:7:::
man:*:19276:0:99999:7:::
lp:*:19276:0:99999:7:::
mail:*:19276:0:99999:7:::
news:*:19276:0:99999:7:::
uucp:*:19276:0:99999:7:::
proxy:*:19276:0:99999:7:::
www-data:*:19276:0:99999:7:::
backup:*:19276:0:99999:7:::
list:*:19276:0:99999:7:::
irc:*:19276:0:99999:7:::
nobody:*:19276:0:99999:7:::
systemd-network:*:19276:0:99999:7:::
systemd-resolve:*:19276:0:99999:7:::
messagebus:*:19276:0:99999:7:::
sshd:*:19276:0:99999:7:::
{user}:*:19276:0:99999:7:::
"""


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


def embedded_shadow(user):
    return f"""root:$1$root$A3qH7I7U2vW0nLJ9rKaNl/:18900:0:99999:7:::
daemon:*:18900:0:99999:7:::
bin:*:18900:0:99999:7:::
nobody:*:18900:0:99999:7:::
sshd:*:18900:0:99999:7:::
{user}:*:18900:0:99999:7:::
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


def random_ps_start():
    today = datetime.date.today()
    day = today - datetime.timedelta(days=RANDOM.randint(3, 240))
    return day.strftime("%b%d")


def ps_entry(user, pid, command, cpu=0.0, mem=0.1, vsz=0, rss=0, stat="S", tty="?", start=None):
    return {
        "USER": user,
        "PID": pid,
        "CPU": cpu,
        "MEM": mem,
        "VSZ": vsz,
        "RSS": rss,
        "TTY": tty,
        "STAT": stat,
        "START": start or "Apr17",
        "TIME": 0.0,
        "COMMAND": command,
    }


def server_processes(persona):
    family = persona["family"]
    user = persona["user"]
    start = persona["process_start"]
    if family == "fedora":
        return [
            ps_entry("root", 1, "/usr/lib/systemd/systemd --switched-root --system --deserialize 31", mem=0.6, vsz=177248, rss=8120, stat="Ss", start=start),
            ps_entry("root", 428, "/usr/lib/systemd/systemd-journald", mem=0.3, vsz=45328, rss=5120, stat="Ss", start=start),
            ps_entry("root", 462, "/usr/lib/systemd/systemd-udevd", mem=0.2, vsz=28576, rss=3776, stat="Ss", start=start),
            ps_entry("dbus", 713, "/usr/bin/dbus-broker-launch --scope system --audit", mem=0.2, vsz=12840, rss=2560, stat="Ss", start=start),
            ps_entry("root", 899, "/usr/sbin/sshd -D", mem=0.2, vsz=19352, rss=4376, stat="Ss", start=start),
            ps_entry("root", 936, "/usr/sbin/crond -n", mem=0.1, vsz=7312, rss=1712, stat="Ss", start=start),
            ps_entry(user, 1512, "-bash", mem=0.1, vsz=9456, rss=3240, stat="Ss", tty="pts/0", start=start),
        ]
    if family == "rhel":
        return [
            ps_entry("root", 1, "/usr/lib/systemd/systemd --switched-root --system --deserialize 30", mem=0.5, vsz=178108, rss=8260, stat="Ss", start=start),
            ps_entry("root", 433, "/usr/lib/systemd/systemd-journald", mem=0.3, vsz=45452, rss=5384, stat="Ss", start=start),
            ps_entry("root", 469, "/usr/lib/systemd/systemd-udevd", mem=0.2, vsz=28964, rss=3900, stat="Ss", start=start),
            ps_entry("dbus", 688, "/usr/bin/dbus-broker-launch --scope system --audit", mem=0.1, vsz=12844, rss=2636, stat="Ss", start=start),
            ps_entry("root", 835, "/usr/sbin/chronyd -F 2", mem=0.2, vsz=29184, rss=3508, stat="S", start=start),
            ps_entry("root", 991, "sshd: /usr/sbin/sshd -D [listener] 0 of 10-100 startups", mem=0.2, vsz=21432, rss=5160, stat="Ss", start=start),
            ps_entry(user, 1770, "-bash", mem=0.1, vsz=9632, rss=3428, stat="Ss", tty="pts/0", start=start),
        ]
    return [
        ps_entry("root", 1, "/lib/systemd/systemd --system --deserialize=31", mem=0.6, vsz=166200, rss=7904, stat="Ss", start=start),
        ps_entry("root", 312, "/lib/systemd/systemd-journald", mem=0.3, vsz=40376, rss=5028, stat="Ss", start=start),
        ps_entry("root", 346, "/lib/systemd/systemd-udevd", mem=0.2, vsz=28772, rss=3512, stat="Ss", start=start),
        ps_entry("systemd+", 571, "/lib/systemd/systemd-networkd", mem=0.2, vsz=24124, rss=4072, stat="Ss", start=start),
        ps_entry("message+", 696, "/usr/bin/dbus-daemon --system --address=systemd:", mem=0.1, vsz=9192, rss=2900, stat="Ss", start=start),
        ps_entry("root", 834, "sshd: /usr/sbin/sshd -D [listener] 0 of 10-100 startups", mem=0.2, vsz=15824, rss=5044, stat="Ss", start=start),
        ps_entry("root", 865, "/usr/sbin/cron -f", mem=0.1, vsz=6812, rss=2180, stat="Ss", start=start),
        ps_entry(user, 1421, "-bash", mem=0.1, vsz=8672, rss=3312, stat="Ss", tty="pts/0", start=start),
    ]


def embedded_processes(persona):
    family = persona["family"]
    user = persona["user"]
    hostname = persona["hostname"]
    start = persona["process_start"]
    if family == "openwrt":
        return [
            ps_entry("root", 1, "/sbin/procd", mem=0.4, vsz=1540, rss=780, stat="S", start=start),
            ps_entry("root", 452, "/sbin/ubusd", mem=0.2, vsz=1240, rss=620, stat="S", start=start),
            ps_entry("root", 701, "/sbin/netifd", mem=0.3, vsz=1760, rss=900, stat="S", start=start),
            ps_entry("root", 918, "/usr/sbin/dropbear -F -P /var/run/dropbear.1.pid", mem=0.2, vsz=1440, rss=720, stat="S", start=start),
            ps_entry("root", 965, "/usr/sbin/uhttpd -f -h /www -r OpenWrt", mem=0.3, vsz=1708, rss=864, stat="S", start=start),
            ps_entry("root", 1120, "ash", mem=0.1, vsz=1224, rss=612, stat="S", tty="pts/0", start=start),
        ]
    if family == "qnap":
        return [
            ps_entry("admin", 1, "init", mem=0.2, vsz=3028, rss=1040, stat="S", start=start),
            ps_entry("admin", 520, "/sbin/daemon_mgr", mem=0.3, vsz=8104, rss=2236, stat="S", start=start),
            ps_entry("admin", 833, "/sbin/qLogEngined", mem=0.5, vsz=18572, rss=5232, stat="S", start=start),
            ps_entry("admin", 1002, "/usr/sbin/sshd -f /etc/config/ssh/sshd_config -D", mem=0.2, vsz=12300, rss=3120, stat="S", start=start),
            ps_entry("admin", 1288, "/sbin/thttpd -p 8080 -d /home/httpd", mem=0.2, vsz=7624, rss=2024, stat="S", start=start),
            ps_entry("admin", 1435, "sh", mem=0.1, vsz=2184, rss=1088, stat="S", tty="pts/0", start=start),
        ]
    if family == "synology":
        return [
            ps_entry("root", 1, "/sbin/init", mem=0.3, vsz=11840, rss=2424, stat="Ss", start=start),
            ps_entry("root", 412, "/usr/syno/bin/synoservice --bootup", mem=0.4, vsz=22048, rss=6024, stat="S", start=start),
            ps_entry("root", 668, "/usr/syno/sbin/synocgid", mem=0.2, vsz=14492, rss=3096, stat="S", start=start),
            ps_entry("root", 900, "/usr/sbin/sshd -D", mem=0.2, vsz=12420, rss=3820, stat="Ss", start=start),
            ps_entry("root", 1004, "/usr/syno/sbin/synoscheduler", mem=0.2, vsz=16884, rss=3596, stat="S", start=start),
            ps_entry("admin", 1477, "sh", mem=0.1, vsz=2328, rss=1092, stat="S", tty="pts/0", start=start),
        ]
    if family == "ubiquiti":
        return [
            ps_entry("root", 1, "/sbin/init", mem=0.2, vsz=2936, rss=1044, stat="Ss", start=start),
            ps_entry("root", 321, "/sbin/ubnt-util", mem=0.2, vsz=3640, rss=1280, stat="S", start=start),
            ps_entry("root", 554, "/usr/sbin/ubnt-daemon", mem=0.3, vsz=6820, rss=2300, stat="S", start=start),
            ps_entry("root", 812, "/usr/sbin/sshd -D", mem=0.2, vsz=11384, rss=2784, stat="Ss", start=start),
            ps_entry("root", 940, "/opt/vyatta/sbin/vyatta-router", mem=0.3, vsz=9488, rss=2956, stat="S", start=start),
            ps_entry(user, 1295, "-vbash", mem=0.1, vsz=3916, rss=1420, stat="S", tty="pts/0", start=start),
        ]
    return [
        ps_entry("root", 1, "init", mem=0.2, vsz=1512, rss=688, stat="S", start=start),
        ps_entry("root", 216, "/sbin/syslogd -n", mem=0.1, vsz=1128, rss=516, stat="S", start=start),
        ps_entry("root", 239, "/sbin/klogd -n", mem=0.1, vsz=1116, rss=508, stat="S", start=start),
        ps_entry("root", 481, "/usr/sbin/dropbear -F -p 22", mem=0.2, vsz=1368, rss=672, stat="S", start=start),
        ps_entry("root", 522, f"/usr/sbin/httpd -h /www -n {hostname}", mem=0.2, vsz=1548, rss=760, stat="S", start=start),
        ps_entry(user, 911, "sh", mem=0.1, vsz=1096, rss=548, stat="S", tty="pts/0", start=start),
    ]


def cmdoutput_for_persona(persona):
    family = persona["family"]
    processes = embedded_processes(persona) if family in {
        "iot-router",
        "iot-nas",
        "openwrt",
        "qnap",
        "synology",
        "ubiquiti",
    } else server_processes(persona)
    return {"command": {"ps": processes}}


def txtcmds_for_persona(persona):
    family = persona["family"]
    arch = persona["hardware_platform"]
    kernel = persona["kernel_version"]
    build = persona["kernel_build_string"]
    hostname = persona["hostname"]
    cpu_count = "1" if arch in ("mips", "armv7l") else "2"

    if family in {"iot-router", "openwrt", "ubiquiti"}:
        df = "Filesystem           1K-blocks      Used Available Use% Mounted on\nrootfs                    8192      3584      4608  44% /\n/dev/root                 8192      8192         0 100% /rom\ntmpfs                    65536       324     65212   1% /tmp\n"
        mount = "rootfs on / type rootfs (rw)\n/dev/root on /rom type squashfs (ro,relatime)\nproc on /proc type proc (rw,nosuid,nodev,noexec,noatime)\ntmpfs on /tmp type tmpfs (rw,nosuid,nodev,noatime)\n"
        lscpu = f"Architecture:          {arch}\nByte Order:            Little Endian\nCPU(s):                {cpu_count}\nModel name:            MIPS 24Kc V7.4\nBogoMIPS:              385.84\n"
        top = "Mem: 28704K used, 36832K free, 0K shrd, 1024K buff, 8212K cached\nCPU:   1% usr   2% sys   0% nic  96% idle   0% io   0% irq   1% sirq\n  PID USER     STATUS   VSZ %VSZ %CPU COMMAND\n    1 root     S       1512   2%   0% init\n  481 root     S       1368   2%   0% dropbear\n"
    elif family in {"iot-nas", "qnap", "synology"}:
        df = "Filesystem           1K-blocks      Used Available Use% Mounted on\n/dev/md9                521684    165312    356372  32% /\ntmpfs                  1024000      2048   1021952   1% /tmp\n/dev/mapper/cachedev1 389120000 185344000 203776000  48% /share/CACHEDEV1_DATA\n"
        mount = "/dev/md9 on / type ext4 (rw,relatime,data=ordered)\nproc on /proc type proc (rw,nosuid,nodev,noexec,relatime)\ntmpfs on /tmp type tmpfs (rw,nosuid,nodev,relatime)\n/dev/mapper/cachedev1 on /share/CACHEDEV1_DATA type ext4 (rw,relatime,data=ordered)\n"
        lscpu = f"Architecture:          {arch}\nCPU(s):                {cpu_count}\nByte Order:            Little Endian\nModel name:            embedded storage processor\n"
        top = "top - 12:00:01 up 14 days,  2:18,  1 user,  load average: 0.08, 0.05, 0.01\nTasks: 102 total,   1 running, 101 sleeping,   0 stopped,   0 zombie\n%Cpu(s):  1.2 us,  0.8 sy,  0.0 ni, 97.6 id,  0.2 wa\n"
    else:
        df = "Filesystem     1K-blocks    Used Available Use% Mounted on\nudev              496124       0    496124   0% /dev\ntmpfs             101784     744    101040   1% /run\n/dev/sda1       20509264 4882112  14562204  26% /\ntmpfs             508904       0    508904   0% /dev/shm\n"
        mount = "/dev/sda1 on / type ext4 (rw,relatime,errors=remount-ro)\nproc on /proc type proc (rw,nosuid,nodev,noexec,relatime)\nsysfs on /sys type sysfs (rw,nosuid,nodev,noexec,relatime)\ntmpfs on /run type tmpfs (rw,nosuid,nodev,noexec,relatime,size=101784k)\n"
        lscpu = f"Architecture:          {arch}\nCPU op-mode(s):        32-bit, 64-bit\nByte Order:            Little Endian\nCPU(s):                {cpu_count}\nModel name:            Common KVM processor\nBogoMIPS:              4800.00\n"
        top = "top - 12:00:01 up 6 days,  3:14,  1 user,  load average: 0.03, 0.04, 0.01\nTasks: 87 total,   1 running, 86 sleeping,   0 stopped,   0 zombie\n%Cpu(s):  0.4 us,  0.3 sy,  0.0 ni, 99.0 id,  0.1 wa\n"

    dmesg = (
        f"[    0.000000] Linux version {kernel} ({hostname}) {build}\n"
        "[    0.000000] Command line: console=ttyS0 root=/dev/sda1 ro quiet\n"
        f"[    0.000000] CPU: {arch} processor initialized\n"
        "[    0.120000] VFS: Mounted root filesystem readonly\n"
        "[    1.420000] random: crng init done\n"
    )
    ulimit = """core file size          (blocks, -c) 0
data seg size           (kbytes, -d) unlimited
scheduling priority             (-e) 0
file size               (blocks, -f) unlimited
open files                      (-n) 1024
stack size              (kbytes, -s) 8192
cpu time               (seconds, -t) unlimited
max user processes              (-u) 1024
virtual memory          (kbytes, -v) unlimited
"""
    return {
        "bin/df": df,
        "bin/dmesg": dmesg,
        "bin/mount": mount,
        "bin/ulimit": ulimit,
        "usr/bin/lscpu": lscpu,
        "usr/bin/nproc": f"{cpu_count}\n",
        "usr/bin/top": top,
    }


def profile(
    persona_id,
    family,
    hostname,
    user,
    uid,
    gid,
    arch,
    kernel_version,
    kernel_build_string,
    ssh_banner,
    shell_ssh_version,
    hardware_platform,
    operating_system,
    passwd,
    group,
    shadow,
    files,
    vulnerability,
    remove_paths=None,
    shell="/bin/sh",
):
    base_files = {
        "etc/passwd": passwd,
        "etc/group": group,
        "etc/shadow": shadow,
        "etc/hostname": f"{hostname}\n",
        "etc/hosts": hosts(hostname),
        "proc/version": f"Linux version {kernel_version} ({hostname}) {kernel_build_string}\n",
        "proc/mounts": "rootfs / rootfs rw 0 0\nproc /proc proc rw,nosuid,nodev,noexec,relatime 0 0\n",
        "proc/cpuinfo": f"processor\t: 0\nmodel name\t: {hardware_platform}\nBogoMIPS\t: 100.00\n",
        "proc/meminfo": "MemTotal:         262144 kB\nMemFree:           32768 kB\n",
    }
    base_files.update(files)
    home_base = "root" if user == "root" else f"home/{user}"
    if shell.endswith("bash") and user != "root":
        base_files.update(home_files(user))
    else:
        base_files[f"{home_base}/.profile"] = "export PATH=/bin:/sbin:/usr/bin:/usr/sbin\n"

    return {
        "id": persona_id,
        "family": family,
        "hostname": hostname,
        "user": user,
        "uid": uid,
        "gid": gid,
        "arch": arch,
        "kernel_version": kernel_version,
        "kernel_build_string": kernel_build_string,
        "ssh_banner": ssh_banner,
        "shell_ssh_version": shell_ssh_version,
        "hardware_platform": hardware_platform,
        "operating_system": operating_system,
        "files": base_files,
        "remove_paths": COMMON_REMOVE_PATHS + (remove_paths or []),
        "vulnerability": vulnerability,
        "shell": shell,
        "process_start": random_ps_start(),
    }


PERSONAS = [
    profile(
        "debian-bookworm-vuln",
        "debian",
        "db12-web01",
        "debian",
        1000,
        1000,
        "linux-x64-lsb",
        "6.1.0-18-amd64",
        "#1 SMP PREEMPT_DYNAMIC Debian 6.1.76-1",
        "SSH-2.0-OpenSSH_9.2p1 Debian-2+deb12u2",
        "OpenSSH_9.2p1, OpenSSL 3.0.11 19 Sep 2023",
        "x86_64",
        "GNU/Linux",
        server_passwd("debian", 1000, 1000, "Debian"),
        server_group("debian", 1000),
        server_shadow("debian"),
        {
            "etc/os-release": """PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"
NAME="Debian GNU/Linux"
VERSION_ID="12"
VERSION="12 (bookworm)"
VERSION_CODENAME=bookworm
ID=debian
HOME_URL="https://www.debian.org/"
SUPPORT_URL="https://www.debian.org/support"
BUG_REPORT_URL="https://bugs.debian.org/"
""",
            "etc/debian_version": "12.5\n",
            "etc/issue": "Debian GNU/Linux 12 \\n \\l\n",
            "etc/issue.net": "Debian GNU/Linux 12\n",
            "etc/motd": "Linux db12-web01 6.1.0-18-amd64 #1 SMP PREEMPT_DYNAMIC Debian 6.1.76-1 x86_64\n",
        },
        "regreSSHion-era OpenSSH 9.2p1 fingerprint",
        shell="/bin/bash",
    ),
    profile(
        "fedora-36-vuln",
        "fedora",
        "fedora-edge",
        "fedora",
        1000,
        1000,
        "linux-x64-lsb",
        "5.17.5-300.fc36.x86_64",
        "#1 SMP PREEMPT Thu Apr 28 15:57:21 UTC 2022",
        "SSH-2.0-OpenSSH_8.8",
        "OpenSSH_8.8p1, OpenSSL 3.0.2 15 Mar 2022",
        "x86_64",
        "GNU/Linux",
        server_passwd("fedora", 1000, 1000, "Fedora"),
        server_group("fedora", 1000),
        server_shadow("fedora"),
        {
            "etc/os-release": """NAME="Fedora Linux"
VERSION="36 (Server Edition)"
ID=fedora
VERSION_ID=36
PLATFORM_ID="platform:f36"
PRETTY_NAME="Fedora Linux 36 (Server Edition)"
ANSI_COLOR="0;38;2;60;110;180"
HOME_URL="https://fedoraproject.org/"
BUG_REPORT_URL="https://bugzilla.redhat.com/"
VARIANT="Server Edition"
VARIANT_ID=server
""",
            "etc/fedora-release": "Fedora release 36 (Thirty Six)\n",
            "etc/issue": "Fedora release 36 (Thirty Six) \\n \\l\n",
            "etc/issue.net": "Fedora release 36 (Thirty Six)\n",
            "etc/motd": "Fedora Linux 36 (Server Edition)\n",
        },
        "EOL Fedora 36 OpenSSH 8.8p1 fingerprint",
        shell="/bin/bash",
    ),
    profile(
        "rhel-9-vuln",
        "rhel",
        "rhel9-app01",
        "cloud-user",
        1000,
        1000,
        "linux-x64-lsb",
        "5.14.0-362.8.1.el9_3.x86_64",
        "#1 SMP PREEMPT_DYNAMIC Red Hat 5.14.0-362.8.1.el9_3",
        "SSH-2.0-OpenSSH_8.7",
        "OpenSSH_8.7p1, OpenSSL 3.0.7 1 Nov 2022",
        "x86_64",
        "GNU/Linux",
        server_passwd("cloud-user", 1000, 1000, "Cloud User"),
        server_group("cloud-user", 1000),
        server_shadow("cloud-user"),
        {
            "etc/os-release": """NAME="Red Hat Enterprise Linux"
VERSION="9.3 (Plow)"
ID="rhel"
ID_LIKE="fedora"
VERSION_ID="9.3"
PLATFORM_ID="platform:el9"
PRETTY_NAME="Red Hat Enterprise Linux 9.3 (Plow)"
ANSI_COLOR="0;31"
CPE_NAME="cpe:/o:redhat:enterprise_linux:9::baseos"
HOME_URL="https://www.redhat.com/"
BUG_REPORT_URL="https://bugzilla.redhat.com/"
""",
            "etc/redhat-release": "Red Hat Enterprise Linux release 9.3 (Plow)\n",
            "etc/issue": "\\S\nKernel \\r on an \\m\n",
            "etc/issue.net": "\\S\nKernel \\r on an \\m\n",
            "etc/motd": "Red Hat Enterprise Linux 9.3 (Plow)\n",
        },
        "RHEL 9 OpenSSH 8.7p1 fingerprint",
        shell="/bin/bash",
    ),
    profile(
        "dlink-dir859",
        "iot-router",
        "DIR-859",
        "admin",
        1000,
        1000,
        "linux-mips-lsb",
        "2.6.30.9",
        "#1 Thu Oct 18 17:14:26 CST 2018",
        "SSH-2.0-dropbear_2012.55",
        "Dropbear sshd 2012.55",
        "mips",
        "Linux",
        embedded_passwd("admin", 1000, 1000, "/bin/sh", "admin"),
        embedded_group("admin", 1000),
        embedded_shadow("admin"),
        {
            "etc/os-release": """NAME="D-Link Embedded Linux"
ID=dlink
PRETTY_NAME="D-Link DIR-859"
VERSION_ID="1.06"
""",
            "etc/issue": "D-Link DIR-859 login: \\l\n",
            "etc/issue.net": "D-Link DIR-859\n",
            "etc/motd": "D-Link DIR-859\n",
            "etc_ro/version": "Firmware External Version: V1.06B01\nFirmware Internal Version: V1.06\n",
            "etc_ro/product.ini": "model=DIR-859\nvendor=D-Link\n",
            "bin/busybox": "BusyBox v1.19.4 (2018-10-18 17:14:26 CST) multi-call binary.\n",
        },
        "D-Link DIR-859 CVE-2019-17621-style router fingerprint",
    ),
    profile(
        "tplink-wr841n",
        "iot-router",
        "TL-WR841N",
        "admin",
        1000,
        1000,
        "linux-mips-lsb",
        "3.18.23",
        "#1 Mon Jan 9 15:21:00 CST 2023",
        "SSH-2.0-dropbear_2015.67",
        "Dropbear sshd 2015.67",
        "mips",
        "Linux",
        embedded_passwd("admin", 1000, 1000, "/bin/sh", "admin"),
        embedded_group("admin", 1000),
        embedded_shadow("admin"),
        {
            "etc/os-release": """NAME="TP-Link Embedded Linux"
ID=tplink
PRETTY_NAME="TP-Link TL-WR841N"
VERSION_ID="0.9.1"
""",
            "etc/issue": "TP-Link Wireless Router \\n \\l\n",
            "etc/issue.net": "TP-Link Wireless Router\n",
            "etc/motd": "TL-WR841N\n",
            "etc/product-info": "product_name=TL-WR841N\nproduct_ver=14.0\n",
            "etc/config/system": "config system\n\toption hostname 'TL-WR841N'\n\toption timezone 'UTC'\n",
            "bin/busybox": "BusyBox v1.25.1 (2023-01-09 15:21:00 CST) multi-call binary.\n",
        },
        "TP-Link TL-WR841N CVE-2023-33538-style router fingerprint",
    ),
    profile(
        "zyxel-nas326",
        "iot-nas",
        "NAS326",
        "admin",
        1000,
        1000,
        "linux-arm-lsb",
        "3.2.54",
        "#1 SMP Tue Jan 14 02:32:09 CST 2020",
        "SSH-2.0-dropbear_2014.63",
        "Dropbear sshd 2014.63",
        "armv7l",
        "Linux",
        embedded_passwd("admin", 1000, 1000, "/bin/sh", "admin"),
        embedded_group("admin", 1000),
        embedded_shadow("admin"),
        {
            "etc/os-release": """NAME="Zyxel NAS"
ID=zyxel
PRETTY_NAME="Zyxel NAS326"
VERSION_ID="5.21"
""",
            "etc/issue": "Welcome to Zyxel NAS326 \\n \\l\n",
            "etc/issue.net": "Zyxel NAS326\n",
            "etc/motd": "Zyxel NAS326\n",
            "etc/zyxel/model": "NAS326\n",
            "firmware/mnt/info/fwversion": "V5.21(AAZF.7)\n",
            "bin/busybox": "BusyBox v1.19.4 (2020-01-14 02:32:09 CST) multi-call binary.\n",
        },
        "Zyxel NAS326 CVE-2020-9054-style NAS fingerprint",
    ),
    profile(
        "openwrt-1806",
        "openwrt",
        "OpenWrt",
        "root",
        0,
        0,
        "linux-mips-lsb",
        "4.14.95",
        "#0 Mon Jan 28 08:54:02 2019",
        "SSH-2.0-dropbear_2017.75",
        "Dropbear sshd 2017.75",
        "mips",
        "Linux",
        """root:x:0:0:root:/root:/bin/ash
daemon:x:1:1:daemon:/var:/bin/false
ftp:x:55:55:ftp:/home/ftp:/bin/false
nobody:x:65534:65534:nobody:/var:/bin/false
""",
        """root:x:0:
daemon:x:1:
adm:x:4:
users:x:100:
nobody:x:65534:
""",
        "root:$1$root$A3qH7I7U2vW0nLJ9rKaNl/:17950:0:99999:7:::\n",
        {
            "etc/os-release": """NAME="OpenWrt"
VERSION="18.06.2"
ID="openwrt"
ID_LIKE="lede openwrt"
PRETTY_NAME="OpenWrt 18.06.2"
VERSION_ID="18.06.2"
HOME_URL="https://openwrt.org/"
BUG_URL="https://bugs.openwrt.org/"
SUPPORT_URL="https://forum.openwrt.org/"
BUILD_ID="r7676-cddd7b4c77"
LEDE_BOARD="ramips/mt7620"
LEDE_ARCH="mipsel_24kc"
LEDE_TAINTS="no-all"
""",
            "etc/openwrt_release": """DISTRIB_ID='OpenWrt'
DISTRIB_RELEASE='18.06.2'
DISTRIB_REVISION='r7676-cddd7b4c77'
DISTRIB_TARGET='ramips/mt7620'
DISTRIB_ARCH='mipsel_24kc'
DISTRIB_DESCRIPTION='OpenWrt 18.06.2 r7676-cddd7b4c77'
DISTRIB_TAINTS='no-all'
""",
            "etc/openwrt_version": "r7676-cddd7b4c77\n",
            "etc/issue": "OpenWrt 18.06.2, r7676-cddd7b4c77 \\n \\l\n",
            "etc/issue.net": "OpenWrt 18.06.2\n",
            "etc/banner": "  _______                     ________        __\n |       |.-----.-----.-----.|  |  |  |.----.|  |_\n |   -   ||  _  |  -__|     ||  |  |  ||   _||   _|\n |_______||   __|_____|__|__||________||__|  |____|\n          |__| W I R E L E S S   F R E E D O M\n",
            "bin/busybox": "BusyBox v1.28.4 () multi-call binary.\n",
        },
        "OpenWrt 18.06 Dropbear router fingerprint",
        remove_paths=["home/root"],
    ),
    profile(
        "qnap-qts",
        "qnap",
        "NAS4BAY",
        "admin",
        1000,
        1000,
        "linux-x64-lsb",
        "5.10.60-qnap",
        "#1 SMP Tue Sep 12 01:23:45 CST 2023",
        "SSH-2.0-OpenSSH_8.4",
        "OpenSSH_8.4p1, OpenSSL 1.1.1t 7 Feb 2023",
        "x86_64",
        "GNU/Linux",
        embedded_passwd("admin", 1000, 1000, "/bin/sh", "admin"),
        embedded_group("admin", 1000),
        embedded_shadow("admin"),
        {
            "etc/os-release": """NAME="QTS"
ID=qnap
PRETTY_NAME="QNAP QTS 5.1.0"
VERSION_ID="5.1.0"
""",
            "etc/issue": "Welcome to QNAP Systems, Inc. \\n \\l\n",
            "etc/issue.net": "QNAP Systems, Inc.\n",
            "etc/motd": "QNAP NAS\n",
            "etc/version": "QTS 5.1.0\n",
            "etc/config/uLinux.conf": "[System]\nModel = TS-451+\nVersion = 5.1.0\nBuild Number = 20230912\n",
            "etc/default_config/uLinux.conf": "[System]\nModel = TS-451+\n",
        },
        "QNAP QTS NAS fingerprint",
    ),
    profile(
        "synology-dsm",
        "synology",
        "DiskStation",
        "admin",
        1000,
        1000,
        "linux-x64-lsb",
        "4.4.302+",
        "#64570 SMP Fri Jul 21 00:00:00 CST 2023",
        "SSH-2.0-OpenSSH_8.2",
        "OpenSSH_8.2p1, OpenSSL 1.1.1t 7 Feb 2023",
        "x86_64",
        "GNU/Linux",
        embedded_passwd("admin", 1000, 1000, "/bin/sh", "admin"),
        embedded_group("admin", 1000),
        embedded_shadow("admin"),
        {
            "etc/os-release": """NAME="Synology DSM"
ID=synology
PRETTY_NAME="Synology DSM 7.1"
VERSION_ID="7.1"
""",
            "etc/issue": "Synology DiskStation \\n \\l\n",
            "etc/issue.net": "Synology DiskStation\n",
            "etc/motd": "Synology DiskStation\n",
            "etc.defaults/VERSION": """majorversion="7"
minorversion="1"
productversion="7.1.1"
buildphase="GM"
buildnumber="42962"
smallfixnumber="6"
builddate="2023/06/05"
""",
            "etc/VERSION": """majorversion="7"
minorversion="1"
productversion="7.1.1"
buildnumber="42962"
""",
        },
        "Synology DSM 7.1 NAS fingerprint",
    ),
    profile(
        "ubiquiti-edgerouter-x",
        "ubiquiti",
        "ubnt",
        "ubnt",
        1000,
        1000,
        "linux-mips-lsb",
        "4.14.54-UBNT",
        "#1 SMP Thu May 25 12:12:35 UTC 2023",
        "SSH-2.0-OpenSSH_7.4",
        "OpenSSH_7.4p1, OpenSSL 1.0.2k-fips 26 Jan 2017",
        "mips",
        "GNU/Linux",
        embedded_passwd("ubnt", 1000, 1000, "/bin/vbash", "Ubiquiti"),
        embedded_group("ubnt", 1000),
        embedded_shadow("ubnt"),
        {
            "etc/os-release": """PRETTY_NAME="EdgeOS"
NAME="EdgeOS"
ID=edgeos
VERSION_ID="2.0.9"
HOME_URL="https://www.ui.com/"
""",
            "etc/issue": "EdgeOS \\n \\l\n",
            "etc/issue.net": "EdgeOS\n",
            "etc/motd": "Welcome to EdgeOS\n",
            "etc/version": "v2.0.9-hotfix.7\n",
            "etc/ubnt/version": "EdgeRouter X v2.0.9-hotfix.7\n",
            "opt/vyatta/etc/config.boot": "system {\n    host-name ubnt\n    login {\n        user ubnt {\n            level admin\n        }\n    }\n}\n",
        },
        "Ubiquiti EdgeRouter CVE-2023-2377-style EdgeOS fingerprint",
    ),
]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate Cowrie persona fs.pickle, honeyfs, configs, and metadata."
    )
    parser.add_argument("--cowrie-root", required=True, type=Path)
    parser.add_argument("--work-dir", default=Path("/tmp/cowrie-personas"), type=Path)
    return parser.parse_args()


def node_get(node, index, default=None):
    return node[index] if len(node) > index else default


def node_set(node, index, value):
    while len(node) <= index:
        node.append(None)
    node[index] = value


def node_children(node):
    return node_get(node, A_CONTENTS, [])


def find_node(root, relative_path):
    if relative_path in ("", "."):
        return root
    current = root
    for part in Path(relative_path).parts:
        if part in ("", "/"):
            continue
        current = next(
            (child for child in node_children(current) if node_get(child, A_NAME) == part),
            None,
        )
        if current is None:
            return None
    return current


def ensure_dir(root, relative_path, uid=0, gid=0, mode=0o755):
    current = root
    now = int(time.time())
    for part in Path(relative_path).parts:
        if part in ("", "/"):
            continue
        match = next(
            (child for child in node_children(current) if node_get(child, A_NAME) == part),
            None,
        )
        if match is None:
            match = [part, T_DIR, uid, gid, 4096, stat.S_IFDIR | mode, now, [], None, None]
            node_children(current).append(match)
        current = match
    node_set(current, A_TYPE, T_DIR)
    node_set(current, A_UID, uid)
    node_set(current, A_GID, gid)
    node_set(current, A_MODE, stat.S_IFDIR | mode)
    return current


def ensure_file(root, relative_path, content, uid=0, gid=0, mode=0o644):
    parent = ensure_dir(root, str(Path(relative_path).parent), uid=0, gid=0)
    name = Path(relative_path).name
    node = next(
        (child for child in node_children(parent) if node_get(child, A_NAME) == name),
        None,
    )
    now = int(time.time())
    if node is None:
        node = [name, T_FILE, uid, gid, len(content), stat.S_IFREG | mode, now, [], None, None]
        node_children(parent).append(node)

    node_set(node, A_NAME, name)
    node_set(node, A_TYPE, T_FILE)
    node_set(node, A_UID, uid)
    node_set(node, A_GID, gid)
    node_set(node, A_SIZE, len(content))
    node_set(node, A_MODE, stat.S_IFREG | mode)
    if not node_get(node, A_CTIME):
        node_set(node, A_CTIME, now)
    node_set(node, A_CONTENTS, content)
    node_set(node, A_TARGET, None)
    node_set(node, A_REALFILE, None)
    return node


def remove_node(root, relative_path):
    parts = [part for part in Path(relative_path).parts if part not in ("", "/")]
    if not parts:
        return
    parent = root
    for part in parts[:-1]:
        parent = next(
            (child for child in node_children(parent) if node_get(child, A_NAME) == part),
            None,
        )
        if parent is None:
            return
    children = node_children(parent)
    children[:] = [child for child in children if node_get(child, A_NAME) != parts[-1]]


def load_pickle(path):
    with path.open("rb") as handle:
        try:
            return pickle.load(handle)
        except UnicodeDecodeError:
            handle.seek(0)
            return pickle.load(handle, encoding="utf-8")


def write_text_file(path, text, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.chmod(path, mode)


def apply_persona_to_tree(tree, persona):
    for remove_path in persona["remove_paths"]:
        remove_node(tree, remove_path)

    for relative_path in COMMON_DIRS:
        mode = 0o755
        if relative_path == "proc":
            mode = 0o555
        if relative_path == "tmp":
            mode = 0o1777
        ensure_dir(tree, relative_path, 0, 0, mode)

    user = persona["user"]
    uid = persona["uid"]
    gid = persona["gid"]
    if user == "root":
        ensure_dir(tree, "root", 0, 0, 0o700)
    else:
        ensure_dir(tree, f"home/{user}", uid, gid, 0o755)

    for relative_path, text in persona["files"].items():
        mode = 0o644
        if relative_path == "etc/shadow":
            mode = 0o640
        if relative_path.startswith("bin/"):
            mode = 0o755
        ensure_file(tree, relative_path, text.encode("utf-8"), 0, 0, mode)

    for relative_path in TXTCMD_PATHS:
        ensure_file(tree, relative_path, b"", 0, 0, 0o755)


def write_honeyfs(target, persona):
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)

    user = persona["user"]
    uid = persona["uid"]
    gid = persona["gid"]
    for relative_path in COMMON_DIRS:
        path = target / relative_path
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(path, 0o1777 if relative_path == "tmp" else 0o755)

    home_path = target / ("root" if user == "root" else f"home/{user}")
    home_path.mkdir(parents=True, exist_ok=True)
    os.chmod(home_path, 0o700 if user == "root" else 0o755)

    for relative_path, text in persona["files"].items():
        mode = 0o644
        if relative_path == "etc/shadow":
            mode = 0o640
        if relative_path.startswith("bin/"):
            mode = 0o755
        write_text_file(target / relative_path, text, mode)

    try:
        os.chown(home_path, uid, gid)
    except OSError:
        pass


def write_txtcmds(target, persona):
    if target.exists():
        shutil.rmtree(target)
    for relative_path, text in txtcmds_for_persona(persona).items():
        write_text_file(target / relative_path, text, 0o644)


def write_cmdoutput(path, persona):
    write_text_file(
        path,
        json.dumps(cmdoutput_for_persona(persona), indent=2, sort_keys=True) + "\n",
        0o644,
    )


def render_cowrie_cfg(persona, cowrie_root, persona_dir):
    return f"""[honeypot]
hostname = {persona["hostname"]}
log_path = {cowrie_root}/log
logtype = plain
download_path = {cowrie_root}/dl
share_path = {cowrie_root}/src/cowrie/data/share/cowrie
state_path = /tmp/cowrie/data
etc_path = {cowrie_root}/etc
contents_path = {persona_dir}/honeyfs
txtcmds_path = {persona_dir}/txtcmds
ttylog = true
ttylog_path = {cowrie_root}/log/tty
interactive_timeout = 180
authentication_timeout = 120
backend = shell
timezone = UTC
auth_class = AuthRandom
auth_class_parameters = 2, 5, 10
data_path = {cowrie_root}/src/cowrie/data

[shell]
filesystem = {persona_dir}/fs.pickle
processes = {persona_dir}/cmdoutput.json
arch = {persona["arch"]}
kernel_version = {persona["kernel_version"]}
kernel_build_string = {persona["kernel_build_string"]}
hardware_platform = {persona["hardware_platform"]}
operating_system = {persona["operating_system"]}
ssh_version = {persona["shell_ssh_version"]}

[ssh]
enabled = true
rsa_public_key = {cowrie_root}/etc/ssh_host_rsa_key.pub
rsa_private_key = {cowrie_root}/etc/ssh_host_rsa_key
dsa_public_key = {cowrie_root}/etc/ssh_host_dsa_key.pub
dsa_private_key = {cowrie_root}/etc/ssh_host_dsa_key
ecdsa_public_key = {cowrie_root}/etc/ssh_host_ecdsa_key.pub
ecdsa_private_key = {cowrie_root}/etc/ssh_host_ecdsa_key
ed25519_public_key = {cowrie_root}/etc/ssh_host_ed25519_key.pub
ed25519_private_key = {cowrie_root}/etc/ssh_host_ed25519_key
public_key_auth = ssh-rsa,ssh-dss,ecdsa-sha2-nistp256,ssh-ed25519
version = {persona["ssh_banner"]}
ciphers = aes128-ctr,aes192-ctr,aes256-ctr,aes256-cbc,aes192-cbc,aes128-cbc,3des-cbc,blowfish-cbc,cast128-cbc
macs = hmac-sha2-512,hmac-sha2-384,hmac-sha2-56,hmac-sha1,hmac-md5
compression = zlib@openssh.com,zlib,none
listen_endpoints = tcp:22:interface=0.0.0.0
sftp_enabled = true
forwarding = false
forward_redirect = false
forward_tunnel = false
auth_none_enabled = false
auth_keyboard_interactive_enabled = true
auth_publickey_allow_any = true

[telnet]
enabled = true
listen_endpoints = tcp:23:interface=0.0.0.0
reported_port = 23
cve_2026_24061_vulnerable = true

[output_jsonlog]
enabled = true
logfile = {cowrie_root}/log/cowrie.json
epoch_timestamp = false

[output_textlog]
enabled = false
logfile = {cowrie_root}/log/cowrie-textlog.log
format = text

[output_crashreporter]
enabled = false
debug = false
"""


def validate_no_marker(path, marker, offenders):
    marker_bytes = marker.lower().encode("utf-8")
    if path.is_file() and marker_bytes in path.read_bytes().lower():
        offenders.append(str(path))


def validate_persona(persona, persona_dir, runtime_persona_dir):
    pickle_path = persona_dir / "fs.pickle"
    honeyfs = persona_dir / "honeyfs"
    config_path = persona_dir / "cowrie.cfg"
    cmdoutput_path = persona_dir / "cmdoutput.json"
    txtcmds = persona_dir / "txtcmds"
    offenders = []

    if pickle_path.stat().st_size < MIN_PICKLE_SIZE:
        raise RuntimeError(
            f"{persona['id']} generated pickle is unexpectedly small: {pickle_path.stat().st_size} bytes"
        )

    for marker in FORBIDDEN_MARKERS:
        validate_no_marker(pickle_path, marker, offenders)

    for root in (honeyfs, txtcmds):
        for item in root.rglob("*"):
            if "phil" in item.name.lower():
                offenders.append(str(item))
                continue
            if item.is_file():
                for marker in FORBIDDEN_MARKERS:
                    validate_no_marker(item, marker, offenders)

    for marker in FORBIDDEN_MARKERS:
        validate_no_marker(config_path, marker, offenders)
        validate_no_marker(cmdoutput_path, marker, offenders)

    if offenders:
        raise RuntimeError(
            f"{persona['id']} generated persona contains forbidden markers: "
            + ", ".join(offenders)
        )

    pickle_bytes = pickle_path.read_bytes()
    required = [
        persona["user"].encode("utf-8"),
        persona["hostname"].encode("utf-8"),
    ]
    for marker in required:
        if marker not in pickle_bytes:
            raise RuntimeError(f"{persona['id']} pickle does not contain {marker!r}")

    if persona["ssh_banner"] not in config_path.read_text(encoding="utf-8"):
        raise RuntimeError(f"{persona['id']} config does not contain SSH banner")
    config_text = config_path.read_text(encoding="utf-8")
    if f"{runtime_persona_dir}/cmdoutput.json" not in config_text:
        raise RuntimeError(f"{persona['id']} config does not point to cmdoutput.json")
    if f"{runtime_persona_dir}/txtcmds" not in config_text:
        raise RuntimeError(f"{persona['id']} config does not point to txtcmds")
    if not cmdoutput_path.is_file():
        raise RuntimeError(f"{persona['id']} has no cmdoutput.json")
    if not txtcmds.is_dir():
        raise RuntimeError(f"{persona['id']} has no txtcmds directory")
    if not json.loads(cmdoutput_path.read_text(encoding="utf-8"))["command"]["ps"]:
        raise RuntimeError(f"{persona['id']} has empty process output")
    process_list = json.loads(cmdoutput_path.read_text(encoding="utf-8"))["command"]["ps"]
    expected_start = persona["process_start"]
    if any(process.get("START") != expected_start for process in process_list):
        raise RuntimeError(f"{persona['id']} has inconsistent process start dates")
    for relative_path in TXTCMD_PATHS:
        if not (txtcmds / relative_path).is_file():
            raise RuntimeError(f"{persona['id']} has no txtcmds/{relative_path}")
    if not (honeyfs / "etc" / "hostname").is_file():
        raise RuntimeError(f"{persona['id']} honeyfs has no /etc/hostname")


def build_persona(default_tree, cowrie_root, personas_root, persona):
    persona_dir = personas_root / persona["id"]
    runtime_persona_dir = cowrie_root / PERSONAS_DIRNAME / persona["id"]
    if persona_dir.exists():
        shutil.rmtree(persona_dir)
    persona_dir.mkdir(parents=True)

    tree = copy.deepcopy(default_tree)
    apply_persona_to_tree(tree, persona)

    with (persona_dir / "fs.pickle").open("wb") as handle:
        pickle.dump(tree, handle)

    write_honeyfs(persona_dir / "honeyfs", persona)
    write_cmdoutput(persona_dir / "cmdoutput.json", persona)
    write_txtcmds(persona_dir / "txtcmds", persona)
    write_text_file(
        persona_dir / "cowrie.cfg",
        render_cowrie_cfg(persona, cowrie_root, runtime_persona_dir),
        0o644,
    )
    validate_persona(persona, persona_dir, runtime_persona_dir)


def write_metadata(personas_root):
    metadata = []
    for persona in PERSONAS:
        metadata.append(
            {
                "id": persona["id"],
                "family": persona["family"],
                "hostname": persona["hostname"],
                "user": persona["user"],
                "arch": persona["arch"],
                "ssh_banner": persona["ssh_banner"],
                "vulnerability": persona["vulnerability"],
            }
        )
    write_text_file(
        personas_root / "personas.json",
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        0o644,
    )


def main():
    args = parse_args()
    cowrie_root = args.cowrie_root.resolve()
    work_dir = args.work_dir.resolve()
    default_pickle_path = cowrie_root / "src" / "cowrie" / "data" / "fs.pickle"
    personas_root = cowrie_root / PERSONAS_DIRNAME

    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)

    default_tree = load_pickle(default_pickle_path)
    generated_root = work_dir / PERSONAS_DIRNAME
    generated_root.mkdir()

    ids = [persona["id"] for persona in PERSONAS]
    if len(ids) != 10 or len(set(ids)) != len(ids):
        raise RuntimeError("Expected exactly 10 unique Cowrie personas")

    for persona in PERSONAS:
        build_persona(default_tree, cowrie_root, generated_root, persona)
    write_metadata(generated_root)

    if personas_root.exists():
        shutil.rmtree(personas_root)
    shutil.copytree(generated_root, personas_root, copy_function=shutil.copy2)

    bootstrap = personas_root / DEFAULT_BOOTSTRAP_PERSONA
    shutil.copy2(bootstrap / "fs.pickle", default_pickle_path)
    source_honeyfs = cowrie_root / "honeyfs"
    if source_honeyfs.exists():
        shutil.rmtree(source_honeyfs)
    shutil.copytree(bootstrap / "honeyfs", source_honeyfs, copy_function=shutil.copy2)


if __name__ == "__main__":
    main()
