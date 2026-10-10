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


def shadow_ctime(ident):
    """/etc/shadow was last written when its newest lastchange day happened."""
    _, root, own = shadow_days(ident)
    day = max(root, own) * DAY
    low = max(day, ident.installed_at)
    return rng_for(ident, "shadow-ctime").randint(low, max(low, min(ident.now, day + DAY - 1)))


def server_passwd(user, uid, gid, gecos=None, shell="/bin/bash", extra=""):
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
{extra}{user}:x:{uid}:{gid}:{gecos}:/home/{user}:{shell}
"""


def server_group(user, gid, extra=""):
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
{extra}{user}:x:{gid}:
"""


_SERVER_SYSTEM_ACCOUNTS = ("daemon", "bin", "sys", "sync", "games", "man", "lp", "mail", "news", "uucp",
                           "proxy", "www-data", "backup", "list", "irc", "nobody", "systemd-network",
                           "systemd-resolve", "messagebus", "sshd")


def server_shadow(user, ident, extra=()):
    system, root, own = shadow_days(ident)
    lines = [f"root:*:{root}:0:99999:7:::"]
    lines += [f"{name}:*:{system}:0:99999:7:::" for name in _SERVER_SYSTEM_ACCOUNTS + tuple(extra)]
    lines.append(f"{user}:*:{own}:0:99999:7:::")
    return "\n".join(lines) + "\n"


# RHEL 9 and CentOS 7 (setup + the packages of a cloud image)
_EL_PASSWD = {
    "rhel": """root:x:0:0:root:/root:/bin/bash
bin:x:1:1:bin:/bin:/sbin/nologin
daemon:x:2:2:daemon:/sbin:/sbin/nologin
adm:x:3:4:adm:/var/adm:/sbin/nologin
lp:x:4:7:lp:/var/spool/lpd:/sbin/nologin
sync:x:5:0:sync:/sbin:/bin/sync
shutdown:x:6:0:shutdown:/sbin:/sbin/shutdown
halt:x:7:0:halt:/sbin:/sbin/halt
mail:x:8:12:mail:/var/spool/mail:/sbin/nologin
operator:x:11:0:operator:/root:/sbin/nologin
games:x:12:100:games:/usr/games:/sbin/nologin
ftp:x:14:50:FTP User:/var/ftp:/sbin/nologin
nobody:x:65534:65534:Kernel Overflow User:/:/sbin/nologin
systemd-coredump:x:999:997:systemd Core Dumper:/:/sbin/nologin
dbus:x:81:81:System message bus:/:/sbin/nologin
tss:x:59:59:Account used for TPM access:/dev/null:/sbin/nologin
sssd:x:998:996:User for sssd:/:/sbin/nologin
chrony:x:997:995:chrony system user:/var/lib/chrony:/sbin/nologin
sshd:x:74:74:Privilege-separated SSH:/usr/share/empty.sshd:/sbin/nologin
""",
    "centos": """root:x:0:0:root:/root:/bin/bash
bin:x:1:1:bin:/bin:/sbin/nologin
daemon:x:2:2:daemon:/sbin:/sbin/nologin
adm:x:3:4:adm:/var/adm:/sbin/nologin
lp:x:4:7:lp:/var/spool/lpd:/sbin/nologin
sync:x:5:0:sync:/sbin:/bin/sync
shutdown:x:6:0:shutdown:/sbin:/sbin/shutdown
halt:x:7:0:halt:/sbin:/sbin/halt
mail:x:8:12:mail:/var/spool/mail:/sbin/nologin
operator:x:11:0:operator:/root:/sbin/nologin
games:x:12:100:games:/usr/games:/sbin/nologin
ftp:x:14:50:FTP User:/var/ftp:/sbin/nologin
nobody:x:99:99:Nobody:/:/sbin/nologin
systemd-network:x:192:192:systemd Network Management:/:/sbin/nologin
dbus:x:81:81:System message bus:/:/sbin/nologin
polkitd:x:999:998:User for polkitd:/:/sbin/nologin
sshd:x:74:74:Privilege-separated SSH:/var/empty/sshd:/sbin/nologin
postfix:x:89:89::/var/spool/postfix:/sbin/nologin
chrony:x:998:996::/var/lib/chrony:/sbin/nologin
""",
}
_EL_GROUP = """root:x:0:
bin:x:1:
daemon:x:2:
sys:x:3:
adm:x:4:{user}
tty:x:5:
disk:x:6:
lp:x:7:
mem:x:8:
kmem:x:9:
wheel:x:10:{user}
cdrom:x:11:
mail:x:12:
man:x:15:
dialout:x:18:
floppy:x:19:
games:x:20:
tape:x:33:
video:x:39:
ftp:x:50:
lock:x:54:
audio:x:63:
users:x:100:
nobody:x:{nobody}:
utmp:x:22:
utempter:x:35:
input:x:999:
systemd-journal:x:190:
dbus:x:81:
sshd:x:74:
{user}:x:{gid}:
"""


