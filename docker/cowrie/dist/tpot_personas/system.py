"""What of Cowrie's file system tree a persona's system has.

Cowrie's fs.pickle is a Debian 12 x86_64 container: dpkg and apt, x86_64
libraries, Python 3.11, OpenSSH, a few systemd units, runit. A persona keeps
only what its own system has (foreign_paths, the multiarch and Python renames
of render.apply_to_tree) and the versions its interpreters print (tools,
shell_keys for the patched perl.py / python.py / git.py of Cowrie)."""
import re
import time

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
    "noble": {"bind9-dnsutils": "1:9.18.28-0ubuntu0.24.04.1", "ethtool": "1:6.7-1build1", "iptables": "1.8.10-3ubuntu2", "iputils-ping": "3:20240117-1build1", "bash": "5.2.21-2ubuntu4", "coreutils": "9.4-3ubuntu6", "gzip": "1.12-1ubuntu3",
              "man-db": "2.12.0-4build2", "netcat-openbsd": "1.226-1ubuntu2", "perl": "5.38.2-3.2build2",
              "python3": "3.12.3-0ubuntu2", "sudo": "1.9.15p5-3ubuntu5", "tar": "1.35+dfsg-3build1",
              "wget": "1.21.4-1ubuntu4.1"},
    "jammy": {"bind9-dnsutils": "1:9.18.30-0ubuntu0.22.04.2", "ethtool": "1:5.16-1ubuntu0.1", "iptables": "1.8.7-1ubuntu5.2", "iputils-ping": "3:20211215-1", "bash": "5.1-6ubuntu1.1", "coreutils": "8.32-4.1ubuntu1.2", "gzip": "1.10-4ubuntu4.1",
              "man-db": "2.10.2-1", "netcat-openbsd": "1.218-4ubuntu1", "perl": "5.34.0-3ubuntu1.3",
              "python3": "3.10.6-1~22.04.1", "sudo": "1.9.9-1ubuntu2.4", "tar": "1.34+dfsg-1ubuntu0.1.22.04.2",
              "wget": "1.21.2-2ubuntu1.1"},
    "trixie": {"iputils-ping": "3:20250605-1", "bash": "5.2.37-2+b5", "coreutils": "9.7-3", "gzip": "1.13-1", "man-db": "2.13.1-1",
               "netcat-openbsd": "1.229-1", "perl": "5.40.1-6", "python3": "3.13.5-1", "tar": "1.35+dfsg-3.1",
               "wget": "1.25.0-2"},
    "bookworm": {"iputils-ping": "3:20221126-1+deb12u1", "bash": "5.2.15-2+b7", "coreutils": "9.1-1", "gzip": "1.12-1", "man-db": "2.11.2-2",
                 "netcat-openbsd": "1.219-1", "perl": "5.36.0-7+deb12u2", "python3": "3.11.2-1+b1",
                 "tar": "1.34+dfsg-1.2+deb12u1", "wget": "1.21.3-1+deb12u1"},
}
APT_AVAILABLE = {
    "noble": {"busybox": "1:1.36.1-6ubuntu3.1", "php-cli": "2:8.3+93ubuntu2", "tftp-hpa": "5.2+20150808-1.4build2", "gcc": "4:13.2.0-7ubuntu1", "hydra": "9.5-1build3", "iproute2": "6.1.0-1ubuntu6",
              "make": "4.3-4.1build2", "masscan": "2:1.3.2+ds1-1", "nmap": "7.94+git20230807.3be01efb1+dfsg-3build2",
              "screen": "4.9.1-1build1", "socat": "1.8.0.0-4build3", "tmux": "3.4-1ubuntu0.1",
              "tor": "0.4.8.10-1build2", "unzip": "6.0-28ubuntu4.1", "zip": "3.0-13build1"},
    "jammy": {"busybox": "1:1.30.1-7ubuntu3.1", "php-cli": "2:8.1+92ubuntu1", "tftp-hpa": "5.2+20150808-1.2build2", "gcc": "4:11.2.0-1ubuntu1", "hydra": "9.2-1ubuntu1", "iproute2": "5.15.0-1ubuntu2",
              "make": "4.3-4.1build1", "masscan": "2:1.3.2+ds1-1", "nmap": "7.91+dfsg1+really7.80+dfsg1-2ubuntu0.1",
              "screen": "4.9.0-1", "socat": "1.7.4.1-3ubuntu4", "tmux": "3.2a-4ubuntu0.2", "tor": "0.4.6.10-1",
              "unzip": "6.0-26ubuntu3.2", "zip": "3.0-12build2"},
    "trixie": {"bind9-dnsutils": "1:9.20.11-4", "busybox": "1:1.37.0-6", "ethtool": "1:6.14.2-1", "iptables": "1.8.11-2", "php-cli": "2:8.4+96", "tftp-hpa": "5.2+20150808-1.6", "gcc": "4:14.2.0-1", "git": "1:2.47.3-0+deb13u1", "hydra": "9.5-3", "iproute2": "6.15.0-1",
               "make": "4.4.1-2", "masscan": "2:1.3.2+ds1-2", "net-tools": "2.10-1.3", "nmap": "7.95+dfsg-3",
               "screen": "4.9.1-3", "socat": "1.8.0.3-1", "tmux": "3.5a-3", "tor": "0.4.8.16-1", "unzip": "6.0-29",
               "zip": "3.0-15"},
    "bookworm": {"bind9-dnsutils": "1:9.18.33-1~deb12u2", "busybox": "1:1.35.0-4+b3", "ethtool": "1:6.1-1", "iptables": "1.8.9-2", "php-cli": "2:8.2+93", "tftp-hpa": "5.2+20150808-1.4", "gcc": "4:12.2.0-3", "git": "1:2.39.5-0+deb12u2", "hydra": "9.4-1", "iproute2": "6.1.0-3",
                 "make": "4.3-4.1", "masscan": "2:1.3.2+ds1-1", "net-tools": "2.10-0.1", "nmap": "7.93+dfsg1-1",
                 "screen": "4.9.0-4", "socat": "1.7.4.4-2", "tmux": "3.3a-3", "tor": "0.4.7.16-1", "unzip": "6.0-28",
                 "zip": "3.0-13"},
}
# the tools Cowrie answers by name (its Python commands) and the programs of their package (Debian's name,
#   the path in the tree): a tool runs exactly where its package is installed, and is a program then;
#   iproute2 has no command of Cowrie and stays uninstalled
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
                               "php", "tftp", "unzip"})
