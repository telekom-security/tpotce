"""What of Cowrie's file system tree a persona's system has.

Cowrie's fs.pickle is a Debian 12 x86_64 container: dpkg and apt, x86_64
libraries, Python 3.11, OpenSSH, a few systemd units, runit. A persona keeps
only what its own system has (foreign_paths, the multiarch and Python renames
of render.apply_to_tree) and the versions its interpreters print (tools,
shell_keys for the patched perl.py / python.py / git.py of Cowrie)."""
import gzip
import re
import time
from pathlib import Path

from .common import deb_arch

# the Docker container Cowrie's tree was taken from (runit started its sshd): on no persona
CONTAINER_PATHS = (".dockerenv", "etc/runit", "etc/sv", "proc/docker", "run/runit", "usr/lib/runit-helper",
                   "var/log/runit")
# Debian's package system: on every persona without dpkg (glob patterns per directory)
DEBIAN_PATHS = (
    "etc/apt", "etc/debconf.conf", "etc/debian_version", "etc/dpkg", "etc/ucf.conf",
    "usr/lib/apt", "usr/lib/dpkg", "usr/libexec/dpkg", "usr/share/debconf", "usr/share/dpkg",
    "var/cache/apt", "var/cache/debconf", "var/lib/apt", "var/lib/dpkg", "var/lib/ucf",
    "var/log/alternatives.log", "var/log/apt", "var/log/dpkg.log",
    "usr/bin/apt-*", "usr/bin/debconf*", "usr/bin/dpkg*", "usr/sbin/dpkg-*",
)
# Debian's own tools, settings and init scripts besides the package system (adduser, debianutils,
#   init-system-helpers, sysv-rc, the ufw profile and the sftp-server link of openssh-server, the
#   loader link of libc-bin, the units of apt and dpkg, Debian's ssh unit names): on every system
#   without dpkg; Red Hat writes its own (system.files, system.links)
DEBIAN_SYSTEM_PATHS = (
    "etc/adduser.conf", "etc/deluser.conf", "etc/ufw", "etc/update-motd.d", "etc/rc?.d", "etc/default/ssh",
    "etc/init.d/hwclock.sh", "etc/init.d/procps", "etc/init.d/ssh", "etc/cron.daily/apt-compat",
    "etc/cron.daily/dpkg",
    "usr/sbin/add-shell", "usr/sbin/remove-shell", "usr/sbin/update-shells", "usr/sbin/installkernel",
    "usr/sbin/invoke-rc.d", "usr/sbin/update-rc.d", "usr/sbin/policy-rc.d", "usr/sbin/start-stop-daemon",
    "usr/sbin/update-passwd", "usr/sbin/pam-auth-update", "usr/sbin/pam_getenv", "usr/sbin/shadowconfig",
    "usr/bin/ld.so", "usr/bin/savelog", "usr/bin/ischroot", "usr/bin/tempfile",
    "usr/lib/openssh", "usr/lib/sftp-server",
    "usr/lib/systemd/system/apt-daily*", "usr/lib/systemd/system/dpkg-db-backup*",
    "usr/lib/systemd/system/ssh.s*", "usr/lib/systemd/system/rescue-ssh.target",
    "etc/systemd/system/timers.target.wants/apt-daily*", "etc/systemd/system/timers.target.wants/dpkg-db-backup*",
    "etc/systemd/system/multi-user.target.wants/ssh.service", "etc/systemd/system/sshd.service",
    "etc/logrotate.d/apt", "usr/share/bash-completion/completions/apt", "usr/share/base-files", "usr/share/bug",
    "usr/share/lintian", "etc/rmt", "usr/sbin/rmt", "usr/sbin/rmt-tar", "usr/sbin/tarcat",
    "etc/alternatives/rmt", "etc/alternatives/rmt.8.gz", "usr/share/man/man8/rmt*.8.gz",
)
# ... and every name or link target with one of these words (man pages, translations, alternatives, keyrings,
#   changelog.Debian.gz, state of deb-systemd-helper): Debian itself, dpkg, apt, the rmt of Debian's tar and
#   the packages adduser, debianutils (which.debianutils) and init-system-helpers
DEBIAN_WORDS = ("debian", "Debian", "dpkg", "apt.mo", "deb-systemd", "adduser", "deluser", "addgroup",
                "delgroup", "rmt-tar")
# x86_64 only: the dynamic loader and the multiarch directory of Debian
X86_PATHS = ("lib64", "usr/lib64", "usr/bin/x86_64", "usr/bin/ld.so")
X86_WORDS = ("x86_64", "x86-64")
MULTIARCH = "x86_64-linux-gnu"
TRIPLETS = {"x86_64": "x86_64-linux-gnu", "aarch64": "aarch64-linux-gnu", "mips": "mipsel-linux-gnu",
            "armv7l": "arm-linux-gnueabihf"}
# systemd: servers, Synology DSM 7 and the Debian 9 based EdgeOS 2 boot with it
SYSTEMD_PATHS = ("etc/systemd", "usr/lib/systemd", "var/lib/systemd")
SYSTEMD_DEVICES = frozenset({"synology", "ubiquiti"})
# OpenSSH: not on a dropbear system
OPENSSH_PATHS = ("etc/ssh", "etc/default/ssh", "etc/init.d/ssh", "usr/lib/openssh", "usr/lib/sftp-server",
                 "usr/sbin/sshd", "usr/share/openssh", "usr/bin/ssh-*", "usr/bin/scp", "usr/bin/sftp",
                 "etc/systemd/system/sshd.service", "etc/systemd/system/multi-user.target.wants/ssh.service",
                 "etc/ufw/applications.d/openssh-server", "etc/rc?.d/S01ssh")
# Python 3.11 and Perl of Cowrie's tree: renamed to the persona's Python, removed where none is
PYTHON_PATHS = ("etc/python3", "etc/python3.11", "usr/lib/python3", "usr/lib/python3.11", "usr/share/python3",
                "usr/bin/py3*", "usr/bin/pydoc3*", "usr/bin/pdb3*", "usr/bin/pygettext3*", "usr/bin/python3*",
                "var/lib/python")
PERL_PATHS = ("usr/share/perl5", "usr/bin/perl*", "usr/lib/x86_64-linux-gnu/perl*")
# /proc/modules of Cowrie's tree lists the modules of a Debian 6.x container host
PROC_MODULES = "proc/modules"
# the standard streams of Cowrie's tree are the pipes of the container's Python (proc/969/fd/pipe:[7685]);
#   a login shell has them on its terminal
STREAM_LINKS = (("dev/stdin", "proc/self/fd/0"), ("dev/stdout", "proc/self/fd/1"), ("dev/stderr", "proc/self/fd/2"))
TERMINAL = "dev/pts/0"

# interpreters of the server releases (codename, or the family for Red Hat): Perl and its
# copyright year of `perl -v`, Python 3 (2.7 on CentOS 7, which has no python3); unverified: the
# copyright years of 5.38.2 / 5.40.1 and the Python 3.9 of each RHEL 9 minor
PERL = {"noble": ("5.38.2", "2023"), "jammy": ("5.34.0", "2021"), "trixie": ("5.40.1", "2025"),
        "bookworm": ("5.36.0", "2022"), "rhel": ("5.32.1", "2021"), "centos": ("5.16.3", "2012"),
        "stretch": ("5.24.1", "2017")}
PYTHON = {"noble": "3.12.3", "jammy": "3.10.12", "trixie": "3.13.5", "bookworm": "3.11.2",
          "rhel": "3.9.21", "centos": "2.7.5"}
RHEL_PYTHON = {"9.4": "3.9.18", "9.5": "3.9.19", "9.6": "3.9.21"}
# sudo of the releases without a sudo package in the catalog; the sudoers grammar of `sudo -V`
#   (unverified: 46 for 1.8.23, 48 for 1.9.0 to 1.9.11, 50 from 1.9.12)
SUDO = {"noble": "1.9.15p5", "jammy": "1.9.9", "rhel": "1.9.5p2", "centos": "1.8.23"}
# CentOS 7 yum loads fastestmirror only
YUM_PLUGINS = "fastestmirror"
SERVICE_STATUS = "service-status.txt"
# the runtime files of the patched systemctl, sudo -l and apt (written per start)
SYSTEMD_UNITS = "systemd-units.json"
SUDO_LIST = "sudo-list.json"
APT_PACKAGES = "apt-packages.json"
# the names of the release's packages that apt, yum 3 and opkg know (package_names, one per line)
PACKAGE_NAMES = "package-names.txt"
# the sudoers of each family: Defaults (name, operator, value) as the file writes them, the rule of
#   the admin group; sudo -l prints both (unverified: the files are the defaults of the packages, shortened)
_DEBIAN_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
_EL_KEEP = (("env_keep", "=", "COLORS DISPLAY HOSTNAME HISTSIZE KDEDIR LS_COLORS"),
            ("env_keep", "+=", "MAIL QTDIR USERNAME LANG LC_ADDRESS LC_CTYPE"),
            ("env_keep", "+=", "LC_COLLATE LC_IDENTIFICATION LC_MEASUREMENT LC_MESSAGES"),
            ("env_keep", "+=", "LC_MONETARY LC_NAME LC_NUMERIC LC_PAPER LC_TELEPHONE"),
            ("env_keep", "+=", "LC_TIME LC_ALL LANGUAGE LINGUAS _XKB_CHARSET XAUTHORITY"))
_EL_FLAGS = tuple((flag, None, None) for flag in ("!visiblepw", "always_set_home", "match_group_by_gid",
                                                   "always_query_group_plugin", "env_reset"))
_DEBIAN_DEFAULTS = (("env_reset", None, None), ("mail_badpass", None, None), ("secure_path", "=", _DEBIAN_PATH),
                    ("use_pty", None, None))