def el_passwd(family, user, uid, gid, gecos="Cloud User", shell="/bin/bash"):
    return _EL_PASSWD[family] + f"{user}:x:{uid}:{gid}:{gecos}:/home/{user}:{shell}\n"


def el_group(family, user, gid):
    return _EL_GROUP.format(user=user, gid=gid, nobody=65534 if family == "rhel" else 99)


def el_shadow(family, user, ident):
    system, root, own = shadow_days(ident)
    names = [line.split(":", 1)[0] for line in _EL_PASSWD[family].splitlines()[1:]]
    lines = [f"root:*:{root}:0:99999:7:::"]
    # the accounts of the setup package have "*", those made by useradd -r later "!!"
    lines += [f"{name}:*:{system}:0:99999:7:::" if int(_uid(family, name)) < 50 or name == "nobody"
              else f"{name}:!!:{system}::::::" for name in names]
    lines.append(f"{user}:!!:{own}:0:99999:7:::")
    return "\n".join(lines) + "\n"


def _uid(family, name):
    for line in _EL_PASSWD[family].splitlines():
        fields = line.split(":")
        if fields[0] == name:
            return fields[2]
    raise KeyError(name)


def el_hosts(hostname):
    return ("127.0.0.1   localhost localhost.localdomain localhost4 localhost4.localdomain4\n"
            "::1         localhost localhost.localdomain localhost6 localhost6.localdomain6\n"
            + ("" if hostname.startswith("localhost") else f"127.0.0.1   {hostname}\n"))


def el_home_files(family, user):
    bashrc = {
        "rhel": """# .bashrc

# Source global definitions
if [ -f /etc/bashrc ]; then
    . /etc/bashrc
fi

# User specific environment
if ! [[ "$PATH" =~ "$HOME/.local/bin:$HOME/bin:" ]]
then
    PATH="$HOME/.local/bin:$HOME/bin:$PATH"
fi
export PATH

# Uncomment the following line if you don't like systemctl's auto-paging feature:
# export SYSTEMD_PAGER=

# User specific aliases and functions
if [ -d ~/.bashrc.d ]; then
    for rc in ~/.bashrc.d/*; do
        if [ -f "$rc" ]; then
            . "$rc"
        fi
    done
fi

unset rc
""",
        "centos": """# .bashrc

# Source global definitions
if [ -f /etc/bashrc ]; then
	. /etc/bashrc
fi

# Uncomment the following line if you don't like systemctl's auto-paging feature:
# export SYSTEMD_PAGER=

# User specific aliases and functions
""",
    }[family]
    return {
        f"home/{user}/.bash_logout": "# ~/.bash_logout\n",
        f"home/{user}/.bash_profile": """# .bash_profile

# Get the aliases and functions
if [ -f ~/.bashrc ]; then
	. ~/.bashrc
fi

# User specific environment and startup programs
""",
        f"home/{user}/.bashrc": bashrc,
    }


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
    """/proc/version: release, builder, compiler (when known), uname -v."""
    builder = release.extra.get("kernel_builder", "root@buildhost")
    compiler = release.extra.get("kernel_compiler")
    middle = f" ({compiler})" if compiler else ""
    return f"Linux version {release.kernel_version} ({builder}){middle} {release.kernel_build}"


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