# the tools of the Red Hat images (rpm: bind-utils, net-tools, iptables, ethtool, iputils; unverified)
EL_TOOLS = {"rhel": frozenset({"ethtool", "iputils-ping"}),
            "centos": frozenset({"ethtool", "iptables", "iputils-ping", "net-tools"})}
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
        for name, rel in TOOL_PROGRAMS[tool].items():
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


def python3_name(version):
    """python3.12 of 3.12.3; None for a Python 2."""
    major, minor = version.split(".")[:2]
    return f"python{major}.{minor}" if major == "3" else None


def python_answers(persona):
    """The commands of Cowrie's Python a server runs (not skipped) besides the interpreters:
    sudo, service and its package manager (apt of Debian, yum 3 of CentOS 7; RHEL 9's yum
    is dnf 4, which Cowrie does not emulate: a txtcmd)."""
    if persona.kind != "server":
        return ()
    answers = SERVER_COMMANDS
    if has_dpkg(persona):
        answers += APT_COMMANDS
    if persona.family == "centos":
        answers += YUM_COMMANDS
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
    paths += [command_path(command) for command in python_answers(persona)]
    if not has_dpkg(persona):
        paths.append("usr/bin/which")
        if persona.hardware_platform == "x86_64":
            paths.append(LOADER if persona.family != "centos" else CENTOS_LOADER)
    if is_el(persona):
        paths.append("usr/libexec/openssh/sftp-server")
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


def apt_packages(persona, ident):
    """What apt knows on a Debian server: the installed packages (the release's, APT_INSTALLED) and the
    versions of the packages it would install (APT_AVAILABLE)."""
    code = ident.release.extra["codename"]
    installed = dict(sorted({**APT_INSTALLED[code], **ident.release.packages}.items()))
    available = {name: version for name, version in sorted(APT_AVAILABLE[code].items()) if name not in installed}
    return {"installed": installed, "available": available}


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
    if not found:
        return {}
    perl, year = found["perl"]
    keys = {"perl_version": perl, "perl_archname": perl_archname(persona), "perl_copyright": year}
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
    if persona.family == "centos":
        keys["yum_plugins"] = YUM_PLUGINS
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