SUDOERS = {
    "ubuntu": (_DEBIAN_DEFAULTS[:2] + (("secure_path", "=", _DEBIAN_PATH + ":/snap/bin"), ("use_pty", None, None))),
    "debian": _DEBIAN_DEFAULTS,
    "raspios": _DEBIAN_DEFAULTS,
    "rhel": _EL_FLAGS + _EL_KEEP + (("secure_path", "=", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin"),),
    "centos": _EL_FLAGS + _EL_KEEP + (("secure_path", "=", "/sbin:/bin:/usr/sbin:/usr/bin"),),
}
# cloud-init of each release and the day it came out (it writes the rule of the image's user on the
#   first boot; unverified: versions and days)
CLOUD_INIT = {
    "noble": (("24.1.3-0ubuntu3", "2024-04-23"), ("24.1.3-0ubuntu3.3", "2024-06-26"),
              ("24.3.1-0ubuntu0~24.04.1", "2024-09-30"), ("24.4-0ubuntu1~24.04.1", "2024-12-10"),
              ("25.1.2-0ubuntu0~24.04.1", "2025-05-13"), ("25.1.4-0ubuntu0~24.04.1", "2025-08-05")),
    "jammy": (("22.1-14-g2e17a0d6-0ubuntu1~22.04.5", "2022-04-19"), ("23.1.2-0ubuntu0~22.04.1", "2023-05-02"),
              ("24.1.3-0ubuntu1~22.04.1", "2024-04-30"), ("24.4-0ubuntu1~22.04.1", "2024-12-10"),
              ("25.1.4-0ubuntu0~22.04.1", "2025-08-05")),
    "trixie": (("25.1.1-1", "2025-03-28"), ("25.1.2-1", "2025-06-02")),
    "bookworm": (("22.4.2-1", "2023-01-20"),),
    "rhel": (("22.1-5.el9", "2022-05-10"), ("23.1.1-11.el9", "2023-11-07"), ("23.4-7.el9", "2024-04-30"),
             ("23.4-19.el9", "2024-11-12"), ("24.4-4.el9", "2025-05-13")),
    "centos": (("19.4-7.el7.centos.2", "2020-11-10"), ("19.4-7.el7.centos.6", "2021-11-01")),
}


def cloud_init_version(persona, ident):
    """(version, release time) of the newest cloud-init out when the system was installed."""
    import calendar
    code = persona.family if is_el(persona) else ident.release.extra["codename"]
    found = [(version, calendar.timegm(time.strptime(day, "%Y-%m-%d"))) for version, day in CLOUD_INIT[code]]
    usable = [entry for entry in found if entry[1] <= ident.installed_at]
    return usable[-1] if usable else found[0]


_SUDOERS_HEAD = """#
# This file MUST be edited with the 'visudo' command as root.
#
# Please consider adding local content in /etc/sudoers.d/ instead of
# directly modifying this file.
#
# See the man page for details on how to write a sudoers file.
#
"""
_SUDOERS_D_README = """#
# The default /etc/sudoers file created on installation of the
# sudo  package now includes the directive:
#
# \t@includedir /etc/sudoers.d
#
# This will cause sudo to read and parse any files in the /etc/sudoers.d
# directory that do not end in '~' or contain a '.' character.
#
# Note that there must be at least one file in the sudoers.d directory (this
# one will do).
#
# Note also, that because sudoers contents can vary widely, no attempt is
# made to add this directive to existing sudoers files on upgrade.  Feel free
# to add the above directive to the end of your /etc/sudoers file to enable
# this functionality for existing installations if you wish! Sudo
# versions older than the one in Debian 11 (bullseye) require the
# directive will only support the old syntax #includedir, and the current
# sudo will happily accept both @includedir and #includedir
#
# Finally, please note that using the visudo command is the recommended way
# to update sudoers content, since it protects against many failure modes.
# See the man page for visudo and sudoers for more information.
#
"""


def _listed_default(name, operator, value):
    """A Default as sudo -l prints it: secure_path=/usr/local/sbin\\:..., env_keep+="..."."""
    if operator is None:
        return name
    if name == "secure_path":
        return f"secure_path={value.replace(':', chr(92) + ':')}"
    return f'{name}{"+=" if operator == "+=" else "="}"{value}"'


def _file_default(name, operator, value, el):
    if operator is None:
        return f"Defaults{'    ' if el else chr(9)}{name}"
    if el:
        if name == "secure_path":
            return f"Defaults    secure_path = {value}"
        return f'Defaults    {name} {operator.ljust(2)} "{value}"'
    return f'Defaults\t{name}="{value}"'


def sudoers_files(persona, ident):
    """/etc/sudoers and /etc/sudoers.d of a server: the package's file, the rule cloud-init wrote for the
    image's user (pi-gen's on Raspberry Pi OS), Debian's README."""
    el = is_el(persona)
    defaults = "\n".join(_file_default(*entry, el) for entry in SUDOERS[persona.family])
    if el:
        main = ("## Sudoers allows particular users to run various commands as\n"
                "## the root user, without needing the root password.\n##\n"
                f"{defaults}\n\n## Allow root to run any commands anywhere\nroot\tALL=(ALL) \tALL\n\n"
                "## Allows people in group wheel to run all commands\n%wheel\tALL=(ALL)\tALL\n\n"
                "## Read drop-in files from /etc/sudoers.d (the # here does not mean a comment)\n"
                "#includedir /etc/sudoers.d\n")
    else:
        admin = ("# Members of the admin group may gain root privileges\n%admin ALL=(ALL) ALL\n\n"
                 if persona.family == "ubuntu" else "")
        main = (f"{_SUDOERS_HEAD}{defaults}\n\n# Host alias specification\n\n# User alias specification\n\n"
                "# Cmnd alias specification\n\n# User privilege specification\nroot\tALL=(ALL:ALL) ALL\n\n"
                f"{admin}# Allow members of group sudo to execute any command\n%sudo\tALL=(ALL:ALL) ALL\n\n"
                "# See sudoers(5) for more information on \"@include\" directives:\n\n@includedir /etc/sudoers.d\n")
    files = {"etc/sudoers": main}
    if not el:
        files["etc/sudoers.d/README"] = _SUDOERS_D_README
    if persona.family == "raspios":
        files["etc/sudoers.d/010_pi-nopasswd"] = f"{persona.user} ALL=(ALL) NOPASSWD: ALL\n"
    else:
        when = time.strftime("%a, %d %b %Y %H:%M:%S +0000", time.gmtime(ident.installed_at))
        version = cloud_init_version(persona, ident)[0]
        files["etc/sudoers.d/90-cloud-init-users"] = (f"# Created by cloud-init v. {version} on {when}\n\n"
                                                     f"# User rules for {persona.user}\n"
                                                     f"{persona.user} ALL=(ALL) NOPASSWD:ALL\n")
    return files


# what apt knows on the Debian releases besides the catalog's packages (unverified: the versions are those
#   of the release pockets around 2025/2026 as remembered, not read from the archive):
#   installed - preinstalled on the image (its programs on PATH, man-db of every apt install)
#   available - the packages bots install most, at the version the release ships
APT_INSTALLED = {
    "noble": {"iproute2": "6.1.0-1ubuntu6", "bind9-dnsutils": "1:9.18.28-0ubuntu0.24.04.1", "ethtool": "1:6.7-1build1", "iptables": "1.8.10-3ubuntu2", "iputils-ping": "3:20240117-1build1", "bash": "5.2.21-2ubuntu4", "coreutils": "9.4-3ubuntu6", "gzip": "1.12-1ubuntu3",
              "man-db": "2.12.0-4build2", "netcat-openbsd": "1.226-1ubuntu2", "perl": "5.38.2-3.2build2",
              "python3": "3.12.3-0ubuntu2", "sudo": "1.9.15p5-3ubuntu5", "tar": "1.35+dfsg-3build1",
              "wget": "1.21.4-1ubuntu4.1"},
    "jammy": {"iproute2": "5.15.0-1ubuntu2", "bind9-dnsutils": "1:9.18.30-0ubuntu0.22.04.2", "ethtool": "1:5.16-1ubuntu0.1", "iptables": "1.8.7-1ubuntu5.2", "iputils-ping": "3:20211215-1", "bash": "5.1-6ubuntu1.1", "coreutils": "8.32-4.1ubuntu1.2", "gzip": "1.10-4ubuntu4.1",
              "man-db": "2.10.2-1", "netcat-openbsd": "1.218-4ubuntu1", "perl": "5.34.0-3ubuntu1.3",
              "python3": "3.10.6-1~22.04.1", "sudo": "1.9.9-1ubuntu2.4", "tar": "1.34+dfsg-1ubuntu0.1.22.04.2",
              "wget": "1.21.2-2ubuntu1.1"},
    "trixie": {"iproute2": "6.15.0-1", "iputils-ping": "3:20250605-1", "bash": "5.2.37-2+b5", "coreutils": "9.7-3", "gzip": "1.13-1", "man-db": "2.13.1-1",
               "netcat-openbsd": "1.229-1", "perl": "5.40.1-6", "python3": "3.13.5-1", "tar": "1.35+dfsg-3.1",
               "wget": "1.25.0-2"},
    "bookworm": {"iproute2": "6.1.0-3", "iputils-ping": "3:20221126-1+deb12u1", "bash": "5.2.15-2+b7", "coreutils": "9.1-1", "gzip": "1.12-1", "man-db": "2.11.2-2",
                 "netcat-openbsd": "1.219-1", "perl": "5.36.0-7+deb12u2", "python3": "3.11.2-1+b1",
                 "tar": "1.34+dfsg-1.2+deb12u1", "wget": "1.21.3-1+deb12u1"},
}
APT_AVAILABLE = {
    "noble": {"busybox": "1:1.36.1-6ubuntu3.1", "php-cli": "2:8.3+93ubuntu2", "tftp-hpa": "5.2+20150808-1.4build2", "gcc": "4:13.2.0-7ubuntu1", "hydra": "9.5-1build3",
              "make": "4.3-4.1build2", "masscan": "2:1.3.2+ds1-1", "nmap": "7.94+git20230807.3be01efb1+dfsg-3build2",
              "screen": "4.9.1-1build1", "socat": "1.8.0.0-4build3", "tmux": "3.4-1ubuntu0.1",
              "tor": "0.4.8.10-1build2", "unzip": "6.0-28ubuntu4.1", "zip": "3.0-13build1"},
    "jammy": {"busybox": "1:1.30.1-7ubuntu3.1", "php-cli": "2:8.1+92ubuntu1", "tftp-hpa": "5.2+20150808-1.2build2", "gcc": "4:11.2.0-1ubuntu1", "hydra": "9.2-1ubuntu1",
              "make": "4.3-4.1build1", "masscan": "2:1.3.2+ds1-1", "nmap": "7.91+dfsg1+really7.80+dfsg1-2ubuntu0.1",
              "screen": "4.9.0-1", "socat": "1.7.4.1-3ubuntu4", "tmux": "3.2a-4ubuntu0.2", "tor": "0.4.6.10-1",
              "unzip": "6.0-26ubuntu3.2", "zip": "3.0-12build2"},
    "trixie": {"bind9-dnsutils": "1:9.20.11-4", "busybox": "1:1.37.0-6", "ethtool": "1:6.14.2-1", "iptables": "1.8.11-2", "php-cli": "2:8.4+96", "tftp-hpa": "5.2+20150808-1.6", "gcc": "4:14.2.0-1", "git": "1:2.47.3-0+deb13u1", "hydra": "9.5-3",
               "make": "4.4.1-2", "masscan": "2:1.3.2+ds1-2", "net-tools": "2.10-1.3", "nmap": "7.95+dfsg-3",
               "screen": "4.9.1-3", "socat": "1.8.0.3-1", "tmux": "3.5a-3", "tor": "0.4.8.16-1", "unzip": "6.0-29",
               "zip": "3.0-15"},
    "bookworm": {"bind9-dnsutils": "1:9.18.33-1~deb12u2", "busybox": "1:1.35.0-4+b3", "ethtool": "1:6.1-1", "iptables": "1.8.9-2", "php-cli": "2:8.2+93", "tftp-hpa": "5.2+20150808-1.4", "gcc": "4:12.2.0-3", "git": "1:2.39.5-0+deb12u2", "hydra": "9.4-1",
                 "make": "4.3-4.1", "masscan": "2:1.3.2+ds1-1", "net-tools": "2.10-0.1", "nmap": "7.93+dfsg1-1",
                 "screen": "4.9.0-4", "socat": "1.7.4.4-2", "tmux": "3.3a-3", "tor": "0.4.7.16-1", "unzip": "6.0-28",
                 "zip": "3.0-13"},
}
# the tools Cowrie answers by name (its Python commands) and the programs of their package (Debian's name,
#   the path in the tree): a tool runs exactly where its package is installed, and is a program then;
#   (iproute2: the patched ip and ss)
TOOL_PROGRAMS = {
    "bind9-dnsutils": {"dig": "usr/bin/dig"},
    "busybox": {"ftpget": "usr/bin/ftpget"},
    "ethtool": {"ethtool": "usr/sbin/ethtool"},
    "iproute2": {"ip": "usr/bin/ip", "ss": "usr/bin/ss"},
    "iptables": {"iptables": "usr/sbin/iptables"},
    "iputils-ping": {"ping": "usr/bin/ping"},
    "net-tools": {"ifconfig": "usr/sbin/ifconfig", "netstat": "usr/bin/netstat"},
    "netcat-openbsd": {"nc": "usr/bin/nc", "netcat": "usr/bin/netcat"},
    "php-cli": {"php": "usr/bin/php"},
    "tftp-hpa": {"tftp": "usr/bin/tftp"},
    "unzip": {"unzip": "usr/bin/unzip"},
}
COWRIE_TOOL_NAMES = frozenset({"dig", "ftpget", "ethtool", "iptables", "ping", "ifconfig", "netstat", "nc", "netcat",
                               "php", "tftp", "unzip", "ip", "ss"})
# the tools of the Red Hat images (rpm: bind-utils, net-tools, iptables, ethtool, iputils; unverified)
EL_TOOLS = {"rhel": frozenset({"ethtool", "iproute2", "iputils-ping"}),
            "centos": frozenset({"ethtool", "iproute2", "iptables", "iputils-ping", "net-tools"})}
# where Red Hat's packages put the programs of a tool (iproute: /usr/sbin/ip, /usr/sbin/ss)
EL_TOOL_PROGRAMS = {"iproute2": {"ip": "usr/sbin/ip", "ss": "usr/sbin/ss"}}


def tool_paths(persona, tool):
    """{name: path} of the programs of a tool on the persona's system."""
    if is_el(persona) and tool in EL_TOOL_PROGRAMS:
        return EL_TOOL_PROGRAMS[tool]
    return TOOL_PROGRAMS[tool]
# nc of netcat-openbsd: the program and the names of the alternative
NETCAT = "usr/bin/nc.openbsd"


def installed_tools(persona, ident):
    """The packages of TOOL_PROGRAMS installed on a server (apt_packages on Debian, EL_TOOLS)."""
    if persona.kind != "server":
        return set()
    if is_el(persona):
        tools = set(EL_TOOLS[persona.family])
        return tools | ({"net-tools"} if "net-tools" in ident.release.packages else set())
    return {tool for tool in TOOL_PROGRAMS if tool in apt_packages(persona, ident)["installed"]}


def tool_programs(persona, ident):
    """(programs, links) of the installed tools."""
    programs, found = [], []
    for tool in sorted(installed_tools(persona, ident)):
        for name, rel in tool_paths(persona, tool).items():
            if tool == "netcat-openbsd":
                # dpkg's alternatives: /usr/bin/nc -> /etc/alternatives/nc -> /bin/nc.openbsd
                found += [(rel, f"/etc/alternatives/{name}"), (f"etc/alternatives/{name}", "/bin/nc.openbsd")]
            else:
                programs.append(rel)
        if tool == "netcat-openbsd":
            programs.append(NETCAT)
    return tuple(programs), tuple(found)


def skipped_tools(persona, ident):
    """The names of Cowrie's commands whose package the server has not installed."""
    if persona.kind != "server":
        return ()
    installed = installed_tools(persona, ident)
    return tuple(name for tool, names in TOOL_PROGRAMS.items() if tool not in installed
                 for name in names if name in COWRIE_TOOL_NAMES)


# the program on PATH of a package (a program of the tree is from an installed package)
PACKAGE_PROGRAMS = {"bash": "bash", "curl": "curl", "git": "git", "gzip": "gzip", "perl": "perl",
                    "python3": "python3", "tar": "tar", "wget": "wget", "openssh-client": "ssh",
                    "openssh-server": "sshd", "sudo": "sudo", "vim": "vim", "nmap": "nmap", "masscan": "masscan",
                    "socat": "socat", "gcc": "gcc", "make": "make", "tor": "tor", "hydra": "hydra", "tmux": "tmux",
                    "screen": "screen", "zip": "zip"}

# what dnf and rpm know on RHEL 9 (unverified: versions as remembered of the minor releases and their
#   rebuilds, not read from the repositories; summaries those of the spec files, sizes typical):
#   installed - the packages of the cloud image besides the persona's (catalog, kernel, interpreters)
#   available - the packages bots install most that BaseOS / AppStream have (masscan, hydra, tor, screen and
#               busybox are not in RHEL 9: dnf finds no match for them)
# name: (arch, repository, summary, epoch:version-release for every minor or {minor: evr})
RHEL_MINORS = ("9.4", "9.5", "9.6")
RPM_INSTALLED = {
    "NetworkManager": ("x86_64", "baseos", "Network connection manager and user applications",
                       {"9.4": "1:1.46.0-26.el9_4", "9.5": "1:1.48.10-5.el9_5", "9.6": "1:1.52.0-3.el9_6"}),
    "acl": ("x86_64", "baseos", "Access control list utilities", "2.3.1-4.el9"),
    "alternatives": ("x86_64", "baseos", "A tool to maintain symbolic links determining default commands",
                     {"9.4": "1.24-1.el9", "9.5": "1.24-1.el9_5.1", "9.6": "1.24-2.el9"}),
    "audit": ("x86_64", "baseos", "User space tools for kernel auditing",
              {"9.4": "3.1.2-2.el9", "9.5": "3.1.5-1.el9", "9.6": "3.1.5-4.el9"}),
    "authselect": ("x86_64", "baseos", "Configures authentication and identity sources from supported profiles",
                   {"9.4": "1.2.6-2.el9", "9.5": "1.2.6-2.el9", "9.6": "1.2.6-3.el9"}),
    "basesystem": ("noarch", "baseos", "The skeleton package which defines a simple Red Hat Enterprise Linux "
                   "system", "11-13.el9"),
    "bash": ("x86_64", "baseos", "The GNU Bourne Again shell", "5.1.8-9.el9"),
    "iproute": ("x86_64", "baseos", "Advanced IP routing and network device configuration tools",
                {"9.4": "6.2.0-6.el9_4", "9.5": "6.2.0-6.el9_4", "9.6": "6.11.0-1.el9"}),
    "chrony": ("x86_64", "baseos", "An NTP client/server",
               {"9.4": "4.5-1.el9", "9.5": "4.5-3.el9", "9.6": "4.6.1-1.el9"}),
    "coreutils": ("x86_64", "baseos", "A set of basic GNU tools commonly used in shell scripts",
                  {"9.4": "8.32-35.el9", "9.5": "8.32-36.el9", "9.6": "8.32-39.el9"}),
    "cronie": ("x86_64", "baseos", "Cron daemon for executing programs at set times",
               {"9.4": "1.5.7-11.el9", "9.5": "1.5.7-12.el9", "9.6": "1.5.7-13.el9"}),
    "dbus-broker": ("x86_64", "baseos", "Linux D-Bus Message Broker", "28-7.el9"),
    "dnf-plugins-core": ("noarch", "baseos", "Core Plugins for DNF",
                         {"9.4": "4.3.0-13.el9", "9.5": "4.3.0-16.el9", "9.6": "4.3.0-20.el9"}),
    "ethtool": ("x86_64", "baseos", "Settings tool for Ethernet NICs",
                {"9.4": "2:6.2-1.el9", "9.5": "2:6.2-1.el9", "9.6": "2:6.11-1.el9"}),
    "glibc": ("x86_64", "baseos", "The GNU libc libraries",
              {"9.4": "2.34-100.el9_4.4", "9.5": "2.34-125.el9_5.3", "9.6": "2.34-168.el9_6.14"}),
    "gzip": ("x86_64", "baseos", "The GNU data compression program", "1.12-1.el9"),
    "iputils": ("x86_64", "baseos", "Network monitoring tools including ping",
                {"9.4": "20210202-9.el9", "9.5": "20210202-10.el9_5", "9.6": "20210202-11.el9_6"}),
    "kexec-tools": ("x86_64", "baseos", "The kexec/kdump userspace component",
                    {"9.4": "2.0.27-8.el9_4", "9.5": "2.0.28-1.el9", "9.6": "2.0.29-5.el9"}),
    "man-db": ("x86_64", "baseos", "Tools for searching and reading man pages", "2.9.3-7.el9"),
    "rsyslog": ("x86_64", "appstream", "Enhanced system logging and kernel message trapping daemon",
                {"9.4": "8.2310.0-4.el9", "9.5": "8.2310.0-4.el9", "9.6": "8.2412.0-1.el9"}),
    "subscription-manager": ("x86_64", "baseos", "Tools and libraries for subscription and repository management",
                             {"9.4": "1.29.40-1.el9", "9.5": "1.29.42-1.el9", "9.6": "1.29.45-1.el9_6"}),
    "tar": ("x86_64", "baseos", "GNU file archiving program",
            {"9.4": "2:1.34-6.el9_4.1", "9.5": "2:1.34-7.el9", "9.6": "2:1.34-7.el9"}),
    "wget": ("x86_64", "appstream", "A utility for retrieving files using the HTTP or FTP protocols",
             "1.21.1-8.el9_4"),
    "diffutils": ("x86_64", "baseos", "GNU collection of diff utilities", "3.7-12.el9"),
    "e2fsprogs": ("x86_64", "baseos", "Utilities for managing ext2, ext3, and ext4 file systems",
                  {"9.4": "1.46.5-5.el9", "9.5": "1.46.5-5.el9", "9.6": "1.46.5-7.el9"}),
    "findutils": ("x86_64", "baseos", "The GNU versions of find utilities (find and xargs)",
                  {"9.4": "1:4.8.0-6.el9", "9.5": "1:4.8.0-7.el9", "9.6": "1:4.8.0-7.el9"}),
    "gawk": ("x86_64", "baseos", "The GNU version of the AWK text processing utility", "5.1.0-6.el9"),
    "gnupg2": ("x86_64", "baseos", "Utility for secure communication and data storage", "2.3.3-4.el9"),
    "grep": ("x86_64", "baseos", "Pattern matching utilities", "3.6-5.el9"),
    "grubby": ("x86_64", "baseos", "Command line tool for updating bootloader configs",
               {"9.4": "8.40-61.el9", "9.5": "8.40-61.el9", "9.6": "8.40-64.el9"}),
    "hostname": ("x86_64", "baseos", "Utility to set/show the host name or domain name", "3.23-6.el9"),
    "initscripts-service": ("noarch", "baseos", "Support for service command",
                            {"9.4": "10.11.6-1.el9", "9.5": "10.11.8-4.el9", "9.6": "10.11.8-4.el9"}),
    "kmod": ("x86_64", "baseos", "Linux kernel module management utilities",
             {"9.4": "28-9.el9", "9.5": "28-10.el9", "9.6": "28-10.el9"}),
    "ncurses": ("x86_64", "baseos", "Ncurses support utilities", "6.2-10.20210508.el9"),
    "pam": ("x86_64", "baseos", "An extensible library which provides authentication for applications",
            {"9.4": "1.5.1-19.el9", "9.5": "1.5.1-22.el9_5", "9.6": "1.5.1-23.el9"}),
    "passwd": ("x86_64", "baseos", "An utility for setting or changing passwords using PAM", "0.80-12.el9"),
    "pciutils": ("x86_64", "baseos", "PCI bus related utilities", "3.7.0-5.el9"),
    "procps-ng": ("x86_64", "baseos", "System and process monitoring utilities",
                  {"9.4": "3.3.17-14.el9", "9.5": "3.3.17-14.el9", "9.6": "3.3.17-14.el9"}),
    "sed": ("x86_64", "baseos", "A GNU stream text editor", "4.8-9.el9"),
    "setup": ("noarch", "baseos", "A set of system configuration and setup files", "2.13.7-10.el9"),
    "shadow-utils": ("x86_64", "baseos", "Utilities for managing accounts and shadow password files",
                     {"9.4": "2:4.9-8.el9", "9.5": "2:4.9-10.el9_5", "9.6": "2:4.9-12.el9"}),
    "util-linux": ("x86_64", "baseos", "A collection of basic system utilities",
                   {"9.4": "2.37.4-18.el9", "9.5": "2.37.4-20.el9", "9.6": "2.37.4-21.el9"}),
    "which": ("x86_64", "baseos", "Displays where a particular program in your path is located",
              {"9.4": "2.21-29.el9", "9.5": "2.21-29.el9", "9.6": "2.21-30.el9"}),
}
# the packages of the persona's own versions: catalog, kernel, interpreters, sudo, OpenSSL, dnf and rpm
RPM_PERSONA = {
    "cloud-init": ("noarch", "appstream", "Cloud instance init scripts"),
    "glibc-common": ("x86_64", "baseos", "Common binaries and locale data for glibc"),
    "redhat-release": ("x86_64", "baseos", "Red Hat Enterprise Linux release file"),
    "systemd-udev": ("x86_64", "baseos", "Rule-based device node and kernel event manager"),
    "vim-minimal": ("x86_64", "baseos", "A minimal version of the VIM editor"),
    "curl": ("x86_64", "baseos", "A utility for getting files from remote servers (FTP, HTTP, and others)"),
    "dnf": ("noarch", "baseos", "Package manager"),
    "kernel": ("x86_64", "baseos", "The Linux kernel, based on version 5.14.0, heavily modified with backports"),
    "kernel-core": ("x86_64", "baseos", "The Linux kernel"),
    "openssh": ("x86_64", "baseos", "An open source implementation of SSH protocol version 2"),
    "openssh-clients": ("x86_64", "baseos", "An open source SSH client applications"),
    "openssh-server": ("x86_64", "baseos", "An open source SSH server daemon"),
    "openssl": ("x86_64", "baseos", "Utilities from the general purpose cryptography library with TLS "
                "implementation"),
    "perl-interpreter": ("x86_64", "appstream", "Standalone executable Perl interpreter"),
    "python3": ("x86_64", "baseos", "Python 3.9 interpreter"),
    "rpm": ("x86_64", "baseos", "The RPM package management system"),
    "sudo": ("x86_64", "baseos", "Allows restricted root access for specified users"),
    "systemd": ("x86_64", "baseos", "System and Service Manager"),
    "vim-enhanced": ("x86_64", "appstream", "A version of the VIM editor which includes recent enhancements"),
    "yum": ("noarch", "baseos", "Package manager"),
}
RHEL_DNF = {"9.4": "4.14.0-9.el9", "9.5": "4.14.0-17.el9", "9.6": "4.14.0-25.el9"}
RHEL_RPM = {"9.4": "4.16.1.3-29.el9", "9.5": "4.16.1.3-34.el9", "9.6": "4.16.1.3-37.el9"}
RHEL_PYTHON_RPM = {"9.4": "3.9.18-3.el9_4.6", "9.5": "3.9.19-8.el9_5.1", "9.6": "3.9.21-2.el9"}
RHEL_OPENSSL = {"9.4": "1:3.0.7-27.el9", "9.5": "1:3.2.2-6.el9_5", "9.6": "1:3.2.2-6.el9_5.1"}
RHEL_PERL = "4:5.32.1-481.el9"
RHEL_RELEASE_RPM = {"9.4": "9.4-0.5.el9", "9.5": "9.5-0.6.el9", "9.6": "9.6-0.1.el9"}
RHEL_SUDO = "1.9.5p2-10.el9_3"
# name: (arch, repository, summary, evr, (download kB, installed kB), dependencies); an evr "=glibc" is
#   that of the installed package (glibc-devel goes with glibc, kernel-headers with the kernel)
RPM_AVAILABLE = {
    "bind-utils": ("x86_64", "appstream", "Utilities for querying DNS name servers",
                   {"9.4": "32:9.16.23-18.el9_4.6", "9.5": "32:9.16.23-24.el9_5.3", "9.6": "32:9.16.23-31.el9_6"},
                   (205, 668), ()),
    "cpp": ("x86_64", "appstream", "The C Preprocessor",
            {"9.4": "11.4.1-3.el9", "9.5": "11.5.0-2.el9", "9.6": "11.5.0-5.el9_5"}, (11_264, 30_720), ()),
    "gcc": ("x86_64", "appstream", "Various compilers (C, C++, Objective-C, ...)",
            {"9.4": "11.4.1-3.el9", "9.5": "11.5.0-2.el9", "9.6": "11.5.0-5.el9_5"}, (32_768, 90_112),
            ("cpp", "glibc-devel", "glibc-headers", "kernel-headers", "libmpc", "libxcrypt-devel")),
    "git": ("x86_64", "appstream", "Fast Version Control System",
            {"9.4": "2.43.5-1.el9_4", "9.5": "2.43.5-2.el9_5", "9.6": "2.47.1-2.el9_6"}, (51, 85),
            ("git-core", "git-core-doc", "perl-Error", "perl-Git", "perl-TermReadKey", "perl-lib")),
    "git-core": ("x86_64", "appstream", "Core package of git with minimal functionality",
                 {"9.4": "2.43.5-1.el9_4", "9.5": "2.43.5-2.el9_5", "9.6": "2.47.1-2.el9_6"}, (4_506, 22_528), ()),
    "git-core-doc": ("noarch", "appstream", "Documentation files for git-core",
                     {"9.4": "2.43.5-1.el9_4", "9.5": "2.43.5-2.el9_5", "9.6": "2.47.1-2.el9_6"}, (2_867, 17_408),
                     ()),
    "glibc-devel": ("x86_64", "appstream", "Object files for development using standard C libraries.", "=glibc",
                    (38, 30), ()),
    "glibc-headers": ("x86_64", "appstream", "Header files for development using standard C libraries.", "=glibc",
                      (450, 2_252), ()),
    "iptables-nft": ("x86_64", "baseos", "nftables compatibility for iptables, arptables and ebtables",
                     {"9.4": "1.8.10-2.el9", "9.5": "1.8.10-5.el9_5", "9.6": "1.8.10-11.el9"}, (207, 676), ()),
    "kernel-headers": ("x86_64", "appstream", "Header files for the Linux kernel for use by glibc", "=kernel",
                       (2_560, 6_451), ()),
    "libmpc": ("x86_64", "appstream", "C library for multiple precision complex arithmetic", "1.2.1-4.el9",
               (66, 160), ()),
    "libxcrypt-devel": ("x86_64", "appstream", "Development files for libxcrypt", "4.4.18-3.el9", (32, 27), ()),
    "lsof": ("x86_64", "baseos", "A utility which lists open files on a Linux/UNIX system", "4.94.0-3.el9",
             (245, 960), ()),
    "make": ("x86_64", "baseos", "A GNU tool which simplifies the build process for users", "1:4.3-8.el9",
             (541, 1_536), ()),
    "net-tools": ("x86_64", "baseos", "Basic networking tools", "2.0-0.64.20160912git.el9", (314, 943), ()),
    "nmap": ("x86_64", "appstream", "Network exploration tool and security scanner",
             {"9.4": "3:7.92-1.el9", "9.5": "3:7.92-3.el9", "9.6": "3:7.92-3.el9"}, (5_735, 24_576), ()),
    "nmap-ncat": ("x86_64", "appstream", "Nmap's Netcat replacement",
                  {"9.4": "3:7.92-1.el9", "9.5": "3:7.92-3.el9", "9.6": "3:7.92-3.el9"}, (229, 485), ()),
    "perl-Error": ("noarch", "appstream", "Error/exception handling in an OO-ish way", "1:0.17029-7.el9", (46, 77),
                   ()),
    "perl-Git": ("noarch", "appstream", "Perl interface to Git",
                 {"9.4": "2.43.5-1.el9_4", "9.5": "2.43.5-2.el9_5", "9.6": "2.47.1-2.el9_6"}, (38, 64), ()),
    "perl-TermReadKey": ("x86_64", "appstream", "A perl module for simple terminal control", "2.38-11.el9",
                         (40, 64), ()),
    "perl-lib": ("x86_64", "appstream", "Manipulate @INC at compile time", "0.65-481.el9", (15, 8), ()),
    "php-cli": ("x86_64", "appstream", "Command-line interface for PHP", "8.0.30-1.el9_2", (3_174, 12_288),
                ("php-common",)),
    "php-common": ("x86_64", "appstream", "Common files for PHP", "8.0.30-1.el9_2", (667, 5_324), ()),
    "python3-pip": ("noarch", "appstream", "A tool for installing and managing Python3 packages", "21.3.1-1.el9",
                    (2_048, 8_806), ()),
    "socat": ("x86_64", "appstream", "Bidirectional data relay between two data channels ('netcat++')",
              {"9.4": "1.7.4.1-5.el9_4.2", "9.5": "1.7.4.1-6.el9", "9.6": "1.7.4.1-6.el9"}, (304, 1_126), ()),
    "tcpdump": ("x86_64", "appstream", "A network traffic monitoring tool", "14:4.99.0-9.el9", (545, 1_331), ()),
    "tmux": ("x86_64", "baseos", "A terminal multiplexer", "3.2a-5.el9", (476, 1_126), ()),
    "telnet": ("x86_64", "appstream", "The client program for the Telnet remote login protocol", "1:0.17-85.el9",
               (64, 117), ()),
    "tftp": ("x86_64", "appstream", "The client for the Trivial File Transfer Protocol (TFTP)", "5.2-38.el9",
             (40, 51), ()),
    "unzip": ("x86_64", "baseos", "A utility for unpacking zip files",
              {"9.4": "6.0-56.el9", "9.5": "6.0-57.el9", "9.6": "6.0-58.el9_5"}, (186, 399), ()),
    "zip": ("x86_64", "baseos", "A file compression and packaging utility compatible with PKZIP", "3.0-35.el9",
            (270, 739), ()),
}
# the programs of the available packages (a package the session installs gets them as Cowrie's apt does)
RPM_AVAILABLE_PROGRAMS = {
    "bind-utils": ("usr/bin/dig", "usr/bin/host", "usr/bin/nslookup"), "cpp": ("usr/bin/cpp",),
    "gcc": ("usr/bin/gcc", "usr/bin/cc"), "git-core": ("usr/bin/git",),
    "iptables-nft": ("usr/sbin/iptables",), "lsof": ("usr/bin/lsof",), "make": ("usr/bin/make",),
    "net-tools": ("usr/bin/netstat", "usr/sbin/ifconfig", "usr/sbin/route"), "nmap": ("usr/bin/nmap",),
    "nmap-ncat": ("usr/bin/ncat", "usr/bin/nc"), "php-cli": ("usr/bin/php",),
    "python3-pip": ("usr/bin/pip3", "usr/bin/pip"), "socat": ("usr/bin/socat",), "tcpdump": ("usr/sbin/tcpdump",),
    "telnet": ("usr/bin/telnet",), "tftp": ("usr/bin/tftp",), "tmux": ("usr/bin/tmux",),
    "unzip": ("usr/bin/unzip",), "zip": ("usr/bin/zip",),
}
# what the installed packages own in the tree (rpm -qf; unverified: the file lists as remembered): the
#   programs in /usr/bin and /usr/sbin, the files in /etc and /usr/lib
_COREUTILS = ("[ arch b2sum base32 base64 basename basenc cat chcon chgrp chmod chown cksum comm cp csplit cut date dd "
              "df dir dircolors dirname du echo env expand expr factor false fmt fold groups head hostid id install "
              "join link ln logname ls md5sum mkdir mkfifo mknod mktemp mv nice nl nohup nproc numfmt od paste "
              "pathchk pinky pr printenv printf ptx pwd readlink realpath rm rmdir runcon seq sha1sum sha224sum "
              "sha256sum sha384sum sha512sum shred shuf sleep sort split stat stdbuf stty sum sync tac tail tee test "
              "timeout touch tr true truncate tsort tty uname unexpand uniq unlink users vdir wc who whoami yes")
_UTIL_LINUX = ("addpart chfn choom chrt chsh delpart dmesg fallocate fincore findmnt flock getopt hardlink i386 ionice "
               "ipcmk ipcrm ipcs kill last lastb linux32 linux64 login logger lsblk lscpu lsfd lsipc lsirq lslocks "
               "lslogins lsmem lsns mcookie mesg more mount mountpoint namei nsenter partx prlimit renice "
               "resizepart rev script scriptlive scriptreplay setarch setpriv setsid setterm su taskset uclampset "
               "umount unshare utmpdump wall wdctl whereis x86_64")
_UTIL_LINUX_SBIN = ("agetty blkdiscard blkid blkzone blockdev chcpu chmem ctrlaltdel findfs fsck fsck.cramfs "
                    "fsck.minix fsfreeze fstrim hwclock isosize ldattach losetup mkfs mkfs.bfs mkfs.cramfs mkfs.minix "
                    "mkswap nologin pivot_root readprofile rtcwake runuser sulogin swaplabel swapoff swapon "
                    "switch_root wipefs zramctl")
_SHADOW_SBIN = ("adduser chgpasswd chpasswd groupadd groupdel groupmems groupmod grpck grpconv grpunconv newusers "
                "pwck pwconv pwunconv useradd userdel usermod vigr vipw")
_E2FS_SBIN = ("badblocks debugfs dumpe2fs e2freefrag e2fsck e2image e2label e2mmpstatus e2scrub e2scrub_all e2undo "
              "e4crypt e4defrag filefrag fsck.ext2 fsck.ext3 fsck.ext4 logsave mke2fs mkfs.ext2 mkfs.ext3 mkfs.ext4 "
              "mklost+found resize2fs tune2fs")
RPM_OWNED = {
    "audit": ("", "auditd"), "bash": ("bash bashbug sh ulimit", ""), "chrony": ("", "chronyd"),
    "cloud-init": ("cloud-init", ""), "coreutils": (_COREUTILS, "chroot"), "cronie": ("", "crond"),
    "curl": ("curl", ""), "dbus-broker": ("dbus-broker-launch", ""), "diffutils": ("cmp diff diff3 sdiff", ""),
    "dnf": ("dnf dnf-3", ""), "e2fsprogs": ("chattr lsattr", _E2FS_SBIN), "ethtool": ("", "ethtool"),
    "findutils": ("find xargs", ""), "gawk": ("awk gawk", ""),
    "glibc": ("", "iconvconfig ldconfig"),
    "glibc-common": ("getconf getent iconv ldd locale localedef pldd tzselect zdump", "zic"),
    "gnupg2": ("gpgv", ""), "grep": ("egrep fgrep grep", ""), "grubby": ("", "installkernel"),
    "gzip": ("gunzip gzexe gzip uncompress zcat zcmp zdiff zegrep zfgrep zforce zgrep zless zmore znew", ""),
    "hostname": ("dnsdomainname domainname hostname nisdomainname ypdomainname", ""),
    "initscripts-service": ("", "service"), "iproute": ("", "bridge ip ss tc"), "iputils": ("ping", ""), "kexec-tools": ("kdumpctl", ""),
    "kmod": ("kmod", ""), "ncurses": ("captoinfo clear infocmp infotocap reset tabs tic toe tput tset", ""),
    "NetworkManager": ("nm-online", "NetworkManager"),
    "openssh-clients": ("scp sftp ssh ssh-add ssh-agent ssh-copy-id ssh-keygen ssh-keyscan", ""),
    "openssh-server": ("", "sshd"),
    "pam": ("", "faillock mkhomedir_helper pam_namespace_helper pam_timestamp_check pwhistory_helper unix_chkpwd "
                "unix_update"),
    "passwd": ("passwd", ""), "pciutils": ("lspci", ""), "perl-interpreter": ("perl", ""),
    "procps-ng": ("free pgrep pidof pidwait pkill pmap ps pwdx skill slabtop snice tload top uptime vmstat w watch",
                  "sysctl"),
    "python3": ("pydoc3 pydoc3.9 python3 python3.9", ""), "rpm": ("rpm", ""), "rsyslog": ("", "rsyslogd"),
    "sed": ("sed", ""), "shadow-utils": ("chage expiry faillog gpasswd lastlog newgrp sg", _SHADOW_SBIN),
    "sudo": ("sudo", ""), "systemd": ("journalctl systemctl systemd-tmpfiles", ""), "systemd-udev": ("udevadm", ""),
    "tar": ("tar", ""), "util-linux": (_UTIL_LINUX, _UTIL_LINUX_SBIN),
    "vim-enhanced": ("rvim vim vim.basic vimdiff vimtutor", ""), "vim-minimal": ("ex rview vi view", ""),
    "wget": ("wget", ""), "which": ("which", ""), "yum": ("yum", ""),
}
RPM_OWNED_FILES = {
    "setup": ("etc/passwd", "etc/group", "etc/shadow", "etc/gshadow", "etc/hosts", "etc/services", "etc/profile",
              "etc/bashrc", "etc/shells", "etc/fstab", "etc/motd"),
    "redhat-release": ("etc/redhat-release", "etc/system-release", "etc/os-release", "usr/lib/os-release",
                       "etc/issue", "etc/issue.net"),
    "sudo": ("etc/sudoers",), "openssh-server": ("etc/ssh/sshd_config",), "systemd": ("usr/lib/systemd/systemd",),
}
# Debian's own programs of Cowrie's tree that Red Hat has not (awk and sh are gawk's and bash's there)
EL_DEBIAN_PROGRAMS = ("usr/bin/clear_console", "usr/bin/dash", "usr/bin/editor", "usr/bin/pager", "usr/bin/helpztags",
                      "usr/bin/lcf", "usr/bin/md5sum.textutils", "usr/bin/mawk", "usr/bin/nawk", "usr/bin/rename.ul",
                      "usr/bin/rgrep", "usr/bin/run-parts", "usr/bin/select-editor", "usr/bin/sensible-*",
                      "usr/bin/ucf", "usr/bin/ucfq", "usr/bin/ucfr", "usr/bin/update-alternatives",
                      "usr/bin/py3clean", "usr/bin/py3compile", "usr/bin/py3versions", "usr/bin/pygettext3*",
                      "usr/bin/pdb3*", "usr/bin/ssh-argv0", "usr/bin/perl5.*", "usr/sbin/cpgr", "usr/sbin/cppw",
                      "usr/sbin/fstab-decode", "usr/sbin/getty", "etc/alternatives/awk*",
                      "etc/alternatives/nawk*")
# RHEL 9: pidof is procps-ng's (no killall5 of sysvinit), no rbash and no slogin
RHEL_DEBIAN_PROGRAMS = ("usr/bin/rbash", "usr/sbin/killall5", "usr/bin/slogin")
EL_PROGRAM_LINKS = (("usr/bin/awk", "usr/bin/gawk"), ("usr/bin/sh", "usr/bin/bash"),
                    # PID 1 is systemd (Cowrie's tree has the dash of its container)
                    ("proc/1/exe", "usr/lib/systemd/systemd"))
# names dnf finds by what a package provides
RPM_PROVIDES = {"nc": "nmap-ncat", "ncat": "nmap-ncat", "vim": "vim-enhanced"}
# the packages of TOOL_PROGRAMS on Red Hat (no busybox)
RPM_TOOLS = {"bind9-dnsutils": "bind-utils", "ethtool": "ethtool", "iproute2": "iproute", "iptables": "iptables-nft",
             "iputils-ping": "iputils", "net-tools": "net-tools", "netcat-openbsd": "nmap-ncat", "php-cli": "php-cli",
             "tftp-hpa": "tftp", "unzip": "unzip"}
# the packages of the programs of PACKAGE_PROGRAMS on Red Hat (masscan, tor, hydra and screen are not in RHEL 9)
RPM_PROGRAMS = {"bash": "bash", "curl": "curl", "git": "git", "gzip": "gzip", "perl": "perl-interpreter",
                "python3": "python3", "tar": "tar", "wget": "wget", "ssh": "openssh-clients", "sshd": "openssh-server",
                "sudo": "sudo", "vim": "vim-enhanced", "nmap": "nmap", "socat": "socat", "gcc": "gcc", "make": "make",
                "tmux": "tmux", "zip": "zip"}
# the repositories of a registered RHEL 9 (subscription-manager): (part of the id, of the name, enabled); EUS:
#   a system that keeps its minor release after the next one came out (unverified: the days the minors came out)
RHEL_NEXT_MINOR = {"9.4": "2024-11-12", "9.5": "2025-05-20", "9.6": "2025-11-11"}
RHEL_REPOS = (("baseos", "BaseOS", "baseos/os", True), ("appstream", "AppStream", "appstream/os", True),
              ("baseos-debug", "BaseOS (Debug RPMs)", "baseos/debug", False),
              ("baseos-source", "BaseOS (Source RPMs)", "baseos/source/SRPMS", False),
              ("appstream-debug", "AppStream (Debug RPMs)", "appstream/debug", False),
              ("appstream-source", "AppStream (Source RPMs)", "appstream/source/SRPMS", False))
DNF_PACKAGES = "dnf-packages.json"
# the packager and build host of Red Hat's packages (dnf --version)
RHEL_PACKAGER = "Red Hat, Inc. <http://bugzilla.redhat.com/bugzilla>"
# service --status-all: the init scripts of the image (unverified: typical cloud and Pi images);
#   RHEL 9 has none, CentOS 7 the network scripts, which print their own status
_UBUNTU_INIT = ("+ apparmor", "+ apport", "+ cron", "+ dbus", "- hwclock.sh", "+ irqbalance", "- iscsid",
                "+ kmod", "- open-iscsi", "- plymouth", "- plymouth-log", "+ procps", "- rsync", "+ ssh",
                "+ udev", "+ ufw", "+ unattended-upgrades", "- uuidd")
_DEBIAN_INIT = ("+ cron", "+ dbus", "- hwclock.sh", "+ kmod", "+ procps", "+ ssh", "- sudo", "+ udev")
_PI_INIT = ("- alsa-utils", "+ avahi-daemon", "+ bluetooth", "- console-setup.sh", "+ cron", "+ dbus",
            "+ dphys-swapfile", "+ fake-hwclock", "- hwclock.sh", "- keyboard-setup.sh", "+ kmod", "+ procps",
            "- rsync", "+ ssh", "- sudo", "+ triggerhappy", "+ udev")
_CENTOS_STATUS = ("netconsole module not loaded", "Configured devices:", "lo eth0", "Currently active devices:",
                  "lo eth0")
# the systemd units behind an init script of service --status-all whose name is not the unit's
INIT_UNITS = {"kmod": ("kmod-static-nodes.service",), "procps": ("systemd-sysctl.service",),
              "udev": ("systemd-udevd.service",)}
# the network scripts of CentOS 7 initscripts (unverified: shortened to the parts that service and
#   systemctl show)
CENTOS_FUNCTIONS = r"""# -*-Shell-script-*-
#
# functions	This file contains functions to be used by most or all
#		shell scripts in the /etc/init.d directory.
#

TEXTDOMAIN=initscripts

# Make sure umask is sane
umask 022

# Set up a default search path.
PATH="/sbin:/usr/sbin:/bin:/usr/bin"
export PATH

if [ $PPID -ne 1 -a -z "$SYSTEMCTL_SKIP_REDIRECT" ] && \
        [ -d /run/systemd/system ] ; then
    case "$0" in
    /etc/init.d/*|/etc/rc.d/init.d/*)
        _use_systemctl=1
        ;;
    esac
fi

systemctl_redirect () {
    local s
    local prog=${1##*/}
    local command=$2
    local options=""

    case "$command" in
    start)
        s=$"Starting $prog (via systemctl): "
        ;;
    stop)
        s=$"Stopping $prog (via systemctl): "
        ;;
    reload|try-reload)
        s=$"Reloading $prog configuration (via systemctl): "
        ;;
    restart|try-restart|condrestart)
        s=$"Restarting $prog (via systemctl): "
        ;;
    esac

    if [ -n "$SYSTEMCTL_IGNORE_DEPENDENCIES" ] ; then
        options="--ignore-dependencies"
    fi

    action "$s" /bin/systemctl $options $command "$prog.service"
}

# Get a sane screen width
[ -z "${COLUMNS:-}" ] && COLUMNS=80

if [ -z "${CONSOLETYPE:-}" ]; then
    if [ -c "/dev/stderr" -a -r "/dev/stderr" ]; then
        CONSOLETYPE="$(/sbin/consoletype < /dev/stderr 2>/dev/null)"
    else
        CONSOLETYPE="serial"
    fi
fi

if [ -z "${BOOTUP:-}" ]; then
    if [ -f /etc/sysconfig/init ]; then
        . /etc/sysconfig/init
    else
        BOOTUP=color
        RES_COL=60
        MOVE_TO_COL="echo -en \\033[${RES_COL}G"
        SETCOLOR_SUCCESS="echo -en \\033[1;32m"
        SETCOLOR_FAILURE="echo -en \\033[1;31m"
        SETCOLOR_WARNING="echo -en \\033[1;33m"
        SETCOLOR_NORMAL="echo -en \\033[0;39m"
        LOGLEVEL=1
    fi
    if [ "$CONSOLETYPE" = "serial" ]; then
        BOOTUP=serial
        MOVE_TO_COL=
        SETCOLOR_SUCCESS=
        SETCOLOR_FAILURE=
        SETCOLOR_WARNING=
        SETCOLOR_NORMAL=
    fi
fi

echo_success() {
    [ "$BOOTUP" = "color" ] && $MOVE_TO_COL
    echo -n "["
    [ "$BOOTUP" = "color" ] && $SETCOLOR_SUCCESS
    echo -n $"  OK  "
    [ "$BOOTUP" = "color" ] && $SETCOLOR_NORMAL
    echo -n "]"
    echo -ne "\r"
    return 0
}

echo_failure() {
    [ "$BOOTUP" = "color" ] && $MOVE_TO_COL
    echo -n "["
    [ "$BOOTUP" = "color" ] && $SETCOLOR_FAILURE
    echo -n $"FAILED"
    [ "$BOOTUP" = "color" ] && $SETCOLOR_NORMAL
    echo -n "]"
    echo -ne "\r"
    return 1
}

success() {
    [ "$BOOTUP" != "verbose" -a -z "${LSB:-}" ] && echo_success
    return 0
}

failure() {
    local rc=$?
    [ "$BOOTUP" != "verbose" -a -z "${LSB:-}" ] && echo_failure
    [ -x /bin/plymouth ] && /bin/plymouth --details
    return $rc
}

action() {
    local STRING rc

    STRING=$1
    echo -n "$STRING "
    shift
    "$@" && success $"$STRING" || failure $"$STRING"
    rc=$?
    echo
    return $rc
}

is_true() {
    case "$1" in
    [tT] | [yY] | [yY][eE][sS] | [oO][nN] | [tT][rR][uU][eE] | 1)
        return 0
        ;;
    esac
    return 1
}

is_false() {
    case "$1" in
    [fF] | [nN] | [nN][oO] | [oO][fF][fF] | [fF][aA][lL][sS][eE] | 0)
        return 0
        ;;
    esac
    return 1
}

# Evaluate shvar-style booleans
if [ -n "${_use_systemctl:-}" ] && [ "$1" != "status" ]; then
    systemctl_redirect $0 $1
    exit $?
fi
"""
CENTOS_NETCONSOLE = r"""#!/bin/bash
#
# netconsole	This loads the netconsole module with the configured parameters.
#
# chkconfig: - 50 50
# description: Initializes network console logging
# config: /etc/sysconfig/netconsole
#
### BEGIN INIT INFO
# Provides:          netconsole
# Required-Start:    $network
# Short-Description: Initializes network console logging
# Description:       Initializes network console logging of kernel messages.
### END INIT INFO

# Copyright 2002 Red Hat, Inc.
#
# Based in part on a shell script by
# Andreas Dilger <adilger@turbolinux.com>  Sep 26, 2001

PATH=/sbin:/usr/sbin:$PATH
RETVAL=0
SERVER_ADDRESS_RESOLUTION=

# Check that networking is up.
. /etc/sysconfig/network

# Source function library.
. /etc/rc.d/init.d/functions

# Default values
LOCALPORT=6666
DEV=
SYSLOGADDR=
SYSLOGPORT=514
SYSLOGMACADDR=

kernel=$(uname -r | cut -d. -f1-2)

usage ()
{
	echo $"Usage: $0 {start|stop|status|restart|condrestart}" 1>&2
	RETVAL=2
}

[ -f /etc/sysconfig/netconsole ] && . /etc/sysconfig/netconsole

case "$1" in
  start)
	[ -z "$SYSLOGADDR" ] && exit 6
	modprobe netconsole netconsole=$LOCALPORT@/$DEV,$SYSLOGPORT@$SYSLOGADDR/$SYSLOGMACADDR 2>/dev/null
	RETVAL=$?
	;;
  stop)
	modprobe -r netconsole 2>/dev/null
	RETVAL=$?
	;;
  status)
	if grep -q "^netconsole " /proc/modules ; then
		echo $"netconsole module loaded"
		RETVAL=0
	else
		echo $"netconsole module not loaded"
		RETVAL=3
	fi
	;;
  restart|reload)
	$0 stop
	$0 start
	RETVAL=$?
	;;
  condrestart)
	if grep -q "^netconsole " /proc/modules ; then
		$0 restart
	fi
	;;
  *)
	usage
	;;
esac

exit $RETVAL
"""
CENTOS_NETWORK = r"""#! /bin/bash
#
# network       Bring up/down networking
#
# chkconfig: 2345 10 90
# description: Activates/Deactivates all network interfaces configured to \
#              start at boot time.
#
### BEGIN INIT INFO
# Provides: $network
# Should-Start: iptables ip6tables NetworkManager-wait-online NetworkManager $network-pre
# Short-Description: Bring up/down networking
# Description: Bring up/down networking
### END INIT INFO

# Source function library.
. /etc/init.d/functions

if [ ! -f /etc/sysconfig/network ]; then
    exit 6
fi

. /etc/sysconfig/network

# Check that networking is up.
[ "${NETWORKING}" = "no" ] && exit 6

# if the ip configuration utility isn't around we can't function.
[ -x /sbin/ip ] || exit 1

CWD=$(pwd)
cd /etc/sysconfig/network-scripts

. ./network-functions

# find all the interfaces besides loopback.
# ignore aliases, alternative configurations, and editor backup files
interfaces=$(ls ifcfg-* | \
            LC_ALL=C sed -e "$__sed_discard_ignored_files" \
                       -e '/\(ifcfg-lo$\|:\|ifcfg-.*-range\)/d' \
                       -e '{ s/^ifcfg-//g;s/[0-9]/ &/}' | \
            LC_ALL=C sort -k 1,1 -k 2n | \
            LC_ALL=C sed 's/ //')
rc=0

# See how we were called.
case "$1" in
start)
        [ "$EUID" != "0" ] && exit 4
        rc=0
        action $"Bringing up loopback interface: " ./ifup ifcfg-lo
        for i in $interfaces; do
                action $"Bringing up interface $i: " ./ifup $i boot
                [ $? -ne 0 ] && rc=1
        done
        touch /var/lock/subsys/network
        ;;
stop)
        [ "$EUID" != "0" ] && exit 4
        for i in $interfaces ; do
                action $"Shutting down interface $i: " ./ifdown $i boot
        done
        action $"Shutting down loopback interface: " ./ifdown ifcfg-lo
        rm -f /var/lock/subsys/network
        ;;
status)
        echo $"Configured devices:"
        echo lo $interfaces

        echo $"Currently active devices:"
        echo $(/sbin/ip -o link show up | awk -F ": " '{ print $2 }')
        ;;
restart|reload|force-reload)
        cd "$CWD"
        $0 stop
        $0 start
        rc=$?
        ;;
*)
        echo $"Usage: $0 {start|stop|status|restart|reload|force-reload}"
        exit 2
esac

exit $rc
"""
# the run level links of the CentOS 7 network scripts (chkconfig: netconsole off, network 2345)
CENTOS_RC_LINKS = tuple((f"etc/rc.d/rc{level}.d/K50netconsole", "etc/rc.d/init.d/netconsole") for level in range(7)) + tuple(
    (f"etc/rc.d/rc{level}.d/{'S10' if level in (2, 3, 4, 5) else 'K90'}network", "etc/rc.d/init.d/network")
    for level in range(7))

# the commands of Cowrie's Python a server runs in place of a txtcmd (F7), with their spellings
SERVER_COMMANDS = ("sudo", "service", "systemctl")
APT_COMMANDS = ("apt", "apt-get")
YUM_COMMANDS = ("yum",)
# RHEL 9: dnf 4 (its yum and dnf are links to dnf-3) and rpm
DNF_COMMANDS = ("dnf", "dnf-3", "yum", "rpm")
DNF_LINKS = (("usr/bin/dnf", "usr/bin/dnf-3"), ("usr/bin/yum", "usr/bin/dnf-3"))


def has_dpkg(persona):
    return "dpkg" in persona.package_managers


def has_systemd(persona):
    return persona.kind == "server" or persona.family in SYSTEMD_DEVICES


def foreign_paths(persona):
    """Paths (glob patterns in their last part) of Cowrie's tree the persona's system does not have."""
    paths = ()
    if not has_dpkg(persona):
        # Red Hat has an installkernel of its own (grubby)
        paths += DEBIAN_PATHS + tuple(p for p in DEBIAN_SYSTEM_PATHS
                                      if not (is_el(persona) and p == "usr/sbin/installkernel"))
    if persona.hardware_platform != "x86_64":
        paths += X86_PATHS
    if not has_systemd(persona):
        paths += SYSTEMD_PATHS
    if persona.ssh_profile.startswith("dropbear"):
        paths += OPENSSH_PATHS
    if triplet(persona) is None:
        paths += (f"usr/lib/{MULTIARCH}",)
    if persona.kind == "device":
        paths += PYTHON_PATHS + (() if has_perl(persona) else PERL_PATHS)
    elif persona.family == "centos":
        paths += PYTHON_PATHS
    if is_el(persona):
        paths += EL_DEBIAN_PROGRAMS + (RHEL_DEBIAN_PROGRAMS if persona.family == "rhel" else ())
    return paths


def foreign_words(persona):
    """Words no name or link target of the persona's tree has (x86_64 on another architecture,
    the Debian packages of DEBIAN_WORDS without dpkg)."""
    words = ()
    if not has_dpkg(persona):
        words += DEBIAN_WORDS
    if persona.hardware_platform != "x86_64":
        words += X86_WORDS
    return words


def is_el(persona):
    return persona.family in ("rhel", "centos")


def has_perl(persona):
    """Perl: every server and EdgeOS (the Vyatta configuration system is Perl)."""
    return persona.kind == "server" or persona.family == "ubiquiti"


def triplet(persona):
    """The multiarch directory of a Debian system, None for other systems (no such directory)."""
    return TRIPLETS.get(persona.hardware_platform) if has_dpkg(persona) else None


def codename(persona, ident):
    """The release name of a Debian family system ("stretch" on EdgeOS), the family on Red Hat."""
    return _codename(persona, ident)


def _codename(persona, ident):
    if persona.family == "ubiquiti":
        return "stretch"   # EdgeOS 2.0: a Debian 9 userland
    return persona.family if is_el(persona) else ident.release.extra.get("codename")


def _upstream(version):
    """The upstream part of a package version: 1:2.43.0-1ubuntu7.3 -> 2.43.0."""
    return version.split(":", 1)[-1].split("-", 1)[0]


def tools(persona, ident):
    """{"perl": (version, copyright year), "python": version, "git": version} of a server (git where
    the release has it installed), {"perl": ...} on EdgeOS, {} on the other devices."""
    if not has_perl(persona):
        return {}
    code = _codename(persona, ident)
    if persona.kind != "server":
        return {"perl": PERL[code]}
    python = PYTHON[code]
    if persona.family == "rhel":
        python = RHEL_PYTHON.get(ident.release.os_version_id, python)
    found = {"perl": PERL[code], "python": python}
    if "git" in ident.release.packages:
        found["git"] = _upstream(ident.release.packages["git"])
    return found


# coreutils of the releases without a coreutils package in the catalog (df --version)
COREUTILS = {"rhel": "8.32", "centos": "8.22", "stretch": "8.26", "synology": "8.31"}
# the year of the copyright line of `df --version` per coreutils release
COREUTILS_YEARS = {"8.22": "2013", "8.26": "2016", "8.28": "2017", "8.30": "2018", "8.31": "2019", "8.32": "2020",
                   "9.1": "2022", "9.4": "2023", "9.5": "2024", "9.7": "2025"}


# the devices whose firmware has GNU coreutils (their df, procps top)
COREUTILS_DEVICES = ("synology", "ubiquiti")


def coreutils_version(persona, ident):
    """The GNU coreutils of a server or of a device with coreutils (its release's package, else
    COREUTILS)."""
    code = "synology" if persona.family == "synology" else _codename(persona, ident)
    package = {**APT_INSTALLED.get(code, {}), **ident.release.packages}.get("coreutils", "")
    return COREUTILS.get(code) or _upstream(package) or "9.1"


# The applets of the firmware's BusyBox, from the links of its root file system (read 2026-10-13
#   from the OpenWrt 21.02.7 / 22.03.7 ramips sysupgrade images and the EdgeRouter X
#   2.0.9-hotfix.2 image); the other firmware keeps the shared list (unverified). The BusyBox of
#   the TL-WR841N v14 250328 image links no wc, sort or seq: its list is the shared one less wc
#   (the shared list keeps the applets Cowrie answers there).
_BB_OPENWRT = ("[, [[, ash, awk, basename, brctl, bunzip2, bzcat, cat, chgrp, chmod, chown, chroot, clear, cmp, cp, "
               "crond, crontab, cut, date, dd, df, dirname, dmesg, du, echo, egrep, env, expr, false, fgrep, find, "
               "flock, free, fsync, grep, gunzip, gzip, halt, head, hexdump, hwclock, id, ifconfig, ip, kill, "
               "killall, less, ln, lock, logger, login, ls, md5sum, mkdir, mkfifo, mknod, mkswap, mktemp, mount, mv, "
               "nc, netmsg, netstat, nice, nslookup, ntpd, passwd, pgrep, pidof, ping, ping6, pivot_root, poweroff, "
               "printf, ps, pwd, readlink, reboot, reset, rm, rmdir, route, sed, seq, sh, sha256sum, sleep, sort, "
               "start-stop-daemon, strings, swapoff, swapon, switch_root, sync, sysctl, tail, tar, tee, test, time, "
               "top, touch, tr, traceroute, traceroute6, true, udhcpc, umount, uname, uniq, uptime, vi, wc, which, "
               "xargs, yes, zcat")
_BB_EDGEOS = ("[, [[, arping, base64, basename, blkdiscard, bunzip2, bzcat, bzip2, cal, cat, chgrp, chroot, cksum, "
              "comm, cp, cpio, cut, date, dd, df, dirname, dos2unix, du, echo, ed, env, expand, expr, factor, false, "
              "fatattr, fold, fsfreeze, groups, head, hostid, hostname, i2cdetect, i2cdump, i2cget, i2cset, id, "
              "install, link, linux32, linux64, ln, logname, ls, lsscsi, lzcat, md5sum, mkdir, mkfifo, mknod, "
              "mkpasswd, mktemp, mv, nc, nice, nl, nohup, nproc, od, partprobe, paste, printenv, printf, pwd, "
              "realpath, rm, rmdir, seq, setpriv, sha1sum, sha256sum, sha3sum, sha512sum, shred, shuf, sleep, sort, "
              "split, ssl_client, stat, stty, sum, svc, sync, tac, tail, tee, telnet, telnetd, test, tftp, touch, tr, "
              "traceroute, true, truncate, tty, uname, unexpand, uniq, unix2dos, unlink, usleep, uudecode, uuencode, "
              "vi, wc, who, whoami, whois, xzcat, yes")
BB_SHARED = ("[, [[, ash, awk, basename, cat, chmod, chown, cp, cut, date, dd, df, dmesg, echo, egrep, env, "
             "false, free, grep, gunzip, gzip, head, hostname, ifconfig, kill, killall, ln, login, ls, mkdir, "
             "mknod, mount, mv, netstat, ping, ps, pwd, reboot, rm, route, sed, sh, sleep, sync, tail, tar, "
             "telnetd, test, top, touch, tr, true, udhcpc, umount, uname, uptime, vi, wc, wget")
# the C library of the firmware (the texts of option errors): ld-musl of OpenWrt, ld-uClibc of the
#   TL-WR841N, glibc (libc.so.6) of EdgeOS, read from their BusyBox; unverified: the others
FIRMWARE_LIBC = {"openwrt": "musl", "tplink": "uclibc", "ubiquiti": "glibc", "dlink": "uclibc", "ipcam": "uclibc",
                 "zyxel": "glibc", "qnap": "glibc"}
# where BusyBox installs the applets (the paths of the OpenWrt image)
APPLET_PATHS = {"wc": "usr/bin/wc", "sort": "usr/bin/sort", "seq": "usr/bin/seq"}
TEXT_TOOLS = ("wc", "sort", "seq")


def busybox_applets(persona, ident):
    """The applets of the persona's BusyBox, in the order of its help."""
    if persona.family == "openwrt":
        text = _BB_OPENWRT if ident.release.label.startswith("21.") else _BB_OPENWRT.replace("bunzip2, bzcat, ", "")
    elif persona.family == "ubiquiti":
        text = _BB_EDGEOS
    elif persona.family == "tplink":
        text = BB_SHARED.replace(", wc, ", ", ")
    else:
        text = BB_SHARED
    return text.split(", ")


def text_programs(persona, ident):
    """{name: (path, link target or None)} of wc, sort and seq: the coreutils of a server or of DSM
    (Cowrie's tree has them), the applets of the firmware's BusyBox (links to it; programs where the
    tree has no busybox)."""
    if persona.kind == "server" or persona.family == "synology":
        return {name: (f"usr/bin/{name}", None) for name in TEXT_TOOLS}
    applets = set(busybox_applets(persona, ident))
    banner = ident.release.extra.get("busybox")
    target = "/bin/busybox" if persona.family == "ubiquiti" else "../../bin/busybox"
    return {name: (APPLET_PATHS[name], target if banner else None) for name in TEXT_TOOLS if name in applets}


# the locale of a login (order of sort, quotation marks, help texts): C.UTF-8 of the Ubuntu and
#   Debian cloud images, en_GB.UTF-8 of Raspberry Pi OS, en_US.UTF-8 of the Red Hat images (glibc
#   2.17 on CentOS 7 orders ties differently), POSIX on DSM (unverified: the Debian and RHEL images, DSM)
TEXT_LOCALES = {"raspios": "en", "rhel": "en", "centos": "en-2.17", "synology": "posix"}


def text_keys(persona, ident):
    """[shell] keys of the patched wc, sort and seq: GNU coreutils (version, copyright year, Debian's
    packager line, the locale, Red Hat's byte order for -f) or the firmware's BusyBox (banner,
    version, C library)."""
    if persona.kind == "server" or persona.family == "synology":
        version = coreutils_version(persona, ident)
        keys = {"text_style": "gnu", "text_version": version, "text_year": COREUTILS_YEARS.get(version, "2022"),
                "text_collate": TEXT_LOCALES.get(persona.family, "c")}
        package = APT_INSTALLED.get(_codename(persona, ident), {}).get("coreutils", "")
        if _codename(persona, ident) == "trixie" and package:
            keys["text_packager"] = f"Debian ({package})"
        if is_el(persona):
            keys["text_fold"] = "bytes"
        return keys
    banner = ident.release.extra.get("busybox", "")
    found = re.match(r"BusyBox v(\d+\.\d+\.\d+)", banner)
    return {"text_style": "busybox", "text_banner": banner, "text_version": found.group(1) if found else "1.24.1",
            "text_libc": FIRMWARE_LIBC.get(persona.family, "glibc")}


def df_table(persona, ident, filesystems):
    """What the patched df reads ([shell] df_table): the file system table of the persona, the
    style of df (GNU coreutils on servers and the devices with coreutils, else BusyBox) and its
    version (coreutils of the release, the BusyBox of the firmware; unverified: the BusyBox of
    QNAP, which names none, and coreutils of DSM)."""
    data = {"filesystems": filesystems}
    if persona.kind == "server" or persona.family in COREUTILS_DEVICES:
        version = coreutils_version(persona, ident)
        data.update(style="gnu", coreutils=version, coreutils_year=COREUTILS_YEARS.get(version, "2022"),
                    el=persona.family in ("rhel", "centos"))
    else:
        banner = ident.release.extra.get("busybox", "")
        found = re.match(r"BusyBox v(\d+)\.(\d+)\.(\d+)", banner)
        data.update(style="busybox", banner=banner,
                    busybox=[int(x) for x in found.groups()] if found else [1, 24, 1])
    return data


# awk of the releases (unverified: the gawk of DSM, mawk of EdgeOS 2)
MAWK = {"noble": "1.3.4 20240123", "jammy": "1.3.4 20200120", "bookworm": "1.3.4 20200120",
        "trixie": "1.3.4 20250131", "stretch": "1.3.3"}
GAWK = {"rhel": "5.1.0", "centos": "4.0.2", "synology": "4.1.4"}


def awk_keys(persona, ident):
    """[shell] keys of the patched awk: its style (gawk on Red Hat and DSM, mawk on the Debian
    family, BusyBox's on the other devices) and version (the BusyBox banner)."""
    code = _codename(persona, ident)
    if persona.family in GAWK:
        return {"awk_style": "gawk", "awk_version": GAWK[persona.family]}
    if persona.kind == "server" or persona.family == "ubiquiti":
        return {"awk_style": "mawk", "awk_version": MAWK.get(code, "1.3.4 20200120")}
    return {"awk_style": "busybox", "awk_version": ident.release.extra.get("busybox", "")}


def python3_name(version):
    """python3.12 of 3.12.3; None for a Python 2."""
    major, minor = version.split(".")[:2]
    return f"python{major}.{minor}" if major == "3" else None


def python_answers(persona):
    """The commands of Cowrie's Python a server runs (not skipped) besides the interpreters:
    sudo, service and its package manager (apt of Debian, yum 3 of CentOS 7, the patched dnf 4
    and rpm of RHEL 9)."""
    if persona.kind != "server":
        return ()
    answers = SERVER_COMMANDS
    if has_dpkg(persona):
        answers += APT_COMMANDS
    if persona.family == "centos":
        answers += YUM_COMMANDS
    if persona.family == "rhel":
        answers += DNF_COMMANDS
    return answers


def command_path(name):
    return f"usr/sbin/{name}" if name == "service" else f"usr/bin/{name}"


def programs(persona, ident):
    """The programs of the persona's system Cowrie's tree has not (or elsewhere): perl, git, its Python,
    sudo, service, systemctl and its package manager on a server, perl on EdgeOS; which and the dynamic
    loader of a system without dpkg."""
    found = tools(persona, ident)
    paths = ["usr/bin/perl"] if "perl" in found else []
    if "git" in found:
        paths.append("usr/bin/git")
    if "python" in found:
        name = python3_name(found["python"])
        paths.append(f"usr/bin/{name}" if name else "usr/bin/python2.7")
    # dnf-3 is a Python script with its text (servers.build_rhel), dnf and yum are links to it
    paths += [command_path(command) for command in python_answers(persona)
              if persona.family != "rhel" or command not in ("dnf", "dnf-3", "yum")]
    if not has_dpkg(persona):
        paths.append("usr/bin/which")
        if persona.hardware_platform == "x86_64":
            paths.append(LOADER if persona.family != "centos" else CENTOS_LOADER)
    if is_el(persona):
        paths += ["usr/libexec/openssh/sftp-server", "usr/bin/gawk"]
    if persona.family == "rhel":
        paths.append("usr/bin/pidof")
    return tuple(paths)


# the dynamic loader of a system without Debian's multiarch directory (glibc 2.34 and later: the file
#   itself); CentOS 7 links it to the loader of glibc 2.17
LOADER = "usr/lib64/ld-linux-x86-64.so.2"
CENTOS_LOADER = "usr/lib64/ld-2.17.so"
# what ls -l shows for these programs (unverified: typical sizes); service is a shell script
PROGRAM_SIZES = {"which": (24_000, 32_000), "ld-linux-x86-64.so.2": (200_000, 240_000),
                 "ld-2.17.so": (160_000, 166_000), "sftp-server": (120_000, 140_000)}


def program_sizes(persona):
    """The size ranges of PROGRAM_SIZES, with /usr/sbin/service of the persona's family (init-system-helpers
    on Debian, initscripts on Red Hat)."""
    sizes = dict(PROGRAM_SIZES)
    sizes["service"] = (3_200, 3_900) if is_el(persona) else (8_800, 9_400)
    return sizes


EL_RC_LOCAL = """#!/bin/bash
# THIS FILE IS ADDED FOR COMPATIBILITY PURPOSES
#
# It is highly advisable to create own systemd services or udev rules
# to run scripts during boot instead of using this file.
#
# In contrast to previous versions due to parallel execution during boot
# this script will NOT be run after all other services.
#
# Please note that you must run 'chmod +x /etc/rc.d/rc.local' to ensure
# that this script will be executed during boot.

touch /var/lock/subsys/local
"""
# the README of systemd in the init directory (unverified: the exact text of each release)
EL_INIT_README = """You are looking for the traditional init scripts in /etc/rc.d/init.d,
and they are gone?

Here's an explanation on what's going on:

You are running a systemd-based OS where traditional init scripts have
been replaced by native systemd services files. Service files provide
very similar functionality to init scripts. To make use of service
files simply invoke "systemctl", which will output a list of all
currently running services (and other units). Use "systemctl
list-unit-files" to get a listing of all known unit files, including
stopped, disabled and masked ones. Use "systemctl start
foobar.service" and "systemctl stop foobar.service" to start or stop a
service, respectively. For further details, please refer to
systemctl(1).

Note that traditional init scripts continue to function on a systemd
system. An init script /etc/rc.d/init.d/foobar is implicitly mapped
into a service unit foobar.service during system initialization.

Thank you!

Further reading:
        man:systemctl(1)
        man:systemd(1)
        http://0pointer.de/blog/projects/systemd-for-admins-3.html
        https://www.freedesktop.org/wiki/Software/systemd/Incompatibilities
"""
EL_SSHD_UNIT = """[Unit]
Description=OpenSSH server daemon
Documentation=man:sshd(8) man:sshd_config(5)
After=network.target sshd-keygen.{keygen}
Wants=sshd-keygen.{keygen}

[Service]
Type=notify
EnvironmentFile={env}/etc/sysconfig/sshd
ExecStart=/usr/sbin/sshd -D $OPTIONS
ExecReload=/bin/kill -HUP $MAINPID
KillMode=process
Restart=on-failure
RestartSec=42s

[Install]
WantedBy=multi-user.target
"""


UBUNTU_ORIGIN = """Vendor: Ubuntu
Vendor-URL: https://www.ubuntu.com/
Bugs: https://bugs.launchpad.net/ubuntu/+filebug
Parent: Debian
"""
# installkernel of grubby (unverified: shortened)
EL_INSTALLKERNEL = """#! /bin/sh
#
# /sbin/installkernel
#
# Copyright 2007-2008 Red Hat, Inc.  All rights reserved.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# Author(s): tyson@rwii.com
#

usage() {
    echo "Usage: `basename $0` <kernel_version> <bootimage> <mapfile>" >&2
    exit 1
}

cfgLoader=

if [ -z "$INSTALL_PATH" -o "$INSTALL_PATH" == "/boot" ]; then
    INSTALL_PATH=/boot
    cfgLoader=1
fi

LINK_PATH=/boot
RELATIVE_PATH=`echo "$INSTALL_PATH/" | sed "s|^$LINK_PATH/||"`
KERNEL_VERSION=$1
BOOTIMAGE=$2
MAPFILE=$3
ARCH=$(uname -m)
if [ $ARCH = 'ppc64' -o $ARCH = 'ppc' ]; then
    KERNEL_NAME=vmlinux
else
    KERNEL_NAME=vmlinuz
fi

if [ -z "$KERNEL_VERSION" -o -z "$BOOTIMAGE" -o -z "$MAPFILE" ]; then
    usage
fi

if [ -f $INSTALL_PATH/$KERNEL_NAME-$KERNEL_VERSION ]; then
      mv $INSTALL_PATH/$KERNEL_NAME-$KERNEL_VERSION \
              $INSTALL_PATH/$KERNEL_NAME-$KERNEL_VERSION.old;
fi

if [ ! -L $INSTALL_PATH/$KERNEL_NAME ]; then
    if [ -e $INSTALLPATH/$KERNEL_NAME ]; then
        mv $INSTALL_PATH/$KERNEL_NAME $INSTALL_PATH/$KERNEL_NAME.old
    fi
fi

cat $BOOTIMAGE > $INSTALL_PATH/$KERNEL_NAME-$KERNEL_VERSION
cp $MAPFILE $INSTALL_PATH/System.map-$KERNEL_VERSION

ln -fs ${RELATIVE_PATH}$INSTALL_PATH/$KERNEL_NAME-$KERNEL_VERSION $LINK_PATH/$KERNEL_NAME
ln -fs ${RELATIVE_PATH}$INSTALL_PATH/System.map-$KERNEL_VERSION $LINK_PATH/System.map

if [ -n "$cfgLoader" ] && [ -x /sbin/new-kernel-pkg ]; then
    /sbin/new-kernel-pkg --mkinitrd --dracut --depmod --install $KERNEL_VERSION
    exit $?
fi
"""


def files(persona):
    """Files of the persona's system in place of Debian's: dpkg's vendor on Ubuntu; the init directory,
    rc.local, the sshd unit and installkernel of Red Hat."""
    if persona.os_id == "ubuntu":
        return {"etc/dpkg/origins/ubuntu": UBUNTU_ORIGIN}
    if not is_el(persona):
        return {}
    rhel = persona.family == "rhel"
    found = {"etc/rc.d/init.d/README": EL_INIT_README, "etc/rc.d/rc.local": EL_RC_LOCAL,
             "usr/sbin/installkernel": EL_INSTALLKERNEL,
             "usr/lib/systemd/system/sshd.service": EL_SSHD_UNIT.format(keygen="target" if rhel else "service",
                                                                       env="-" if rhel else "")}
    if not rhel:
        found.update({"etc/rc.d/init.d/functions": CENTOS_FUNCTIONS, "etc/rc.d/init.d/netconsole": CENTOS_NETCONSOLE,
                      "etc/rc.d/init.d/network": CENTOS_NETWORK})
    return found


def links(persona):
    """(link, target) of the persona's system in place of Debian's (targets from the root, as in
    Cowrie's pickle): Red Hat's init directories, the enabled sshd and adduser (useradd), the loader
    link of CentOS 7."""
    found = []
    if persona.os_id == "ubuntu":
        found.append(("etc/dpkg/origins/default", "etc/dpkg/origins/ubuntu"))
    if is_el(persona):
        found += [("etc/init.d", "etc/rc.d/init.d"), ("etc/rc.local", "etc/rc.d/rc.local"),
                  ("etc/systemd/system/multi-user.target.wants/sshd.service", "usr/lib/systemd/system/sshd.service"),
                  ("usr/sbin/adduser", "usr/sbin/useradd")]
        found += [(f"etc/rc{level}.d", f"etc/rc.d/rc{level}.d") for level in range(7)]
        found += EL_PROGRAM_LINKS
    if persona.family == "rhel":
        found += DNF_LINKS
    if persona.family == "centos":
        found.append((LOADER, CENTOS_LOADER))
        found += CENTOS_RC_LINKS
    return tuple(found)


def ssh_units(persona, ident):
    """(paths to remove, links) of the ssh units: Ubuntu 24.04 starts sshd from ssh.socket, so
    ssh.service is not enabled (no alias sshd.service); the socket's link comes with the units."""
    if ident.release.extra.get("codename") != "noble":
        return (), ()
    return ("etc/systemd/system/sshd.service", "etc/systemd/system/multi-user.target.wants/ssh.service"), ()


# where systemd looks for an ExecStart program given without a path
EXEC_DIRS = ("usr/local/bin", "usr/bin", "usr/sbin", "usr/lib/systemd")


def exec_program(line):
    """The program of an ExecStart= line: the first word without the prefixes of systemd (@-+!:)."""
    command = line.split("=", 1)[1].strip()
    return command.lstrip("@-+!:").split()[0] if command.lstrip("@-+!:") else ""


def program_node(fs, program):
    """The node of an ExecStart program (absolute, or searched in EXEC_DIRS), None if there is none."""
    from . import tree
    if program.startswith("/"):
        return tree.find_node(fs, program)
    return next((node for node in (tree.find_node(fs, f"{d}/{program}") for d in EXEC_DIRS) if node is not None),
                None)


def directories(persona):
    """Empty directories of the persona's system (the run level directories of Red Hat)."""
    return tuple(f"etc/rc.d/rc{level}.d" for level in range(7)) if is_el(persona) else ()


# the devices whose /bin/sh is BusyBox ash (DSM's is bash, EdgeOS logs in to vbash)
ASH_FAMILIES = frozenset({"dlink", "tplink", "zyxel", "openwrt", "qnap", "ipcam"})


def shell_style(persona):
    """(the shell's name, how it says a command is missing): BusyBox ash prints "-ash: x: not found",
    bash and vbash "-bash: x: command not found"; the name is that of the login shell."""
    return persona.shell.rsplit("/", 1)[-1], "ash" if persona.family in ASH_FAMILIES else "bash"


def login_program(persona, ident):
    """The program of the login shell (/proc/self/exe of the shell): BusyBox of a firmware with it,
    else bash."""
    return "bin/busybox" if ident.release.extra.get("busybox") and persona.family != "ubiquiti" else "usr/bin/bash"


def apt_sources(files):
    """(url, suite) of the apt sources in files (sources.list, then sources.list.d in order;
    one-line and deb822 format), as apt-get update names them."""
    found = []
    for rel in ["etc/apt/sources.list"] + sorted(r for r in files if r.startswith("etc/apt/sources.list.d/")):
        text = files.get(rel, "")
        if rel.endswith(".sources"):
            for block in text.split("\n\n"):
                fields = dict(line.split(":", 1) for line in block.splitlines() if ":" in line and line[0] != " ")
                if "deb" not in fields.get("Types", "").split():
                    continue
                for url in fields.get("URIs", "").split():
                    found += [(url.rstrip("/"), suite) for suite in fields.get("Suites", "").split()]
        else:
            for line in text.splitlines():
                words = line.split()
                if len(words) >= 3 and words[0] == "deb":
                    found.append((words[1].rstrip("/"), words[2]))
    return list(dict.fromkeys(found))


def sudo_version(persona, ident):
    package = ident.release.packages.get("sudo")
    return _upstream(package) if package else SUDO[_codename(persona, ident)]


def sudo_grammar(version):
    major, minor, patch = (int(x) for x in re.match(r"(\d+)\.(\d+)\.(\d+)", version).groups())
    if (major, minor) < (1, 9):
        return "46"
    return "48" if patch < 12 else "50"


# the init scripts of service --status-all that Cowrie's Debian tree has (kept as they are)
TREE_INIT = frozenset({"hwclock.sh", "procps", "ssh"})
# their Default-Start / Default-Stop (Cowrie's pickle has their names and links, not their text)
TREE_INIT_LEVELS = {"hwclock.sh": ("S", "0 6"), "procps": ("S", ""), "ssh": ("2 3 4 5", "")}
# Short-Description of the LSB header of each script (unverified: the wording of each package)
INIT_DESCRIPTIONS = {
    "alsa-utils": "Restore and store ALSA driver settings", "apparmor": "AppArmor initialization",
    "apport": "automatic crash report generation", "avahi-daemon": "Avahi mDNS/DNS-SD Daemon",
    "bluetooth": "Start bluetooth daemons", "console-setup.sh": "Set console font and keymap",
    "cron": "Regular background program processing daemon", "dbus": "D-Bus systemwide message bus",
    "dphys-swapfile": "Autogenerate and use a swap file", "fake-hwclock": "Restore / save the current clock",
    "irqbalance": "daemon to balance interrupts for SMP systems", "iscsid": "iSCSI initiator daemon (iscsid)",
    "keyboard-setup.sh": "Set the console keyboard layout", "kmod": "Load the modules listed in /etc/modules.",
    "open-iscsi": "Login to default iSCSI targets", "plymouth": "Stop Plymouth Boot Screen",
    "plymouth-log": "Copy the boot log to /var/log/boot.log", "rsync": "fast remote file copy program daemon (rsyncd)",
    "sudo": "Provide limited super user privileges to specific users", "triggerhappy": "triggerhappy hotkey daemon",
    "udev": "Start systemd-udevd and populate /dev", "ufw": "start firewall",
    "unattended-upgrades": "Check if unattended upgrades are being applied", "uuidd": "uuidd daemon",
}
_INIT_SCRIPT = """#!/bin/sh
### BEGIN INIT INFO
# Provides:          {provides}
# Required-Start:    $remote_fs $syslog
# Required-Stop:     $remote_fs $syslog
# Default-Start:     {start}
# Default-Stop:      {stop}
# Short-Description: {description}
### END INIT INFO

PATH=/sbin:/bin:/usr/sbin:/usr/bin
NAME={provides}
DESC="{description}"

. /lib/lsb/init-functions

case "$1" in
  start|stop|restart|force-reload)
	log_daemon_msg "Running $1 of $DESC" "$NAME"
	log_end_msg 0
	;;
  status)
	exit {status}
	;;
  *)
	echo "Usage: /etc/init.d/$NAME {{start|stop|restart|force-reload|status}}" >&2
	exit 3
	;;
esac

exit 0
"""


def _init_entries(persona):
    """(sign, name) of service --status-all on a Debian server."""
    init = _PI_INIT if persona.family == "raspios" else _UBUNTU_INIT if persona.os_id == "ubuntu" else _DEBIAN_INIT
    return tuple((entry[0], entry[2:]) for entry in init)


def _boot_script(name):
    return name.endswith(".sh") or name in ("kmod", "udev", "fake-hwclock", "alsa-utils")


def init_links(persona):
    """(link, target) of the run level directories for the scripts of init_scripts, as their
    Default-Start / Default-Stop say (insserv numbers them all 01)."""
    found = []
    for rel in init_scripts(persona):
        name = rel.rsplit("/", 1)[-1]
        if _boot_script(name):
            found.append((f"etc/rcS.d/S01{name}", rel))
        else:
            found += [(f"etc/rc{level}.d/S01{name}", rel) for level in (2, 3, 4, 5)]
            found += [(f"etc/rc{level}.d/K01{name}", rel) for level in (0, 1, 6)]
    return tuple(found)


def init_scripts(persona):
    """/etc/init.d of a Debian server: a script with its LSB header for every entry of service
    --status-all that Cowrie's tree has not (shortened; its status is the sign of the entry)."""
    if not has_dpkg(persona) or persona.kind != "server":
        return {}
    scripts = {}
    for sign, name in _init_entries(persona):
        if name in TREE_INIT:
            continue
        boot = _boot_script(name)
        scripts[f"etc/init.d/{name}"] = _INIT_SCRIPT.format(
            provides=name.removesuffix(".sh"), description=INIT_DESCRIPTIONS[name], start="S" if boot else "2 3 4 5",
            stop="" if boot else "0 1 6", status=0 if sign == "+" else 3)
    return scripts


def service_status(persona):
    """What service --status-all prints on a server."""
    if persona.family == "rhel":
        return ""
    if persona.family == "centos":
        lines = _CENTOS_STATUS
    else:
        lines = tuple(f" [ {sign} ]  {name}" for sign, name in _init_entries(persona))
    return "".join(line + "\n" for line in lines)


def sudo_list(persona):
    """What sudo -l lists on a server (sudoers_files): the Defaults of the family's sudoers and the rules
    of the login user (its group sudo / wheel, then the NOPASSWD rule of sudoers.d) and of root."""
    el = is_el(persona)
    group = "(ALL) ALL" if el else "(ALL : ALL) ALL"
    return {"defaults": [_listed_default(*entry) for entry in SUDOERS[persona.family]],
            "rules": {persona.user: [group, "(ALL) NOPASSWD: ALL"], "root": [group]}}


# programs of an apt package besides those of TOOL_PROGRAMS
APT_PROGRAMS = {"busybox": ("usr/bin/busybox",)}
# packages apt knows by name but has no candidate for (Debian 12 / 13, Ubuntu 22.04 / 24.04): yum of
#   Red Hat ("Package yum is not available, but is referred to by another package")
# names a release refers to but has no package of (apt: "is not available, but is referred to by another
#   package"; checked on debian:12 and ubuntu:24.04; jammy and trixie do not know yum at all)
APT_UNAVAILABLE = {"bookworm": ("yum",), "noble": ("yum",)}


def apt_programs(name):
    """The programs an apt install of the package puts on PATH: its tools (TOOL_PROGRAMS) and
    APT_PROGRAMS, else /usr/bin/<name>."""
    found = set(TOOL_PROGRAMS.get(name, {}).values()) | set(APT_PROGRAMS.get(name, ()))
    return ["/" + rel for rel in sorted(found)] if found else [f"/usr/bin/{name}"]


# the programs whose Cowrie command answers as the installed program does once a package manager of the
#   session installed it: the tools of TOOL_PROGRAMS and git (its version is the installed one); the
#   others (gcc, busybox: Cowrie's own versions) stay faked programs
SESSION_ANSWERS = COWRIE_TOOL_NAMES | {"git"}


def session_answers(paths):
    """The names of the programs (paths) that answer after an install in the session (SESSION_ANSWERS)."""
    return sorted({path.rsplit("/", 1)[-1] for path in paths} & SESSION_ANSWERS)


def apt_packages(persona, ident):
    """What apt knows on a Debian server: the installed packages (the release's, APT_INSTALLED), the
    versions of the packages it would install (APT_AVAILABLE), their programs (those that answer then:
    session_answers) and the packages it has no candidate for (APT_UNAVAILABLE)."""
    code = ident.release.extra["codename"]
    kernel, kernel_available = kernel_packages(persona, ident)
    installed = dict(sorted({**APT_INSTALLED[code], **ident.release.packages, **kernel}.items()))
    available = {name: version for name, version in sorted({**APT_AVAILABLE[code], **kernel_available}.items())
                 if name not in installed}
    return {"installed": installed, "available": available,
            "programs": {name: apt_programs(name) for name in available},
            "answers": session_answers(path for name in available for path in apt_programs(name)),
            "unavailable": [name for name in APT_UNAVAILABLE.get(code, ()) if name not in installed
                            and name not in available]}


# the name lists of the releases (built by docker/cowrie/tools/build_package_names.py, sources in the
#   README there): apt.txt.gz "name releases", yum.txt.gz "name", opkg.txt.gz "name releases feed"
NAMES_DIR = Path(__file__).resolve().parent / "package_names"
APT_NAME_RELEASES = {"bookworm": "b", "trixie": "t", "jammy": "j", "noble": "n"}
OPKG_NAME_RELEASES = {"21.02": "a", "22.03": "b"}


def _name_rows(name):
    with gzip.open(NAMES_DIR / name, "rt", encoding="utf-8") as f:
        return [line.split() for line in f if line.strip()]


# opkg of OpenWrt: the installed packages ("name version", opkg list-installed of the x86-64 rootfs of the
#   release without the packages of that target: kernel, kmods, grub, the disk tools; unverified: the
#   wireless packages and kmods of the ramips/mt7621 images are left out); its help; the files of opkg
OPKG_INSTALLED = {
    "21.02": """
    base-files 1452-r16847-f8282da11e busybox 1.33.2-2 ca-bundle 20211016-1 cgi-io 2022-08-10-901b0f04-21
    dnsmasq 2.85-9 dropbear 2020.81-2 firewall 2021-03-23-61db17ed-1.1 fstools 2021-01-04-c53b1882-1 fwtool
    2019-11-12-8f7fe925-1 getrandom 2020-10-25-9ef88681-2 ip6tables 1.8.7-1 iptables 1.8.7-1 jshn
    2021-05-16-b14c4688-2 jsonfilter 2018-02-04-c7e938d6-1 libblobmsg-json20210516 2021-05-16-b14c4688-2 libc
    1.1.24-3 libgcc1 8.4.0-3 libip4tc2 1.8.7-1 libip6tc2 1.8.7-1 libiwinfo-data 2022-08-19-0dad3e66-1
    libiwinfo-lua 2022-08-19-0dad3e66-1 libiwinfo20210430 2022-08-19-0dad3e66-1 libjson-c5 0.15-2
    libjson-script20210516 2021-05-16-b14c4688-2 liblua5.1.5 5.1.5-9 liblucihttp-lua 2021-06-11-3dc89af4-1
    liblucihttp0 2021-06-11-3dc89af4-1 libnl-tiny1 2020-08-05-c291088f-2 libpthread 1.1.24-3 librt 1.1.24-3
    libubox20210516 2021-05-16-b14c4688-2 libubus-lua 2021-06-30-4fc532c8-2 libubus20210630
    2021-06-30-4fc532c8-2 libuci20130104 2021-04-14-4b3db117-5 libuclient20201210 2023-04-13-007d9454-1
    libustream-wolfssl20201210 2022-01-16-868fd881-2 libwolfssl5.5.3.99a5b54a 5.5.3-stable-1 libxtables12
    1.8.7-1 logd 2020-10-25-9ef88681-2 lua 5.1.5-9 luci git-20.074.84698-ead5e81 luci-app-firewall
    git-22.089.67741-3856d50 luci-app-opkg git-22.273.29015-e01e38c luci-base git-23.093.57360-e98243e
    luci-lib-base git-20.232.39649-1f6dc29 luci-lib-ip git-20.250.76529-62505bd luci-lib-jsonc
    git-22.097.61937-bc85ba5 luci-lib-nixio git-20.234.06894-c4a4e43 luci-mod-admin-full
    git-19.253.48496-3f93650 luci-mod-network git-22.244.54918-77c916e luci-mod-status
    git-22.089.70019-d4f0b06 luci-mod-system git-23.013.73129-aa7938d luci-proto-ipv6 git-21.148.49484-14511e5
    luci-proto-ppp git-21.163.64918-6c6559a luci-ssl git-20.244.36115-e10f954 luci-theme-bootstrap
    git-22.288.45155-afd0012 mtd 26 netifd 2021-10-30-8f82742c-1 odhcp6c 2021-01-09-53f07e90-16
    odhcpd-ipv6only 2021-07-18-bc9d317f-3 openwrt-keyring 2021-02-20-49283916-2 opkg 2021-06-13-1bf042dd-2 ppp
    2.4.8.git-2020-10-03-3 ppp-mod-pppoe 2.4.8.git-2020-10-03-3 procd 2021-03-08-2cfc26f8-2 px5g-wolfssl 4.1
    rpcd 2022-02-19-8d26a1ba-1 rpcd-mod-file 2022-02-19-8d26a1ba-1 rpcd-mod-iwinfo 2022-02-19-8d26a1ba-1
    rpcd-mod-luci 20210614 rpcd-mod-rrdns 20170710 ubox 2020-10-25-9ef88681-2 ubus 2021-06-30-4fc532c8-2 ubusd
    2021-06-30-4fc532c8-2 uci 2021-04-14-4b3db117-5 uclient-fetch 2023-04-13-007d9454-1 uhttpd
    2021-03-21-15346de8-2 uhttpd-mod-ubus 2021-03-21-15346de8-2 urandom-seed 3 urngd 2020-01-21-c7f7b6b6-1
    usign 2020-05-23-f1f65026-1
""",
    "22.03": """
    base-files 1506-r20341-591b7e93d3 busybox 1.35.0-5 ca-bundle 20230311-1 cgi-io 2022-08-10-901b0f04-21
    dnsmasq 2.86-16 dropbear 2022.82-4 firewall4 2022-10-18-7ae5e14b-1 fstools 2022-06-02-93369be0-2 fwtool
    2019-11-12-8f7fe925-1 getrandom 2021-08-03-205defb5-2 jansson4 2.13.1-2 jshn 2022-05-15-d2223ef9-1
    jsonfilter 2024-01-23-594cfa86-1 libblobmsg-json20220515 2022-05-15-d2223ef9-1 libc 1.2.3-4 libgcc1
    11.2.0-4 libiwinfo-data 2022-12-15-8d158096-1 libiwinfo-lua 2022-12-15-8d158096-1 libiwinfo20210430
    2022-12-15-8d158096-1 libjson-c5 0.15-2 libjson-script20220515 2022-05-15-d2223ef9-1 liblua5.1.5 5.1.5-11
    liblucihttp-lua 2023-03-15-9b5b683f-1 liblucihttp0 2023-03-15-9b5b683f-1 libmnl0 1.0.5-1 libnftnl11
    1.2.1-2 libnl-tiny1 2021-11-21-8e0555fb-1 libpthread 1.2.3-4 librt 1.2.3-4 libubox20220515
    2022-05-15-d2223ef9-1 libubus-lua 2022-06-01-2bebf93c-1 libubus20220601 2022-06-01-2bebf93c-1
    libuci20130104 2021-10-22-f84f49f0-6 libuclient20201210 2023-04-13-007d9454-1 libucode20220812
    2022-12-02-46d93c9c-1 libustream-wolfssl20201210 2022-12-08-9217ab46-2 libwolfssl5.7.2.ee39414e
    5.7.2-stable-1 logd 2021-08-03-205defb5-2 lua 5.1.5-11 luci git-23.093.42303-d58cd69 luci-app-firewall
    git-24.067.02332-0337416 luci-app-opkg git-24.045.79537-9f5b5dd luci-base git-24.067.02332-4c1ddfb
    luci-lib-base git-20.232.39649-1f6dc29 luci-lib-ip git-23.311.79310-704a335 luci-lib-jsonc
    git-23.298.74581-7dfbbca luci-lib-nixio git-24.034.55122-b50146c luci-mod-admin-full
    git-19.253.48496-3f93650 luci-mod-network git-24.075.44908-000d7e5 luci-mod-status
    git-24.087.58505-279cf19 luci-mod-system git-24.067.02339-de5734b luci-proto-ipv6 git-23.355.78888-e047387
    luci-proto-ppp git-24.135.45225-e7a8d09 luci-ssl git-20.244.36115-e10f954 luci-theme-bootstrap
    git-24.072.69887-4219a92 mtd 26 netifd 2022-08-25-76d2d41b-1 nftables-json 1.0.2-2.1 odhcp6c
    2022-08-05-7d21e8d8-18 odhcpd-ipv6only 2023-01-02-4a673e1c-2 openwrt-keyring 2022-03-25-62471e69-3 opkg
    2022-02-24-d038e5b6-1 ppp 2.4.9.git-2021-01-04-3 ppp-mod-pppoe 2.4.9.git-2021-01-04-3 procd
    2022-06-01-7a009685-3 procd-seccomp 2022-06-01-7a009685-3 procd-ujail 2022-06-01-7a009685-3 px5g-wolfssl
    6.2 rpcd 2022-12-15-7de4820c-1 rpcd-mod-file 2022-12-15-7de4820c-1 rpcd-mod-iwinfo 2022-12-15-7de4820c-1
    rpcd-mod-luci 20210614 rpcd-mod-rrdns 20170710 ubox 2021-08-03-205defb5-2 ubus 2022-06-01-2bebf93c-1 ubusd
    2022-06-01-2bebf93c-1 uci 2021-10-22-f84f49f0-6 uclient-fetch 2023-04-13-007d9454-1 ucode
    2022-12-02-46d93c9c-1 ucode-mod-fs 2022-12-02-46d93c9c-1 ucode-mod-ubus 2022-12-02-46d93c9c-1
    ucode-mod-uci 2022-12-02-46d93c9c-1 uhttpd 2023-06-25-34a8a74d-1 uhttpd-mod-ubus 2023-06-25-34a8a74d-1
    urandom-seed 3 urngd 2023-11-01-44365eb1-1 usign 2020-05-23-f1f65026-1
""",
}
OPKG_HELP = """opkg must have one sub-command argument
usage: opkg [options...] sub-command [arguments...]
where sub-command is one of:

Package Manipulation:
\tupdate\t\t\tUpdate list of available packages
\tupgrade <pkgs>\t\tUpgrade packages
\tinstall <pkgs>\t\tInstall package(s)
\tremove <pkgs|regexp>\tRemove package(s)

Informational Commands:
\tlist\t\t\tList available packages
\tlist-installed\t\tList installed packages
\tlist-upgradable\t\tList installed and upgradable packages
\tinfo [pkg|regexp]\tDisplay all info for <pkg>
"""
OPKG_HELP_FILE = "opkg-help.txt"


def opkg_branch(ident):
    return ".".join(ident.release.os_version.split(".")[:2])


def opkg_keys(persona, ident, runtime_dir):
    """[shell] keys of the patched opkg of OpenWrt: the release and its download folders, the installed
    packages, the names of the feeds (package_names) and the help."""
    x = ident.release.extra
    pairs = OPKG_INSTALLED[opkg_branch(ident)].split()
    return {"opkg_release": ident.release.os_version, "opkg_target": x["target"], "opkg_arch": x["pkg_arch"],
            "opkg_installed": ",".join(f"{name} {version}" for name, version in zip(pairs[::2], pairs[1::2])),
            "opkg_help": f"{runtime_dir}/{OPKG_HELP_FILE}", "package_names": f"{runtime_dir}/{PACKAGE_NAMES}"}


def kernel_packages(persona, ident):
    """The packages of the persona's running kernel (uname -r) at the version of its build (uname -v):
    ({installed}, {available}). Ubuntu's cloud images have its image, modules and headers (the cloud
    image manifests of 22.04 / 24.04), Debian and Raspberry Pi OS its image; the headers (and Ubuntu's
    modules-extra) install at the same version."""
    release, build = ident.release.kernel_version, ident.release.kernel_build
    if persona.family == "ubuntu":
        abi, flavour = release.rsplit("-", 1)
        found = re.search(r"#(\S+)-Ubuntu", build)
        version = f"{abi}.{found.group(1) if found else '1'}"
        installed = {f"linux-image-{release}", f"linux-modules-{release}", f"linux-headers-{release}"}
        if flavour == "generic":
            installed.add(f"linux-headers-{abi}")
        available = {f"linux-modules-extra-{release}"}
    else:
        found = re.search(r"Debian (\S+) \(", build)
        version = found.group(1) if found else release
        installed, available = {f"linux-image-{release}"}, {f"linux-headers-{release}"}
    return ({name: version for name in installed}, {name: version for name in available})


def kernel_package_names(persona, ident):
    """The names of the packages of the persona's running kernel (kernel_packages)."""
    installed, available = kernel_packages(persona, ident)
    return set(installed) | set(available)


def package_names(persona, ident):
    """The lines of the runtime file of the names apt (Debian, Ubuntu, Raspberry Pi OS), yum 3
    (CentOS 7) or opkg (OpenWrt: "name feed") know on the persona's release: the names of the
    release's indexes, of its kernel and the packages of the persona; apt adds its virtual names
    ("name provider,..."), yum the names its packages provide ("name package"); the names apt has
    no candidate for are none of them. None for the other personas."""
    if persona.kind == "server" and has_dpkg(persona):
        letter = "r" if persona.family == "raspios" else APT_NAME_RELEASES[ident.release.extra["codename"]]
        data = apt_packages(persona, ident)
        names = ({row[0] for row in _name_rows("apt.txt.gz") if letter in row[1]} | set(data["installed"])
                 | set(data["available"]) | kernel_package_names(persona, ident)) - set(data["unavailable"])
        virtuals = [f"{row[0]} {row[2]}" for row in _name_rows("apt-virtual.txt.gz")
                    if letter in row[1] and row[0] not in names and row[0] not in data["unavailable"]]
        return sorted(names) + sorted(virtuals)
    if persona.family == "centos":
        rows = _name_rows("yum.txt.gz")
        names = {row[0] for row in rows if len(row) == 1} | set(ident.release.packages)
        return sorted(names) + sorted(f"{row[0]} {row[1]}" for row in rows if len(row) == 2 and row[0] not in names)
    if persona.family == "openwrt":
        letter = OPKG_NAME_RELEASES[opkg_branch(ident)]
        return [f"{row[0]} {row[2]}" for row in _name_rows("opkg.txt.gz") if letter in row[1]]
    if persona.family == "ubiquiti":
        # EdgeOS has no apt sources until the user sets a repository
        return []
    return None


def rpm_evr(value, minor):
    """The epoch:version-release of a table entry for the minor release (9.6)."""
    return value[minor] if isinstance(value, dict) else value


def rhel_eus(ident):
    """A RHEL that stayed on its minor release after the next one came out takes the EUS repositories."""
    r = ident.release
    return r.extra["updated"] >= RHEL_NEXT_MINOR[r.os_version_id]


def rhel_repos(ident):
    """The repositories of a registered RHEL 9: [{"id", "name", "url", "enabled"}], the enabled first."""
    eus = rhel_eus(ident)
    found = []
    for part, title, path, enabled in RHEL_REPOS:
        kind, _, variant = part.partition("-")
        label, _, rpms = title.partition(" (")
        found.append({
            "id": f"rhel-9-for-x86_64-{kind}{'-eus' if eus else ''}{'-' + variant if variant else ''}-rpms",
            "name": f"Red Hat Enterprise Linux 9 for x86_64 - {label}{' - Extended Update Support' if eus else ''}"
                    f" ({rpms or 'RPMs)'}",
            "url": f"https://cdn.redhat.com/content/{'eus' if eus else 'dist'}/rhel9/$releasever/x86_64/{path}",
            "enabled": enabled})
    return found


_REDHAT_REPO_HEAD = """#
# Certificate-Based Repositories
# Managed by (rhsm) subscription-manager
#
# *** This file is auto-generated.  Changes made here will be over-written. ***
# *** Use "subscription-manager repo-override --help" if you wish to make changes. ***
#
# If this file is empty and this system is subscribed consider
# a "yum repolist" to refresh available repos
#
"""


def redhat_repo(ident, serial):
    """/etc/yum.repos.d/redhat.repo of subscription-manager (unverified: the keys of a 9.x system, shortened
    to the repositories of rhel_repos; serial: the number of the entitlement certificate)."""
    blocks = []
    for repo in rhel_repos(ident):
        blocks.append(f"[{repo['id']}]\n"
                      f"name = {repo['name']}\n"
                      f"baseurl = {repo['url']}\n"
                      f"enabled = {int(repo['enabled'])}\n"
                      "gpgcheck = 1\n"
                      "gpgkey = file:///etc/pki/rpm-gpg/RPM-GPG-KEY-redhat-release\n"
                      "sslverify = 1\n"
                      "sslcacert = /etc/rhsm/ca/redhat-uep.pem\n"
                      f"sslclientkey = /etc/pki/entitlement/{serial}-key.pem\n"
                      f"sslclientcert = /etc/pki/entitlement/{serial}.pem\n"
                      "sslverifystatus = 1\n"
                      "metadata_expire = 86400\n"
                      f"enabled_metadata = {int(repo['enabled'])}\n")
    return _REDHAT_REPO_HEAD + "\n" + "\n".join(blocks)


def python_links(persona, ident):
    """(link, target) of the unversioned Python names: python3 -> python3.12; on CentOS 7
    python -> python2 -> python2.7 (targets from the root, as Cowrie's pickle stores them)."""
    found = tools(persona, ident)
    if "python" not in found:
        return ()
    name = python3_name(found["python"])
    if name:
        return (("usr/bin/python3", f"usr/bin/{name}"),)
    return (("usr/bin/python2", "usr/bin/python2.7"), ("usr/bin/python", "usr/bin/python2"))


def perl_archname(persona):
    if is_el(persona):
        return f"{persona.hardware_platform}-linux-thread-multi"
    return f"{TRIPLETS[persona.hardware_platform]}-thread-multi"


def shell_keys(persona, ident, files, runtime_dir):
    """[shell] keys of the patched perl.py, python.py, git.py, sudo.py, service.py, apt.py and
    yum.py (perl.py only on EdgeOS, {} on the other devices). files: the persona's files (its apt sources)."""
    found = tools(persona, ident)
    if persona.family == "openwrt":
        return opkg_keys(persona, ident, runtime_dir)
    if not found:
        return {}
    perl, year = found["perl"]
    keys = {"perl_version": perl, "perl_archname": perl_archname(persona), "perl_copyright": year}
    if persona.family == "ubiquiti":
        # apt-get of EdgeOS: apt 1.4 of Debian 9 without sources (the image names none): no name is found
        keys.update({"apt_version": ident.release.packages["apt"], "deb_arch": ident.release.extra["deb_arch"],
                     "apt_sources": "", "package_names": f"{runtime_dir}/{PACKAGE_NAMES}"})
    if persona.kind != "server":
        return keys
    sudo = sudo_version(persona, ident)
    keys.update({"python_version": found["python"], "sudo_version": sudo, "sudo_grammar": sudo_grammar(sudo),
                 "sudo_list": f"{runtime_dir}/{SUDO_LIST}", "service_status_all": f"{runtime_dir}/{SERVICE_STATUS}",
                 "systemctl_units": f"{runtime_dir}/{SYSTEMD_UNITS}"})
    if "git" in found:
        keys["git_version"] = found["git"]
    if has_dpkg(persona):
        keys.update({"apt_version": ident.release.packages["apt"], "deb_arch": deb_arch(persona),
                     "apt_sources": ",".join(f"{url} {suite}" for url, suite in apt_sources(files)),
                     "apt_packages": f"{runtime_dir}/{APT_PACKAGES}",
                     "apt_fallback": "ubuntu" if persona.os_id == "ubuntu" else "debian"})
    if has_dpkg(persona) or persona.family == "centos":
        keys["package_names"] = f"{runtime_dir}/{PACKAGE_NAMES}"
    if persona.family == "centos":
        keys["yum_plugins"] = YUM_PLUGINS
        keys["yum_installed"] = ",".join(f"{name} {evr}" for name, evr in sorted(ident.release.packages.items()))
    if persona.family == "rhel":
        keys["dnf_packages"] = f"{runtime_dir}/{DNF_PACKAGES}"
    return keys


def python_commands(persona, ident):
    """The commands of Cowrie's Python the system does not have: python on a system with Python 3
    only, python3 on CentOS 7 (Python 2 only), both on devices; git where it is not installed."""
    found = tools(persona, ident)
    if "python" not in found:
        names = ("python", "python3")
    else:
        names = ("python",) if python3_name(found["python"]) else ("python3",)
    return names + (() if "git" in found else ("git",))


def modules_line(name, size, users, deps, address):
    return f"{name} {size} {users} {','.join(deps) + ',' if deps else '-'} Live {address}\n"


# lsmod of a fresh cloud VM; (name, size on a 5.x / 6.x kernel, users, dependencies) - unverified: typical
#   lists of Ubuntu / Debian / RHEL 9 guests, the sizes of 3.10 are those of CentOS 7.9 (not page aligned)
_MODULES_DEBIAN = (("tls", 155648, 0, ()), ("binfmt_misc", 28672, 1, ()), ("nls_iso8859_1", 12288, 1, ()),
                   ("input_leds", 12288, 0, ()), ("joydev", 32768, 0, ()), ("serio_raw", 20480, 0, ()),
                   ("qemu_fw_cfg", 20480, 0, ()), ("sch_fq_codel", 24576, 3, ()), ("efi_pstore", 12288, 0, ()),
                   ("x_tables", 65536, 1, ("ip_tables",)), ("ip_tables", 32768, 0, ()),
                   ("autofs4", 57344, 2, ()), ("psmouse", 217088, 0, ()))
_MODULES_RHEL = (("rfkill", 40960, 1, ()), ("intel_rapl_msr", 20480, 0, ()), ("intel_rapl_common", 40960, 1,
                 ("intel_rapl_msr",)), ("virtio_balloon", 24576, 0, ()), ("joydev", 28672, 0, ()),
                 ("i2c_piix4", 28672, 0, ()), ("pcspkr", 16384, 0, ()), ("xfs", 2002944, 1, ()),
                 ("libcrc32c", 16384, 1, ("xfs",)), ("virtio_net", 73728, 0, ()), ("net_failover", 24576, 1,
                 ("virtio_net",)), ("failover", 16384, 1, ("net_failover",)), ("ata_generic", 16384, 0, ()),
                 ("virtio_blk", 24576, 2, ()), ("serio_raw", 20480, 0, ()), ("fuse", 176128, 1, ()))
_MODULES_CENTOS = (("sb_edac", 32034, 0, ()), ("iosf_mbi", 15582, 0, ()), ("kvm_intel", 188740, 0, ()),
                   ("kvm", 637289, 1, ("kvm_intel",)), ("irqbypass", 13503, 1, ("kvm",)),
                   ("virtio_balloon", 18015, 0, ()), ("i2c_piix4", 22401, 0, ()), ("pcspkr", 12718, 0, ()),
                   ("ip_tables", 27126, 0, ()), ("xfs", 997727, 1, ()), ("libcrc32c", 12644, 1, ("xfs",)),
                   ("virtio_net", 28063, 0, ()), ("virtio_blk", 18323, 2, ()), ("ata_piix", 35052, 0, ()),
                   ("libata", 243133, 1, ("ata_piix",)), ("serio_raw", 13434, 0, ()))
_MODULES_PI = (("cmac", 12288, 3, ()), ("algif_hash", 12288, 1, ()), ("aes_arm64", 12288, 3, ()),
               ("algif_skcipher", 12288, 1, ()), ("af_alg", 28672, 6, ("algif_hash", "algif_skcipher")),
               ("brcmfmac", 352256, 0, ()), ("brcmutil", 20480, 1, ("brcmfmac",)),
               ("cfg80211", 1028096, 1, ("brcmfmac",)), ("rfkill", 32768, 2, ("cfg80211",)),
               ("raspberrypi_hwmon", 12288, 0, ()), ("vc4", 389120, 0, ()), ("v3d", 81920, 0, ()),
               ("gpu_sched", 45056, 1, ("v3d",)), ("i2c_dev", 12288, 0, ()), ("fuse", 126976, 1, ()),
               ("ip_tables", 24576, 0, ()), ("x_tables", 45056, 1, ("ip_tables",)), ("ipv6", 573440, 24, ()))
_MODULES_NVIDIA = (("nvidia_uvm", 1789952, 0, ()), ("nvidia_drm", 122880, 0, ()),
                   ("nvidia_modeset", 1355776, 1, ("nvidia_drm",)),
                   ("nvidia", 56807424, 2, ("nvidia_uvm", "nvidia_modeset")))


# the modules of the device firmware, newest first (unverified: composites of the vendors' SDKs and public
#   lsmod listings, not taken from the images); 2.6 / 3.x kernels do not round the sizes to pages
_MODULES_DEVICE = {
    "dlink": (("ath_pktlog", 19648, 0, ()), ("umac", 588720, 0, ()), ("ath_dev", 212364, 1, ("umac",)),
              ("ath_rate_atheros", 31392, 2, ("umac", "ath_dev")),
              ("ath_hal", 406720, 3, ("umac", "ath_dev", "ath_rate_atheros")),
              ("asf", 5008, 4, ("ath_pktlog", "umac", "ath_dev", "ath_hal")),
              ("adf", 54448, 4, ("ath_pktlog", "umac", "ath_dev", "ath_hal"))),
    "tplink": (("nf_nat_pptp", 1904, 0, ()), ("nf_conntrack_pptp", 3712, 1, ("nf_nat_pptp",)),
               ("nf_nat_proto_gre", 1072, 1, ("nf_nat_pptp",)),
               ("nf_conntrack_proto_gre", 3008, 1, ("nf_conntrack_pptp",)), ("mt7628", 1083568, 1, ())),
    "zyxel": (("ufsd", 640114, 0, ()), ("jnl", 21302, 1, ("ufsd",)), ("hfsplus", 77370, 0, ()),
              ("usb_storage", 41022, 0, ()), ("fuse", 72290, 1, ()), ("nfsd", 238116, 13, ()),
              ("exportfs", 3152, 1, ("nfsd",))),
    "openwrt": (("mt7603e", 98304, 0, ()), ("mt76", 61440, 1, ("mt7603e",)),
                ("mac80211", 507904, 2, ("mt7603e", "mt76")), ("cfg80211", 286720, 3, ("mt7603e", "mt76", "mac80211")),
                ("compat", 4096, 2, ("mac80211", "cfg80211")), ("nf_conntrack", 81920, 0, ())),
    "qnap": (("fbdisk", 36864, 0, ()), ("ufsd", 991232, 0, ()), ("jnl", 36864, 1, ("ufsd",)),
             ("usb_storage", 77824, 0, ()), ("fuse", 155648, 1, ()), ("nfsd", 425984, 13, ())),
    "synology": (("synoacl_vfs", 20480, 1, ()), ("btrfs", 1015808, 1, ()), ("fuse", 94208, 1, ()),
                 ("usb_storage", 57344, 0, ()), ("loop", 28672, 2, ())),
    "ubiquiti": (("ubnt_nf_app", 12288, 0, ()), ("tdts", 507904, 1, ("ubnt_nf_app",)), ("ipt_NETFLOW", 49152, 0, ()),
                 ("imq", 12288, 0, ()), ("ubnt_platform", 36864, 0, ())),
    "ipcam": (("hi_sensor_i2c", 5884, 0, ()), ("hi_mipi_rx", 31812, 0, ()), ("hi3516cv500_isp", 193804, 0, ()),
              ("hi3516cv500_vi", 285916, 1, ("hi3516cv500_isp",)), ("hi3516cv500_sys", 67508, 2,
              ("hi3516cv500_isp", "hi3516cv500_vi")), ("hi3516cv500_base", 13060, 3,
              ("hi3516cv500_isp", "hi3516cv500_vi", "hi3516cv500_sys")),
              ("hi_osal", 160432, 6, ("hi_sensor_i2c", "hi_mipi_rx", "hi3516cv500_isp", "hi3516cv500_vi",
                                      "hi3516cv500_sys", "hi3516cv500_base"))),
}
# where a vendor kernel loads its modules (the 2.6 kernels and the firmware show the addresses to every
#   reader, no kptr_restrict): MIPS KSEG2, ARM below the kernel, the x86_64 module area
_MODULE_BASES = {"mips": 0xc0000000, "armv7l": 0xbf000000, "x86_64": 0xffffffffc0000000}


def address_width(persona):
    """Hex digits of a kernel address: 8 on the 32-bit devices, 16 on 64-bit systems."""
    return 8 if persona.hardware_platform in ("mips", "armv7l") else 16


def _device_modules(persona):
    modules = _MODULES_DEVICE[persona.family]
    width = address_width(persona)
    address, lines = _MODULE_BASES[persona.hardware_platform], []
    # loaded oldest first: the list shows the newest first, at the higher addresses
    for name, size, users, deps in reversed(modules):
        lines.append(modules_line(name, size, users, deps, f"0x{address:0{width}x}"))
        address += -(-size // 4096) * 4096
    return "".join(reversed(lines))


def proc_modules(persona, ident):
    """/proc/modules: the modules of the persona's kind of machine; a server's addresses as a
    reader without CAP_SYSLOG sees them (kptr_restrict), a device's as its vendor kernel shows them."""
    if persona.kind == "device":
        return _device_modules(persona)
    if persona.family == "centos":
        modules = _MODULES_CENTOS
    elif persona.family == "rhel":
        modules = _MODULES_RHEL
    elif persona.family == "raspios":
        modules = _MODULES_PI
    else:
        modules = _MODULES_DEBIAN
    if ident.hardware.gpus:
        modules = _MODULES_NVIDIA + modules
    return "".join(modules_line(name, size, users, deps, "0x0000000000000000")
                   for name, size, users, deps in modules)


def modules_problems(text, width=16):
    """A module named as a dependency is listed itself; the format of the kernel (addresses of
    width hex digits)."""
    out = []
    names = set()
    for line in text.splitlines():
        fields = line.split()
        if len(fields) != 6 or fields[4] != "Live" or not re.fullmatch(rf"0x[0-9a-f]{{{width}}}", fields[5]):
            out.append(f"modules: {line!r} is no /proc/modules line")
            continue
        names.add(fields[0])
        deps = [] if fields[3] == "-" else fields[3].rstrip(",").split(",")
        if not fields[2].isdigit() or int(fields[2]) < len(deps):
            out.append(f"modules: {fields[0]} is used {fields[2]} times by {len(deps)} modules")
    for line in text.splitlines():
        fields = line.split()
        if len(fields) == 6 and fields[3] != "-":
            out += [f"modules: {fields[0]} is used by {dep}, which is not loaded"
                    for dep in fields[3].rstrip(",").split(",") if dep not in names]
    return out