# x86 model name -> family, model, stepping, cache size, MHz, address sizes as a guest sees them.
# Models not listed keep the generic Haswell values of the generator before the persona library.
X86_CPUS = {
    # AWS m5/t3 (Cascade Lake)
    "Intel(R) Xeon(R) Platinum 8259CL CPU @ 2.50GHz": (6, 85, 7, "36608 KB", 2499.998,
                                                         "46 bits physical, 48 bits virtual"),
    # AWS m6a/c6a (Milan); unverified: MHz as shown on the guest
    "AMD EPYC 7R13 Processor": (25, 1, 1, "512 KB", 2649.998, "48 bits physical, 48 bits virtual"),
    # the QEMU/KVM CPU models "Icelake-Server" and "EPYC-Milan" of many VPS hosters; unverified: cache and MHz
    "Intel Xeon Processor (Icelake)": (6, 134, 0, "16384 KB", 2399.998, "40 bits physical, 48 bits virtual"),
    "AMD EPYC-Milan Processor": (25, 1, 1, "512 KB", 2445.406, "40 bits physical, 48 bits virtual"),
    # DigitalOcean basic droplets (an Intel host CPU under DO's own model name); unverified: all values
    "DO-Regular": (6, 85, 7, "16384 KB", 2494.140, "40 bits physical, 48 bits virtual"),
    # GPU servers: Rome (DGX A100), Genoa, Milan
    "AMD EPYC 7742 64-Core Processor": (23, 49, 0, "512 KB", 2245.780, "43 bits physical, 48 bits virtual"),
    "AMD EPYC 9354 32-Core Processor": (25, 17, 1, "1024 KB", 3249.998, "52 bits physical, 57 bits virtual"),
    "AMD EPYC 7543 32-Core Processor": (25, 1, 1, "512 KB", 2794.748, "48 bits physical, 48 bits virtual"),
    # bare metal NAS CPUs (Intel ARK: J1900 Bay Trail-D, N5105 Jasper Lake, C3538 Denverton, J4125 Gemini
    # Lake Refresh); unverified: MHz (the TSC rate) and cache as the kernel reports them
    "Intel(R) Celeron(R) CPU  J1900  @ 1.99GHz": (6, 55, 8, "1024 KB", 1996.800, "36 bits physical, 48 bits virtual"),
    "Intel(R) Celeron(R) N5105 @ 2.00GHz": (6, 156, 0, "4096 KB", 1996.800, "39 bits physical, 48 bits virtual"),
    "Intel(R) Atom(TM) CPU C3538 @ 2.10GHz": (6, 95, 1, "2048 KB", 2100.000, "39 bits physical, 48 bits virtual"),
    "Intel(R) Celeron(R) J4125 CPU @ 2.00GHz": (6, 122, 8, "4096 KB", 1996.800, "39 bits physical, 48 bits virtual"),
}
# Flags and cpuid level of CPUs a persona runs on bare metal. None of these Atom-class cores has AVX,
# FMA or BMI; Bay Trail has no AES-NI. Unverified: the exact flag lists of the kernels of QTS / DSM.
_SILVERMONT = ("fpu vme de pse tsc msr pae mce cx8 apic sep mtrr pge mca cmov pat pse36 clflush dts acpi mmx fxsr "
               "sse sse2 ss ht tm pbe syscall nx rdtscp lm constant_tsc arch_perfmon pebs bts rep_good nopl "
               "xtopology nonstop_tsc cpuid aperfmperf pni pclmulqdq dtes64 monitor ds_cpl vmx est tm2 ssse3 cx16 "
               "xtpr pdcm sse4_1 sse4_2 movbe popcnt tsc_deadline_timer rdrand lahf_lm 3dnowprefetch epb pti ibrs "
               "ibpb stibp tpr_shadow vnmi flexpriority ept vpid tsc_adjust smep erms dtherm ida arat md_clear")
_GOLDMONT = ("fpu vme de pse tsc msr pae mce cx8 apic sep mtrr pge mca cmov pat pse36 clflush dts acpi mmx fxsr sse "
             "sse2 ss ht tm pbe syscall nx pdpe1gb rdtscp lm constant_tsc arch_perfmon pebs bts rep_good nopl "
             "xtopology nonstop_tsc aperfmperf tsc_known_freq pni pclmulqdq dtes64 monitor ds_cpl vmx est tm2 ssse3 "
             "sdbg cx16 xtpr pdcm sse4_1 sse4_2 x2apic movbe popcnt tsc_deadline_timer aes xsave rdrand lahf_lm "
             "3dnowprefetch epb cat_l2 intel_pt tpr_shadow vnmi flexpriority ept vpid fsgsbase tsc_adjust smep erms "
             "mpx rdt_a rdseed smap clflushopt sha_ni xsaveopt xsavec xgetbv1 dtherm arat pln pts")
_TREMONT = (_GOLDMONT.replace(" mpx", "").replace(" epb", " cpuid_fault epb")
            .replace("cat_l2", "cat_l2 cdp_l2 ssbd ibrs ibpb stibp ibrs_enhanced")
            + " clwb xsaves split_lock_detect ida hwp hwp_notify hwp_act_window hwp_epp hwp_pkg_req umip waitpkg "
              "gfni rdpid movdiri movdir64b md_clear flush_l1d arch_capabilities")
X86_BARE_METAL = {
    "Intel(R) Celeron(R) CPU  J1900  @ 1.99GHz": (_SILVERMONT, 11),
    "Intel(R) Atom(TM) CPU C3538 @ 2.10GHz": (_GOLDMONT, 21),
    "Intel(R) Celeron(R) J4125 CPU @ 2.00GHz": (_GOLDMONT + " xsaves umip rdpid", 24),
    "Intel(R) Celeron(R) N5105 @ 2.00GHz": (_TREMONT, 27),
}
VM_PROVIDERS = ("aws", "kvm", "do")  # a guest's cpuinfo has the hypervisor flag, bare metal (dgx, gpu, devices) not
_X86_DEFAULT = (6, 63, 2, "30720 KB", None, "46 bits physical, 48 bits virtual")
# Raspberry Pi: core -> variant, part, revision, features (arm64 /proc/cpuinfo)
ARM64_CORES = {
    "Cortex-A72": ("0x0", "0xd08", "3", "fp asimd evtstrm crc32 cpuid"),
    "Cortex-A76": ("0x4", "0xd0b", "1", "fp asimd evtstrm aes pmull sha1 sha2 crc32 atomics fphp asimdhp cpuid "
                                        "asimdrdm lrcpc dcpop asimddp"),
}
# revision codes of the boards (new-style: memory size, board type, revision)
PI_REVISIONS = {
    ("Raspberry Pi 4 Model B Rev 1.5", 2): "b03115",
    ("Raspberry Pi 4 Model B Rev 1.5", 4): "c03115",
    ("Raspberry Pi 4 Model B Rev 1.5", 8): "d03115",
    ("Raspberry Pi 5 Model B Rev 1.0", 4): "c04170",
    ("Raspberry Pi 5 Model B Rev 1.0", 8): "d04170",
}


def cpu_mhz(hw):
    known = X86_CPUS.get(hw.cpu_model, _X86_DEFAULT)[4]
    if known:
        return known
    match = re.search(r"@\s*([\d.]+)\s*GHz", hw.cpu_model)
    return float(match.group(1)) * 1000 if match else 2399.998


# BogoMIPS of the embedded cores; unverified: the exact values, chosen from the clock of each SoC
# (QCA9563 775 MHz, MT7628 575 MHz, MT7621 880 MHz, Armada 380 1.3 GHz, a 600 MHz Cortex-A7 camera SoC)
EMBEDDED_BOGOMIPS = {
    "MIPS 74Kc V5.0": "386.04",
    "MIPS 24Kc V5.5": "385.84",
    "MIPS 1004Kc V2.15": "586.13",
    "ARMv7 Processor rev 1 (v7l)": "1594.36",
    "ARMv7 Processor rev 5 (v7l)": "1196.85",
}


def bogomips(persona, hw):
    if persona.hardware_platform == "x86_64":
        return f"{cpu_mhz(hw) * 2:.2f}"
    if hw.cpu_model in EMBEDDED_BOGOMIPS:
        return EMBEDDED_BOGOMIPS[hw.cpu_model]
    if persona.hardware_platform == "mips":
        return "385.84"
    if persona.hardware_platform == "aarch64":
        return "108.00"  # the 54 MHz system timer of the Pi 4 and Pi 5
    return "50.00"


_X86_FLAGS = ("fpu vme de pse tsc msr pae mce cx8 apic sep mtrr pge mca cmov pat pse36 clflush mmx fxsr "
              "sse sse2 ht syscall nx rdtscp lm constant_tsc rep_good nopl xtopology cpuid tsc_known_freq "
              "pni pclmulqdq ssse3 fma cx16 pcid sse4_1 sse4_2 x2apic movbe popcnt aes xsave avx f16c "
              "rdrand hypervisor lahf_lm abm")


_AMD_FLAGS = ("fpu vme de pse tsc msr pae mce cx8 apic sep mtrr pge mca cmov pat pse36 clflush mmx fxsr sse "
              "sse2 ht syscall nx mmxext fxsr_opt pdpe1gb rdtscp lm constant_tsc rep_good nopl nonstop_tsc cpuid "
              "extd_apicid tsc_known_freq pni pclmulqdq ssse3 fma cx16 pcid sse4_1 sse4_2 x2apic movbe popcnt aes "
              "xsave avx f16c rdrand hypervisor lahf_lm cmp_legacy cr8_legacy abm sse4a misalignsse 3dnowprefetch "
              "topoext invpcid_single ssbd ibrs ibpb stibp vmmcall fsgsbase bmi1 avx2 smep bmi2 rdseed adx smap "
              "clflushopt clwb sha_ni xsaveopt xsavec xgetbv1 xsaves clzero xsaveerptr rdpru wbnoinvd arat npt "
              "nrip_save umip vaes vpclmulqdq rdpid")


def x86_flags(hw):
    """(flags, cpuid level): of the CPU on bare metal, else the vendor's set; only a VM has `hypervisor`."""
    if hw.cpu_model in X86_BARE_METAL:
        flags, level = X86_BARE_METAL[hw.cpu_model]
    else:
        flags, level = (_AMD_FLAGS if "AMD" in hw.cpu_model else _X86_FLAGS), 13
    if hw.provider not in VM_PROVIDERS:
        flags = " ".join(f for f in flags.split() if f != "hypervisor")
    return flags, level


def cpuinfo_x86(persona, hw):
    vendor = "AuthenticAMD" if "AMD" in hw.cpu_model else "GenuineIntel"
    family, model, stepping, cache, _, address = X86_CPUS.get(hw.cpu_model, _X86_DEFAULT)
    flags, level = x86_flags(hw)
    blocks = []
    for i in range(hw.cpus):
        blocks.append(
            f"processor\t: {i}\n"
            f"vendor_id\t: {vendor}\n"
            f"cpu family\t: {family}\n"
            f"model\t\t: {model}\n"
            f"model name\t: {hw.cpu_model}\n"
            f"stepping\t: {stepping}\n"
            f"cpu MHz\t\t: {cpu_mhz(hw):.3f}\n"
            f"cache size\t: {cache}\n"
            "physical id\t: 0\n"
            f"siblings\t: {hw.cpus}\n"
            f"core id\t\t: {i}\n"
            f"cpu cores\t: {hw.cpus}\n"
            f"apicid\t\t: {i}\n"
            "fpu\t\t: yes\n"
            "fpu_exception\t: yes\n"
            f"cpuid level\t: {level}\n"
            "wp\t\t: yes\n"
            f"flags\t\t: {flags}\n"
            f"bogomips\t: {bogomips(persona, hw)}\n"
            "clflush size\t: 64\n"
            "cache_alignment\t: 64\n"
            f"address sizes\t: {address}\n"
            "power management:\n"
        )
    return "\n".join(blocks) + "\n"


# ARMv7 cores: CPU variant, part, revision and the features a 3.10 / 3.18 kernel lists
ARM_CORES = {
    "ARMv7 Processor rev 1 (v7l)": ("0x4", "0xc09", "1", "swp half thumb fastmult vfp edsp neon vfpv3 tls vfpd32"),
    "ARMv7 Processor rev 5 (v7l)": ("0x0", "0xc07", "5",
                                    "swp half thumb fastmult vfp edsp neon vfpv3 tls vfpv4 idiva idivt vfpd32 lpae"),
}


def cpuinfo_arm(persona, hw):
    """ARMv7 /proc/cpuinfo of a 3.8+ kernel: one block per CPU, then Hardware, Revision, Serial."""
    variant, part, revision, features = ARM_CORES.get(
        hw.cpu_model, ("0x4", "0xc09", "1", "half thumb fastmult vfp edsp neon vfpv3 tls vfpd32"))
    blocks = []
    for i in range(hw.cpus):
        blocks.append(
            f"processor\t: {i}\n"
            f"model name\t: {hw.cpu_model}\n"
            f"BogoMIPS\t: {bogomips(persona, hw)}\n"
            f"Features\t: {features} \n"
            "CPU implementer\t: 0x41\n"
            "CPU architecture: 7\n"
            f"CPU variant\t: {variant}\n"
            f"CPU part\t: {part}\n"
            f"CPU revision\t: {revision}\n"
        )
    tail = ""
    if hw.board:
        tail = f"\nHardware\t: {hw.board}\nRevision\t: 0000\nSerial\t\t: 0000000000000000\n"
    return "\n".join(blocks) + tail


# MIPS cores: PRId (dmesg "CPU revision is"), watch registers, ASEs on a 2.6 / a 4.x+ kernel, MT (VPEs)
MIPS_CORES = {
    "MIPS 74Kc V5.0": ("00019750", "[0x0000, 0x0ff8, 0x0ff8, 0x0ff8]", "mips16 dsp", "mips16 dsp dsp2", False),
    "MIPS 24Kc V5.5": ("00019655", "[0x0ffc, 0x0ffc, 0x0ffb, 0x0ffb]", "mips16 dsp", "mips16 dsp", False),
    "MIPS 1004Kc V2.15": ("0001992f", "[0x0ffc, 0x0ffc, 0x0ffb, 0x0ffb]", "mips16 dsp mt", "mips16 dsp mt", True),
}
_MIPS_OPTIONS = ("tlb 4kex 4k_cache prefetch mcheck ejtag llsc pindexed_dcache userlocal vint perf_cntr_intr_bit "
                 "cdmm perf")


def kernel_series(kernel_version):
    """"2.6.31-svn58067" -> (2, 6), "5.10.221" -> (5, 10)."""
    found = re.match(r"(\d+)\.(\d+)", kernel_version or "")
    return (int(found.group(1)), int(found.group(2))) if found else (0, 0)


def cpuinfo_mips(persona, hw, kernel=""):
    """MIPS /proc/cpuinfo as the kernel prints it: 2.6 kernels without machine, isa
    and Options lines; 4.x and later with them, kscratch, package and the VPE of MT cores."""
    _, watch, ases_old, ases_new, mt = MIPS_CORES.get(hw.cpu_model, (None, None, "mips16 dsp", "mips16 dsp", False))
    new = kernel_series(kernel) >= (3, 0)
    head = f"system type\t\t: {hw.board}\n" if hw.board else ""
    if new and hw.machine:
        head += f"machine\t\t\t: {hw.machine}\n"
    blocks = []
    for i in range(hw.cpus):
        lines = [f"processor\t\t: {i}",
                 f"cpu model\t\t: {hw.cpu_model}",
                 f"BogoMIPS\t\t: {bogomips(persona, hw)}",
                 "wait instruction\t: yes",
                 "microsecond timers\t: yes",
                 "tlb_entries\t\t: 32",
                 "extra interrupt vector\t: yes"]
        if watch:
            lines.append(f"hardware watchpoint\t: yes, count: 4, address/irw mask: {watch}")
        if new:
            lines.append("isa\t\t\t: mips1 mips2 mips32r1 mips32r2")
        lines.append(f"ASEs implemented\t: {ases_new if new else ases_old}")
        if new:
            lines.append(f"Options implemented\t: {_MIPS_OPTIONS}")
        lines.append("shadow register sets\t: 1")
        if new:
            lines += ["kscratch registers\t: 0", "package\t\t\t: 0"]
        lines.append(f"core\t\t\t: {i // 2 if mt else i}")
        if new and mt:
            lines.append(f"VPE\t\t\t: {i % 2}")
        lines += ["VCED exceptions\t\t: not available", "VCEI exceptions\t\t: not available"]
        blocks.append("\n".join(lines) + "\n")
    return head + "\n".join(blocks) + "\n"


def cpuinfo_arm64(persona, hw, serial):
    """arm64 /proc/cpuinfo has no model name; the Pi firmware adds Revision, Serial and Model."""
    variant, part, revision, features = ARM64_CORES[hw.cpu_model]
    blocks = []
    for i in range(hw.cpus):
        blocks.append(
            f"processor\t: {i}\n"
            f"BogoMIPS\t: {bogomips(persona, hw)}\n"
            f"Features\t: {features}\n"
            "CPU implementer\t: 0x41\n"
            "CPU architecture: 8\n"
            f"CPU variant\t: {variant}\n"
            f"CPU part\t: {part}\n"
            f"CPU revision\t: {revision}\n"
        )
    code = PI_REVISIONS.get((hw.board, max(1, round(hw.mem_kb / 1024 / 1024))), "0000")
    return "\n".join(blocks) + f"\nRevision\t: {code}\nSerial\t\t: {serial}\nModel\t\t: {hw.board}\n"


def root_uuid(ident, purpose="root-uuid"):
    """A file system or GPT partition UUID of this identity (version 4 form)."""
    bits = f"{rng_for(ident, purpose).getrandbits(128):032x}"
    return f"{bits[:8]}-{bits[8:12]}-4{bits[13:16]}-{'89ab'[int(bits[16], 16) % 4]}{bits[17:20]}-{bits[20:]}"


def pi_partuuid(ident):
    """The disk identifier pi-gen writes; partitions are <id>-01 (boot) and <id>-02 (root)."""
    return f"{rng_for(ident, 'partuuid').getrandbits(32):08x}"


def pi_serial(ident):
    rng = rng_for(ident, "serial")
    if "Pi 4" in ident.hardware.board:
        return "10000000" + f"{rng.getrandbits(32):08x}"
    return f"{rng.getrandbits(64):016x}"


def cpuinfo(persona, hw, serial="0000000000000000", kernel=""):
    if persona.hardware_platform == "x86_64":
        return cpuinfo_x86(persona, hw)
    if persona.hardware_platform == "mips":
        return cpuinfo_mips(persona, hw, kernel)
    if persona.hardware_platform == "aarch64":
        return cpuinfo_arm64(persona, hw, serial)
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
              f"CPU(s):                {hw.cpus}"]
    if persona.hardware_platform == "x86_64":
        lines.append(f"Vendor ID:             {'AuthenticAMD' if 'AMD' in hw.cpu_model else 'GenuineIntel'}")
    elif persona.hardware_platform == "aarch64":
        lines.append("Vendor ID:             ARM")
    lines.append(f"Model name:            {hw.cpu_model}")
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
    # at least one space between columns, also for long device names and multi-TB disks
    return f"{fs:<14} {size:>9} {used:>7} {avail:>9} {pct:>3}% {mount}"


def server_df(ident, root_dev="/dev/sda1", run_lock=True, boot=None):
    """boot: (device, size kB, mount point) of a boot partition, e.g. the Pi's FAT /boot/firmware."""
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
    if boot:
        dev, size, mount = boot
        boot_used = int(size * rng.uniform(0.12, 0.14))
        rows.append(_df_row(dev, size, boot_used, size - boot_used, mount))
    return "\n".join(rows) + "\n"


def server_mount(ident, root_dev="/dev/sda1", run_lock=True, boot=None, root_type="ext4",
                 root_options="rw,relatime,errors=remount-ro"):
    run = ident.hardware.mem_kb // 10
    text = (f"{root_dev} on / type {root_type} ({root_options})\n"
            "proc on /proc type proc (rw,nosuid,nodev,noexec,relatime)\n"
            "sysfs on /sys type sysfs (rw,nosuid,nodev,noexec,relatime)\n"
            f"tmpfs on /run type tmpfs (rw,nosuid,nodev,noexec,relatime,size={run}k,mode=755)\n")
    if run_lock:
        text += "tmpfs on /run/lock type tmpfs (rw,nosuid,nodev,noexec,relatime,size=5120k)\n"
    if boot:
        text += (f"{boot[0]} on {boot[2]} type vfat (rw,relatime,fmask=0022,dmask=0022,codepage=437,"
                 "iocharset=ascii,shortname=mixed,errors=remount-ro)\n")
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


def ps_start(ts, now):
    """START as procps prints it (pr_stime, in the honeypot's UTC): HH:MM for a
    process of today, MonDD for one of this year, else its year."""
    started, today = time.gmtime(ts), time.gmtime(now)
    if started.tm_year != today.tm_year:
        return gm(ts, "%Y")
    if started.tm_yday != today.tm_yday:
        return gm(ts, "%b%d")
    return gm(ts, "%H:%M")


def assign_processes(ident, entries):
    """PID 1 first, increasing PIDs drawn from pid_seed. Everything started at
    boot; a login shell (on a tty) at some time between boot and now. %MEM is
    RSS over MemTotal, as ps computes it."""
    rng = random.Random(ident.pid_seed)
    boot = ps_start(ident.booted_at, ident.now)
    pid = 0
    for entry in entries:
        if entry["RSS"]:
            entry["MEM"] = round(entry["RSS"] * 100 / ident.hardware.mem_kb, 1)
        if is_login_shell(entry):
            pid += rng.randint(200, 20000)
            entry["START"] = ps_start(rng.randint(ident.booted_at, ident.now), ident.now)
        else:
            pid = 1 if pid == 0 else pid + (rng.randint(150, 450) if pid == 1 else rng.randint(8, 220))
            entry["START"] = boot
        entry["PID"] = pid
    return entries


# --- the parts every persona has -------------------------------------------------

def base_files(persona, ident, passwd, group, shadow, home=None):
    serial = pi_serial(ident) if persona.hardware_platform == "aarch64" else "0000000000000000"
    files = {
        "etc/passwd": passwd,
        "etc/group": group,
        "etc/shadow": shadow,
        "etc/hostname": f"{ident.hostname}\n",
        "etc/hosts": hosts(ident.hostname),
        "proc/version": proc_version(ident.release),
        "proc/mounts": PROC_MOUNTS,
        "proc/cpuinfo": cpuinfo(persona, ident.hardware, serial, ident.release.kernel_version),
        "proc/meminfo": meminfo(ident),
    }
    if persona.kind == "server" and persona.user != "root":
        files.update(home_files(persona.user) if home is None else home)
    else:
        files[f"{home_dir(persona)}/.profile"] = "export PATH=/bin:/sbin:/usr/bin:/usr/sbin\n"
    return files


def base_txtcmds(persona, ident, df, mount, lscpu_text, top, cmdline=None):
    """cmdline: the kernel command line of dmesg (servers build it from their root disk)."""
    kernel = dmesg(ident.release, persona.hardware_platform) if cmdline is None else \
        dmesg(ident.release, persona.hardware_platform, cmdline)
    return {
        "bin/df": df,
        "bin/dmesg": kernel,
        "bin/mount": mount,
        "bin/ulimit": ULIMIT,
        "usr/bin/lscpu": lscpu_text,
        "usr/bin/nproc": nproc(ident.hardware),
        "usr/bin/top": top,
    }
