"""What of Cowrie's file system tree a persona's system has.

Cowrie's fs.pickle is a Debian 12 x86_64 container: dpkg and apt, x86_64
libraries, Python 3.11, OpenSSH, a few systemd units, runit. A persona keeps
only what its own system has (foreign_paths, the multiarch and Python renames
of render.apply_to_tree) and the versions its interpreters print (tools,
shell_keys for the patched perl.py / python.py / git.py of Cowrie)."""
import gzip
import hashlib
import re
import time
from pathlib import Path

from .common import deb_arch
from .firmware import TPLINK_APPLETS
from .firmware import programs as firmware_programs

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
RHEL_PYTHON = {"9.4": "3.9.18", "9.5": "3.9.21", "9.6": "3.9.21"}
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
    # EdgeOS 2.0: /etc/sudoers, then sudoers.d/00timestamp and 01cfg-system of the image
    "ubiquiti": (("env_reset", None, None), ("timestamp_timeout", "=", "0"), ("syslog_goodpri", "=", "info"),
                 ("env_keep", "+=", "VYATTA_*"), ("env_keep", "+=", "UBNT_*"),
                 ("secure_path", "=", "/usr/sbin:/usr/bin:/sbin:/bin")),
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
    # sudo quotes a value with blanks (env_keep of Red Hat), not a single word (timestamp_timeout=0)
    shown = f'"{value}"' if any(c in value for c in " \t") else value
    return f'{name}{"+=" if operator == "+=" else "="}{shown}'


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
# (and the telnet server of every Debian / Ubuntu persona: inetutils-telnetd behind openbsd-inetd and tcpd,
#   versions of the refs systemd-telnet *-install-telnetd and apt-cache policy of ubuntu:24.04)
APT_INSTALLED = {
    "noble": {"systemd-timesyncd": "255.4-1ubuntu8.10", "inetutils-telnetd": "2:2.5-3ubuntu4.2", "openbsd-inetd": "0.20221205-3build4", "tcpd": "7.6.q-33", "update-inetd": "4.53", "iproute2": "6.1.0-1ubuntu6", "bind9-dnsutils": "1:9.18.28-0ubuntu0.24.04.1", "ethtool": "1:6.7-1build1", "iptables": "1.8.10-3ubuntu2", "iputils-ping": "3:20240117-1build1", "bash": "5.2.21-2ubuntu4", "coreutils": "9.4-3ubuntu6", "gzip": "1.12-1ubuntu3",
              "man-db": "2.12.0-4build2", "netcat-openbsd": "1.226-1ubuntu2", "perl": "5.38.2-3.2build2",
              "python3": "3.12.3-0ubuntu2", "sudo": "1.9.15p5-3ubuntu5", "tar": "1.35+dfsg-3build1",
              "wget": "1.21.4-1ubuntu4.1"},
    "jammy": {"systemd-timesyncd": "249.11-0ubuntu3.16", "inetutils-telnetd": "2:2.2-2ubuntu0.2", "openbsd-inetd": "0.20160825-5", "tcpd": "7.6.q-31build2", "update-inetd": "4.51", "iproute2": "5.15.0-1ubuntu2", "bind9-dnsutils": "1:9.18.30-0ubuntu0.22.04.2", "ethtool": "1:5.16-1ubuntu0.1", "iptables": "1.8.7-1ubuntu5.2", "iputils-ping": "3:20211215-1", "bash": "5.1-6ubuntu1.1", "coreutils": "8.32-4.1ubuntu1.2", "gzip": "1.10-4ubuntu4.1",
              "man-db": "2.10.2-1", "netcat-openbsd": "1.218-4ubuntu1", "perl": "5.34.0-3ubuntu1.3",
              "python3": "3.10.6-1~22.04.1", "sudo": "1.9.9-1ubuntu2.4", "tar": "1.34+dfsg-1ubuntu0.1.22.04.2",
              "wget": "1.21.2-2ubuntu1.1"},
    "trixie": {"systemd-timesyncd": "257.9-1~deb13u1", "inetutils-telnetd": "2:2.6-3+deb13u3", "openbsd-inetd": "0.20221205-3+b3", "tcpd": "7.6.q-36", "update-inetd": "4.53", "iproute2": "6.15.0-1", "iputils-ping": "3:20250605-1", "bash": "5.2.37-2+b5", "coreutils": "9.7-3", "gzip": "1.13-1", "man-db": "2.13.1-1",
               "netcat-openbsd": "1.229-1", "perl": "5.40.1-6", "python3": "3.13.5-1", "tar": "1.35+dfsg-3.1",
               "wget": "1.25.0-2"},
    "bookworm": {"systemd-timesyncd": "252.39-1~deb12u1", "inetutils-telnetd": "2:2.4-2+deb12u3", "openbsd-inetd": "0.20221205-2~deb12u1", "tcpd": "7.6.q-32", "update-inetd": "4.53", "iproute2": "6.1.0-3", "iputils-ping": "3:20221126-1+deb12u1", "bash": "5.2.15-2+b7", "coreutils": "9.1-1", "gzip": "1.12-1", "man-db": "2.11.2-2",
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
    # the telnet server of Debian / Ubuntu (dpkg -L of debian:12 after apt install inetutils-telnetd openbsd-inetd)
    "inetutils-telnetd": {"telnetd": "usr/sbin/telnetd"},
    "openbsd-inetd": {"inetd": "usr/sbin/inetd"},
    "tcpd": {"safe_finger": "usr/sbin/safe_finger", "tcpd": "usr/sbin/tcpd", "tcpdchk": "usr/sbin/tcpdchk",
             "tcpdmatch": "usr/sbin/tcpdmatch", "try-from": "usr/sbin/try-from"},
    "update-inetd": {"update-inetd": "usr/sbin/update-inetd"},
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
# the packages of the CentOS 7.9 image yum knows as installed besides the release's (iproute of @core)
#   and the telnet server and tuned of the persona (telnet-server-0.17-66.el7 of centos:7; tuned unverified)
#   and dhclient of NetworkManager (dhclient-4.2.5-83.el7.centos.2 of centos:7, refs network-dhcp)
CENTOS_BASE = {"iproute": "4.11.0-30.el7", "telnet-server": "1:0.17-66.el7", "tuned": "2.11.0-12.el7",
               "dhclient": "12:4.2.5-83.el7.centos.2", "dhcp-common": "12:4.2.5-83.el7.centos.2",
               "dhcp-libs": "12:4.2.5-83.el7.centos.2"}
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
            if tool == "iproute2" and name == "ip" and not is_el(persona):
                # Debian's iproute2: /sbin/ip -> /bin/ip (trixie: ../bin/ip), so root finds /usr/sbin/ip first
                found.append(("usr/sbin/ip", "../bin/ip" if _codename(persona, ident) == "trixie" else "/bin/ip"))
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
#   rebuilds, not read from the repositories; summaries those of the spec files, sizes typical; the versions
#   are the last builds of each minor in the AlmaLinux vault 9.4 / 9.5 / 9.6, the RHEL names without .alma):
#   installed - the packages of the cloud image besides the persona's (catalog, kernel, interpreters)
#   available - the packages bots install most that BaseOS / AppStream have (masscan, hydra, tor, screen and
#               busybox are not in RHEL 9: dnf finds no match for them)
# name: (arch, repository, summary, epoch:version-release for every minor or {minor: evr})
RHEL_MINORS = ("9.4", "9.5", "9.6")
RPM_INSTALLED = {
    "NetworkManager": ("x86_64", "baseos", "Network connection manager and user applications",
                       {"9.4": "1:1.46.0-19.el9_4", "9.5": "1:1.48.10-8.el9_5", "9.6": "1:1.52.0-9.el9_6"}),
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
               {"9.4": "1.5.7-11.el9", "9.5": "1.5.7-12.el9_5", "9.6": "1.5.7-14.el9_6"}),
    "dbus-broker": ("x86_64", "baseos", "Linux D-Bus Message Broker", "28-7.el9"),
    "dnf-plugins-core": ("noarch", "baseos", "Core Plugins for DNF",
                         {"9.4": "4.3.0-13.el9", "9.5": "4.3.0-16.el9", "9.6": "4.3.0-20.el9"}),
    "ethtool": ("x86_64", "baseos", "Settings tool for Ethernet NICs",
                {"9.4": "2:6.2-1.el9", "9.5": "2:6.2-1.el9", "9.6": "2:6.11-1.el9"}),
    "glibc": ("x86_64", "baseos", "The GNU libc libraries",
              {"9.4": "2.34-100.el9_4.4", "9.5": "2.34-125.el9_5.8", "9.6": "2.34-168.el9_6.24"}),
    "gzip": ("x86_64", "baseos", "The GNU data compression program", "1.12-1.el9"),
    "iputils": ("x86_64", "baseos", "Network monitoring tools including ping",
                {"9.4": "20210202-9.el9", "9.5": "20210202-10.el9_5", "9.6": "20210202-11.el9_6.3"}),
    "kexec-tools": ("x86_64", "baseos", "The kexec/kdump userspace component",
                    {"9.4": "2.0.27-8.el9_4.3", "9.5": "2.0.27-16.el9_5.1", "9.6": "2.0.29-5.el9_6.2"}),
    "man-db": ("x86_64", "baseos", "Tools for searching and reading man pages", "2.9.3-7.el9"),
    "rsyslog": ("x86_64", "appstream", "Enhanced system logging and kernel message trapping daemon",
                {"9.4": "8.2310.0-4.el9", "9.5": "8.2310.0-4.el9", "9.6": "8.2412.0-1.el9"}),
    "subscription-manager": ("x86_64", "baseos", "Tools and libraries for subscription and repository management",
                             {"9.4": "1.29.40-1.el9", "9.5": "1.29.42-1.el9", "9.6": "1.29.45.1-1.el9_6"}),
    "telnet-server": ("x86_64", "appstream", "The server program for the Telnet remote login protocol",
                      "1:0.17-85.el9"),
    "tuned": ("noarch", "baseos", "A dynamic adaptive system tuning daemon",
              {"9.4": "2.22.1-1.el9", "9.5": "2.24.0-2.el9_5", "9.6": "2.25.1-2.el9_6"}),
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
               {"9.4": "8.40-64.el9", "9.5": "8.40-64.el9", "9.6": "8.40-64.el9"}),
    "hostname": ("x86_64", "baseos", "Utility to set/show the host name or domain name", "3.23-6.el9"),
    "initscripts-service": ("noarch", "baseos", "Support for service command",
                            {"9.4": "10.11.6-1.el9", "9.5": "10.11.7-1.el9", "9.6": "10.11.8-4.el9"}),
    "kmod": ("x86_64", "baseos", "Linux kernel module management utilities",
             {"9.4": "28-9.el9", "9.5": "28-10.el9", "9.6": "28-10.el9"}),
    # rsyslog requires it (the BaseOS of the vault: ref final-wave almalinux-9-ffw-logrotate-vault)
    "logrotate": ("x86_64", "baseos", "Rotates, compresses, removes and mails system log files",
                  {"9.4": "3.18.0-8.el9", "9.5": "3.18.0-8.el9", "9.6": "3.18.0-9.el9"}),
    "ncurses": ("x86_64", "baseos", "Ncurses support utilities", {"9.4": "6.2-10.20210508.el9", "9.5": "6.2-10.20210508.el9", "9.6": "6.2-10.20210508.el9_6.2"}),
    "pam": ("x86_64", "baseos", "An extensible library which provides authentication for applications",
            {"9.4": "1.5.1-19.el9", "9.5": "1.5.1-22.el9_5", "9.6": "1.5.1-26.el9_6"}),
    "passwd": ("x86_64", "baseos", "An utility for setting or changing passwords using PAM", "0.80-12.el9"),
    "pciutils": ("x86_64", "baseos", "PCI bus related utilities", {"9.4": "3.7.0-5.el9", "9.5": "3.7.0-5.el9_5.1", "9.6": "3.7.0-7.el9"}),
    "procps-ng": ("x86_64", "baseos", "System and process monitoring utilities",
                  {"9.4": "3.3.17-14.el9", "9.5": "3.3.17-14.el9", "9.6": "3.3.17-14.el9"}),
    "sed": ("x86_64", "baseos", "A GNU stream text editor", "4.8-9.el9"),
    "setup": ("noarch", "baseos", "A set of system configuration and setup files", "2.13.7-10.el9"),
    "shadow-utils": ("x86_64", "baseos", "Utilities for managing accounts and shadow password files",
                     {"9.4": "2:4.9-8.el9", "9.5": "2:4.9-10.el9_5", "9.6": "2:4.9-12.el9"}),
    "util-linux": ("x86_64", "baseos", "A collection of basic system utilities",
                   {"9.4": "2.37.4-18.el9", "9.5": "2.37.4-20.el9", "9.6": "2.37.4-21.el9"}),
    # agetty of the console is util-linux-core's (ref almalinux-9-t12-owners)
    "util-linux-core": ("x86_64", "baseos", "The most essential utilities from the util-linux suite.",
                        {"9.4": "2.37.4-18.el9", "9.5": "2.37.4-20.el9", "9.6": "2.37.4-21.el9"}),
    "which": ("x86_64", "baseos", "Displays where a particular program in your path is located",
              {"9.4": "2.21-29.el9", "9.5": "2.21-29.el9", "9.6": "2.21-30.el9_6"}),
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
RHEL_PYTHON_RPM = {"9.4": "3.9.18-3.el9_4.6", "9.5": "3.9.21-1.el9_5", "9.6": "3.9.21-2.el9_6.2"}
# openssl of 9.4: the last errata the rebuilds published (Rocky / AlmaLinux vault 9.4, 2024-09-18);
#   unverified: EUS errata after 9.5 (2024-11), which the 2025-02-20 level could have
RHEL_OPENSSL = {"9.4": "1:3.0.7-28.el9_4", "9.5": "1:3.2.2-6.el9_5.1", "9.6": "1:3.2.2-6.el9_5.1"}
RHEL_PERL = {"9.4": "4:5.32.1-481.el9", "9.5": "4:5.32.1-481.el9", "9.6": "4:5.32.1-481.1.el9_6"}
RHEL_RELEASE_RPM = {"9.4": "9.4-0.5.el9", "9.5": "9.5-0.6.el9", "9.6": "9.6-0.1.el9"}
RHEL_SUDO = {"9.4": "1.9.5p2-10.el9_3", "9.5": "1.9.5p2-10.el9_3", "9.6": "1.9.5p2-10.el9_6.2"}
# name: (arch, repository, summary, evr, (download kB, installed kB), dependencies); an evr "=glibc" is
#   that of the installed package (glibc-devel goes with glibc, kernel-headers with the kernel)
RPM_AVAILABLE = {
    "bind-utils": ("x86_64", "appstream", "Utilities for querying DNS name servers",
                   {"9.4": "32:9.16.23-18.el9_4.6", "9.5": "32:9.16.23-24.el9_5.3", "9.6": "32:9.16.23-31.el9_6.2"},
                   (205, 668), ()),
    "cpp": ("x86_64", "appstream", "The C Preprocessor",
            {"9.4": "11.4.1-3.el9", "9.5": "11.5.0-5.el9_5", "9.6": "11.5.0-5.el9_5"}, (11_264, 30_720), ()),
    "gcc": ("x86_64", "appstream", "Various compilers (C, C++, Objective-C, ...)",
            {"9.4": "11.4.1-3.el9", "9.5": "11.5.0-5.el9_5", "9.6": "11.5.0-5.el9_5"}, (32_768, 90_112),
            ("cpp", "glibc-devel", "glibc-headers", "kernel-headers", "libmpc", "libxcrypt-devel")),
    "git": ("x86_64", "appstream", "Fast Version Control System",
            {"9.4": "2.43.5-1.el9_4", "9.5": "2.43.5-2.el9_5", "9.6": "2.47.3-1.el9_6"}, (51, 85),
            ("git-core", "git-core-doc", "perl-Error", "perl-Git", "perl-TermReadKey", "perl-lib")),
    "git-core": ("x86_64", "appstream", "Core package of git with minimal functionality",
                 {"9.4": "2.43.5-1.el9_4", "9.5": "2.43.5-2.el9_5", "9.6": "2.47.3-1.el9_6"}, (4_506, 22_528), ()),
    "git-core-doc": ("noarch", "appstream", "Documentation files for git-core",
                     {"9.4": "2.43.5-1.el9_4", "9.5": "2.43.5-2.el9_5", "9.6": "2.47.3-1.el9_6"}, (2_867, 17_408),
                     ()),
    "glibc-devel": ("x86_64", "appstream", "Object files for development using standard C libraries.", "=glibc",
                    (38, 30), ()),
    "glibc-headers": ("x86_64", "appstream", "Header files for development using standard C libraries.", "=glibc",
                      (450, 2_252), ()),
    "iptables-nft": ("x86_64", "baseos", "nftables compatibility for iptables, arptables and ebtables",
                     {"9.4": "1.8.10-4.el9_4", "9.5": "1.8.10-11.el9_5", "9.6": "1.8.10-11.el9_5"}, (207, 676), ()),
    "kernel-headers": ("x86_64", "appstream", "Header files for the Linux kernel for use by glibc", "=kernel",
                       (2_560, 6_451), ()),
    "libmpc": ("x86_64", "appstream", "C library for multiple precision complex arithmetic", "1.2.1-4.el9",
               (66, 160), ()),
    "libxcrypt-devel": ("x86_64", "appstream", "Development files for libxcrypt", "4.4.18-3.el9", (32, 27), ()),
    "lsof": ("x86_64", "baseos", "A utility which lists open files on a Linux/UNIX system", "4.94.0-3.el9",
             (245, 960), ()),
    "make": ("x86_64", "baseos", "A GNU tool which simplifies the build process for users", "1:4.3-8.el9",
             (541, 1_536), ()),
    "net-tools": ("x86_64", "baseos", "Basic networking tools",
                  {"9.4": "2.0-0.62.20160912git.el9", "9.5": "2.0-0.64.20160912git.el9",
                   "9.6": "2.0-0.64.20160912git.el9"}, (314, 943), ()),
    "nmap": ("x86_64", "appstream", "Network exploration tool and security scanner",
             {"9.4": "3:7.92-1.el9", "9.5": "3:7.92-3.el9", "9.6": "3:7.92-3.el9"}, (5_735, 24_576), ()),
    "nmap-ncat": ("x86_64", "appstream", "Nmap's Netcat replacement",
                  {"9.4": "3:7.92-1.el9", "9.5": "3:7.92-3.el9", "9.6": "3:7.92-3.el9"}, (229, 485), ()),
    "perl-Error": ("noarch", "appstream", "Error/exception handling in an OO-ish way", "1:0.17029-7.el9", (46, 77),
                   ()),
    "perl-Git": ("noarch", "appstream", "Perl interface to Git",
                 {"9.4": "2.43.5-1.el9_4", "9.5": "2.43.5-2.el9_5", "9.6": "2.47.3-1.el9_6"}, (38, 64), ()),
    "perl-TermReadKey": ("x86_64", "appstream", "A perl module for simple terminal control", "2.38-11.el9",
                         (40, 64), ()),
    "perl-lib": ("x86_64", "appstream", "Manipulate @INC at compile time", {"9.4": "0.65-481.el9", "9.5": "0.65-481.el9", "9.6": "0.65-481.1.el9_6"}, (15, 8), ()),
    "php-cli": ("x86_64", "appstream", "Command-line interface for PHP", {"9.4": "8.0.30-1.el9_2", "9.5": "8.0.30-1.el9_2", "9.6": "8.0.30-3.el9_6"}, (3_174, 12_288),
                ("php-common",)),
    "php-common": ("x86_64", "appstream", "Common files for PHP", {"9.4": "8.0.30-1.el9_2", "9.5": "8.0.30-1.el9_2", "9.6": "8.0.30-3.el9_6"}, (667, 5_324), ()),
    "python3-pip": ("noarch", "appstream", "A tool for installing and managing Python3 packages",
                    {"9.4": "21.2.3-8.el9", "9.5": "21.3.1-1.el9", "9.6": "21.3.1-1.el9"}, (2_048, 8_806), ()),
    "socat": ("x86_64", "appstream", "Bidirectional data relay between two data channels ('netcat++')",
              {"9.4": "1.7.4.1-5.el9_4.2", "9.5": "1.7.4.1-6.el9", "9.6": "1.7.4.1-6.el9_6.1"}, (304, 1_126), ()),
    "tcpdump": ("x86_64", "appstream", "A network traffic monitoring tool", "14:4.99.0-9.el9", (545, 1_331), ()),
    "tmux": ("x86_64", "baseos", "A terminal multiplexer", "3.2a-5.el9", (476, 1_126), ()),
    "telnet": ("x86_64", "appstream", "The client program for the Telnet remote login protocol", "1:0.17-85.el9",
               (64, 117), ()),
    "tftp": ("x86_64", "appstream", "The client for the Trivial File Transfer Protocol (TFTP)", "5.2-38.el9",
             (40, 51), ()),
    "unzip": ("x86_64", "baseos", "A utility for unpacking zip files",
              {"9.4": "6.0-56.el9", "9.5": "6.0-58.el9_5", "9.6": "6.0-58.el9_5"}, (186, 399), ()),
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
    "curl": ("curl", ""), "dbus-broker": ("dbus-broker dbus-broker-launch", ""), "diffutils": ("cmp diff diff3 sdiff", ""),
    "dnf": ("dnf dnf-3", ""), "e2fsprogs": ("chattr lsattr", _E2FS_SBIN), "ethtool": ("", "ethtool"),
    "findutils": ("find xargs", ""), "gawk": ("awk gawk", ""),
    "glibc": ("", "iconvconfig ldconfig"),
    "glibc-common": ("getconf getent iconv ldd locale localedef pldd tzselect zdump", "zic"),
    "gnupg2": ("gpgv", ""), "grep": ("egrep fgrep grep", ""), "grubby": ("", "installkernel"),
    "gzip": ("gunzip gzexe gzip uncompress zcat zcmp zdiff zegrep zfgrep zforce zgrep zless zmore znew", ""),
    "hostname": ("dnsdomainname domainname hostname nisdomainname ypdomainname", ""),
    "initscripts-service": ("", "service"), "iproute": ("", "bridge ip ss tc"), "iputils": ("ping", ""), "kexec-tools": ("kdumpctl", ""),
    "kmod": ("kmod", "lsmod"), "logrotate": ("", "logrotate"), "ncurses": ("captoinfo clear infocmp infotocap reset tabs tic toe tput tset", ""),
    "NetworkManager": ("nm-online", "NetworkManager"),
    "openssh": ("ssh-keygen", ""),
    "openssh-clients": ("scp sftp slogin ssh ssh-add ssh-agent ssh-copy-id ssh-keyscan", ""),
    "openssh-server": ("", "sshd"),
    "pam": ("", "faillock mkhomedir_helper pam_namespace_helper pam_timestamp_check pwhistory_helper unix_chkpwd "
                "unix_update"),
    "passwd": ("passwd", ""), "pciutils": ("lspci", ""), "perl-interpreter": ("perl", ""),
    "procps-ng": ("free pgrep pidof pidwait pkill pmap ps pwdx skill slabtop snice tload top uptime vmstat w watch",
                  "sysctl"),
    "python3": ("pydoc3 pydoc3.9 python3 python3.9", ""), "rpm": ("rpm", ""), "rsyslog": ("", "rsyslogd"),
    "sed": ("sed", ""), "shadow-utils": ("chage expiry faillog gpasswd lastlog newgrp sg", _SHADOW_SBIN),
    "subscription-manager": ("rhsmcertd", ""), "telnet-server": ("", "in.telnetd"), "tuned": ("", "tuned"),
    "dhclient": ("", "dhclient dhclient-script"),
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
    "sudo": ("etc/sudoers",), "openssh-server": ("etc/ssh/sshd_config",),
    # the daemons of ps below /usr/lib and /usr/libexec (refs t12 almalinux-9-t12-owners, centos-7-t12-owners)
    "systemd": ("usr/lib/systemd/systemd", "usr/lib/systemd/systemd-journald", "usr/lib/systemd/systemd-logind"),
    "systemd-udev": ("usr/lib/systemd/systemd-udevd",), "NetworkManager": ("usr/libexec/nm-dhcp-helper",),
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
# CentOS 7: bash has no rbash (centos:7; its openssh-clients has slogin, its sysvinit-tools killall5)
CENTOS_DEBIAN_PROGRAMS = ("usr/bin/rbash",)
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
                "+ kmod", "- open-iscsi", "+ openbsd-inetd", "- plymouth", "- plymouth-log", "+ procps", "- rsync", "+ ssh",
                "+ udev", "+ ufw", "+ unattended-upgrades", "- uuidd")
_DEBIAN_INIT = ("+ cron", "+ dbus", "- hwclock.sh", "+ kmod", "+ openbsd-inetd", "+ procps", "+ ssh", "- sudo", "+ udev")
_PI_INIT = ("- alsa-utils", "+ avahi-daemon", "+ bluetooth", "- console-setup.sh", "+ cron", "+ dbus",
            "+ dphys-swapfile", "+ fake-hwclock", "- hwclock.sh", "- keyboard-setup.sh", "+ kmod", "+ openbsd-inetd",
            "+ procps",
            "- rsync", "+ ssh", "- sudo", "+ triggerhappy", "+ udev")
_CENTOS_STATUS = ("netconsole module not loaded", "Configured devices:", "lo eth0", "Currently active devices:",
                  "lo eth0")
# the systemd units behind an init script of service --status-all whose name is not the unit's
INIT_UNITS = {"kmod": ("kmod-static-nodes.service",), "procps": ("systemd-sysctl.service",),
              "openbsd-inetd": ("inetd.service",),
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
# apt, apt-get and the patched dpkg, dpkg-query and apt-cache of the session's package state
APT_COMMANDS = ("apt", "apt-get", "dpkg", "dpkg-query", "apt-cache")
# yum 3 and the patched rpm of the session's package state
YUM_COMMANDS = ("yum", "rpm")
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
        removed = EL_DEBIAN_PROGRAMS + (RHEL_DEBIAN_PROGRAMS if persona.family == "rhel" else CENTOS_DEBIAN_PROGRAMS)
        paths += removed + el_debian_manpages(removed)
    # a program of the firmware image is its own (the scp of dropbear on the Archer C7)
    own = firmware_programs(persona) or {}
    return tuple(p for p in paths if p not in own)


# the languages of the manual pages of Cowrie's tree
MAN_LANGUAGES = ("", "da", "de", "es", "fr", "it", "ja", "nl", "pl", "pt", "ru", "sl", "sv", "tr")


def el_debian_manpages(programs):
    """The manual pages of Debian's own programs (and ucf's configuration) a Red Hat system has not, with the
    links of Debian's alternatives to them (awk.1.gz -> mawk.1.gz, sh.1.gz -> dash.1.gz)."""
    found = ["usr/share/man/man5/ucf.conf.5.gz", "usr/share/man/man1/awk.1.gz", "usr/share/man/man1/sh.1.gz"]
    for rel in programs:
        name = rel.rsplit("/", 1)[-1]
        if not rel.startswith(("usr/bin/", "usr/sbin/")):
            continue
        section = "8" if rel.startswith("usr/sbin/") or name == "run-parts" else "1"
        for language in MAN_LANGUAGES:
            folder = f"usr/share/man/{language + '/' if language else ''}man{section}"
            found.append(f"{folder}/{name}.{section}.gz" if not name.endswith("*") else f"{folder}/{name}")
    return tuple(found)


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


# coreutils of the releases without a coreutils package in the catalog (df --version); DSM 7.2: 8.30, the
#   coreutils-8.x of Synology's GPL sources of 7.2-72806 (PACKAGE_VERSION of its configure and its NEWS; the
#   .tarball-version and ChangeLog left in it still name 8.24)
COREUTILS = {"rhel": "8.32", "centos": "8.22", "stretch": "8.26", "synology": "8.30"}
# the year of the copyright line of `df --version` per coreutils release
COREUTILS_YEARS = {"8.22": "2013", "8.26": "2016", "8.28": "2017", "8.30": "2018", "8.31": "2019", "8.32": "2020",
                   "9.1": "2022", "9.4": "2023", "9.5": "2024", "9.7": "2025"}


# the devices whose firmware has GNU coreutils (their df, procps top)
COREUTILS_DEVICES = ("synology",)


def coreutils_version(persona, ident):
    """The GNU coreutils of a server or of a device with coreutils (its release's package, else
    COREUTILS)."""
    code = "synology" if persona.family == "synology" else _codename(persona, ident)
    package = {**APT_INSTALLED.get(code, {}), **ident.release.packages}.get("coreutils", "")
    return COREUTILS.get(code) or _upstream(package) or "9.1"


# The applets of the firmware's BusyBox, from the links of its root file system (read 2026-10-13
#   from the OpenWrt 21.02.7 / 22.03.7 ramips sysupgrade images and the EdgeRouter X
#   2.0.9-hotfix.2 image); the Archer C7 v5 has the applet table of its own BusyBox
#   (firmware.TPLINK_APPLETS); the other firmware keeps the shared list (unverified).
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
# the C library of the firmware (the texts of option errors): ld-musl of OpenWrt, ld-uClibc 0.9.33.2 of the
#   Archer C7, glibc (libc.so.6) of EdgeOS, read from their BusyBox; unverified: the others
FIRMWARE_LIBC = {"openwrt": "musl", "tplink": "uclibc", "ubiquiti": "glibc", "dlink": "uclibc", "ipcam": "uclibc",
                 "zyxel": "glibc", "qnap": "glibc"}
# where BusyBox installs the applets (the paths of the OpenWrt image)
APPLET_PATHS = {"wc": "usr/bin/wc", "sort": "usr/bin/sort", "seq": "usr/bin/seq"}
TEXT_TOOLS = ("wc", "sort", "seq")


def busybox_applets(persona, ident):
    """The applets of the persona's BusyBox, in the order of its help."""
    if persona.family == "tplink":
        # the applet table of the BusyBox of the Archer C7 v5 images (firmware.TPLINK_APPLETS)
        return list(TPLINK_APPLETS)
    if persona.family == "openwrt":
        text = _BB_OPENWRT if ident.release.label.startswith("21.") else _BB_OPENWRT.replace("bunzip2, bzcat, ", "")
    elif persona.family == "ubiquiti":
        text = _BB_EDGEOS
    else:
        text = BB_SHARED
    return text.split(", ")


# the device firmware with util-linux and coreutils (EdgeOS: Debian 9; DSM: util-linux-2.x and coreutils-8.x
#   of Synology's GPL sources of 7.2-72806)
UTIL_LINUX_DEVICES = frozenset({"ubiquiti", "synology"})


def cpu_commands(persona, ident):
    """(lscpu, nproc): whether the system has them. A server has util-linux and coreutils; a device
    with util-linux (UTIL_LINUX_DEVICES) both, any other only nproc of its BusyBox (an applet from
    1.27, coreutils/nproc.c; BusyBox has no lscpu up to 1.36) when the applet is in its list."""
    if persona.kind == "server" or persona.family in UTIL_LINUX_DEVICES:
        return True, True
    found = re.search(r"BusyBox v(\d+)\.(\d+)", ident.release.extra.get("busybox", ""))
    new = bool(found) and (int(found.group(1)), int(found.group(2))) >= (1, 27)
    return False, new and "nproc" in busybox_applets(persona, ident)


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


# the texts of rm, cp, mv, touch, rmdir and mkdir (tools/build_fs_texts.py from the recordings of the real tools)
FS_TEXTS = Path(__file__).resolve().parent / "fs_texts.json"
# GNU: the recording of the persona's coreutils (version, Red Hat build, locale): 8.30 of DSM in the POSIX locale
#   is that of debian:10, 9.1 in en_GB.UTF-8 of Raspberry Pi OS that of debian:12 with en_GB.UTF-8
FS_GNU_VARIANTS = {"8.22": "centos_7", "8.30": "debian_10", "8.32": "ubuntu_22.04", "9.1": "debian_12",
                   "9.4": "ubuntu_24.04", "9.7": "debian_13"}
# BusyBox: the firmware's own BusyBox where it was recorded (under qemu-user), the D-Link's 1.14.1 derived from
#   the Archer C7 v5 build and the 1.14.1 sources; unverified: the NAS326, the IP camera and QTS, whose
#   firmware could not be run, take the minimal build of the Archer C7 v5 (no CONFIG_DESKTOP, as
#   busybox_desktop assumes of them; the texts of their option errors follow their own text_libc)
FS_BUSYBOX_VARIANTS = {"tplink": "fw_tplink_c7v5", "ubiquiti": "fw_edgeos_209hf5", "dlink": "src_busybox_1.14.1",
                       "zyxel": "fw_tplink_c7v5", "ipcam": "fw_tplink_c7v5", "qnap": "fw_tplink_c7v5"}
# the families whose BusyBox variant stands in for a build of another C library (see FS_BUSYBOX_VARIANTS)
FS_STAND_INS = frozenset({"zyxel", "ipcam", "qnap"})


def fs_variant(persona, ident):
    """The variant of fs_texts.json the persona's file commands answer with."""
    text = text_keys(persona, ident)
    if text["text_style"] == "gnu":
        if is_el(persona) and text["text_version"] == "8.32":
            return "rockylinux_9"
        if persona.family == "raspios":
            return "debian_12_en"
        return FS_GNU_VARIANTS.get(text["text_version"], "debian_12")
    if persona.family == "openwrt":
        return "fw_openwrt_21.02.7" if ident.release.label.startswith("21.") else "fw_openwrt_22.03.7"
    return FS_BUSYBOX_VARIANTS.get(persona.family, "fw_tplink_c7v5")


def fs_keys(persona, ident):
    """[shell] keys of the patched rm, cp, mv, touch, rmdir and mkdir: the file of their texts and the variant."""
    return {"fs_texts": str(FS_TEXTS), "fs_variant": fs_variant(persona, ident)}


def df_table(persona, ident, filesystems):
    """What the patched df reads ([shell] df_table): the file system table of the persona, the
    style of df (GNU coreutils on servers and the devices with coreutils, else BusyBox) and its
    version (coreutils of the release, the BusyBox of the firmware: 1.24.1 of QTS 5.2.3 and coreutils 8.30
    of DSM 7.2 from the vendors' GPL sources)."""
    data = {"filesystems": filesystems}
    if persona.kind == "server" or persona.family in COREUTILS_DEVICES:
        version = coreutils_version(persona, ident)
        text = text_keys(persona, ident)
        data.update(collate=text["text_collate"], **({"packager": text["text_packager"]} if "text_packager" in text
                                                     else {}))
        data.update(style="gnu", coreutils=version, coreutils_year=COREUTILS_YEARS.get(version, "2022"),
                    el=persona.family in ("rhel", "centos"))
    else:
        banner = ident.release.extra.get("busybox", "")
        found = re.match(r"BusyBox v(\d+)\.(\d+)\.(\d+)", banner)
        data.update(style="busybox", banner=banner, libc=FIRMWARE_LIBC.get(persona.family, "glibc"),
                    busybox=[int(x) for x in found.groups()] if found else [1, 24, 1])
    return data


# awk of the releases (unverified: the gawk of DSM, which Synology's GPL sources of 7.2-72806 do not list,
#   mawk of EdgeOS 2)
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
    if persona.family == "ubiquiti":
        # EdgeOS 2.0: sudo and systemd of Debian 9
        return ("sudo", "systemctl")
    if persona.family == "synology":
        return ("systemctl",)
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


def command_path(name, persona=None):
    if name == "systemctl" and persona is not None and persona.family == "ubiquiti":
        return "bin/systemctl"  # Debian 9 before the /usr merge
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
    paths += [command_path(command, persona) for command in python_answers(persona)
              if persona.family != "rhel" or command not in ("dnf", "dnf-3", "yum")]
    if has_systemd(persona):
        # journalctl of systemd (bin/ before the /usr merge of EdgeOS)
        paths.append("bin/journalctl" if persona.family == "ubiquiti" else "usr/bin/journalctl")
    if not has_dpkg(persona):
        paths.append("usr/bin/which")
        if persona.hardware_platform == "x86_64":
            paths.append(LOADER if persona.family != "centos" else CENTOS_LOADER)
    if is_el(persona):
        paths += ["usr/libexec/openssh/sftp-server", "usr/bin/gawk"]
    if persona.family == "rhel":
        paths.append("usr/bin/pidof")
    # the telnet daemon of a server: inetutils' telnetd behind inetd and tcpd (Debian, Ubuntu), in.telnetd
    #   of telnet-server (Red Hat, CentOS)
    if persona.kind == "server" and is_el(persona):
        # (Debian's telnetd, inetd and tcpd are programs of their packages: TOOL_PROGRAMS)
        paths.append("usr/sbin/in.telnetd")
        paths += EL_DAEMON_PROGRAMS.get(persona.family, ())
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
# /sbin/installkernel of grubby: 8.28-26.el7 of CentOS 7 (2754 bytes) and the wrapper of 8.40-64.el9 of
#   RHEL 9 (323 bytes; AlmaLinux 9.4 vault)
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
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
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
      mv $INSTALL_PATH/$KERNEL_NAME-$KERNEL_VERSION \\
              $INSTALL_PATH/$KERNEL_NAME-$KERNEL_VERSION.old;
fi

if [ ! -L $INSTALL_PATH/$KERNEL_NAME ]; then
    if [ -e $INSTALLPATH/$KERNEL_NAME ]; then 
	mv $INSTALL_PATH/$KERNEL_NAME $INSTALL_PATH/$KERNEL_NAME.old
    fi
fi

if [ -f $INSTALL_PATH/System.map-$KERNEL_VERSION ]; then 
      mv $INSTALL_PATH/System.map-$KERNEL_VERSION \\
              $INSTALL_PATH/System.map-$KERNEL_VERSION.old; 
fi

if [ ! -L $INSTALL_PATH/System.map ]; then
    if [ -e $INSTALLPATH/System.map ]; then 
	mv $INSTALL_PATH/System.map $INSTALL_PATH/System.map.old
    fi
fi
ln -sf ${RELATIVE_PATH}$INSTALL_PATH/System.map-$KERNEL_VERSION $LINK_PATH/System.map

cat $BOOTIMAGE > $INSTALL_PATH/$KERNEL_NAME-$KERNEL_VERSION
cp $MAPFILE $INSTALL_PATH/System.map-$KERNEL_VERSION

ln -fs ${RELATIVE_PATH}$INSTALL_PATH/$KERNEL_NAME-$KERNEL_VERSION $LINK_PATH/$KERNEL_NAME
ln -fs ${RELATIVE_PATH}$INSTALL_PATH/System.map-$KERNEL_VERSION $LINK_PATH/System.map

if [ -n "$cfgLoader" ] && [ -x /sbin/new-kernel-pkg ]; then
	if [ -n "$(which dracut 2>/dev/null)" ]; then
		new-kernel-pkg --mkinitrd --dracut --host-only --depmod --install --kernel-name $KERNEL_NAME $KERNEL_VERSION
	else
		new-kernel-pkg --mkinitrd --depmod --install --kernel-name $KERNEL_NAME $KERNEL_VERSION
	fi
	new-kernel-pkg --rpmposttrans --kernel-name $KERNEL_NAME $KERNEL_VERSION
fi
"""
EL9_INSTALLKERNEL = """#!/bin/bash
if [[ -x /usr/libexec/installkernel/installkernel ]] ; then
	exec /usr/libexec/installkernel/installkernel "${@}"
elif [[ -x /usr/libexec/installkernel/installkernel-bls ]] ; then
	exec /usr/libexec/installkernel/installkernel-bls "${@}"
fi
echo "installkernel is not installed correctly." >>/dev/stderr
exit 1
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
             "usr/sbin/installkernel": EL9_INSTALLKERNEL if rhel else EL_INSTALLKERNEL,
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


def busybox_desktop(persona):
    """Whether the persona's BusyBox is built with CONFIG_DESKTOP, which gives ls -l of a folder its total line
    from 1.16 (coreutils/ls.c 1.16.0 to 1.36.1; busybox:1.26.2 and 1.27.2 have it). None of the persona builds
    has it: 1.14.1 of the DIR-859 has no total line, the Archer C7 v5 (recorded), the NAS326 (no total format
    in its BusyBox) and OpenWrt 21.02 / 22.03 (recorded) are built without; QTS and the camera are taken as
    those (unverified). None without BusyBox ash."""
    if persona.family not in ASH_FAMILIES:
        return None
    return False


def shell_style(persona):
    """(the shell's name, how it says a command is missing): BusyBox ash prints "-ash: x: not found",
    bash and vbash "-bash: x: command not found"; the name is that of the login shell."""
    return persona.shell.rsplit("/", 1)[-1], "ash" if persona.family in ASH_FAMILIES else "bash"


def history_sizes(persona, release):
    """(root, other users): the lines the login shell keeps in its history (HISTSIZE).
    Ubuntu's /root/.bashrc (base-files) and /etc/skel/.bashrc set 1000; Debian's /root/.bashrc sets
    none (bash's 500), its /etc/skel/.bashrc 1000 (Raspberry Pi OS as Debian); EdgeOS 10000: the .bashrc
    of root and of its skeleton source /etc/bash_completion.d/vyatta-cfg, which exports HISTSIZE=10000
    (the ER-e50 v2.0.9-hotfix.7 image; the line editor holds at most 1000 of them, tpot_history_size); RHEL and
    CentOS set 1000 in /etc/profile; DSM is taken as bash's 500 (unverified).
    BusyBox ash keeps FEATURE_EDITING_HISTORY lines: 15 up to 1.16, 255 from 1.17 (the defaults of
    libbb/Config.in / Config.src; the vendors' configs are unverified), OpenWrt's build 256."""
    if persona.family in ("ubuntu", "rhel", "centos"):
        return 1000, 1000
    if persona.family == "ubiquiti":
        return 10000, 10000
    if persona.family in ("debian", "raspios"):
        return 500, 1000
    if persona.family == "openwrt":
        return 256, 256
    if persona.family in ASH_FAMILIES:
        found = re.search(r"BusyBox v(\d+)\.(\d+)", release.extra.get("busybox", ""))
        size = 15 if found and (int(found.group(1)), int(found.group(2))) < (1, 17) else 255
        return size, size
    return 500, 500


def history_controls(persona):
    """(root, other users): HISTCONTROL of the login shell (the patched line editor, [shell]
    history_control). Ubuntu's /root/.bashrc ignoredups:ignorespace and /etc/skel/.bashrc ignoreboth;
    Debian's /root/.bashrc none, its /etc/skel/.bashrc ignoreboth (Raspberry Pi OS as Debian); EdgeOS
    none: the .bashrc of root and of its skeleton source /etc/bash_completion.d/vyatta-cfg, which exports
    an empty HISTCONTROL (the ER-e50 v2.0.9-hotfix.7 image); RHEL and CentOS ignoredups of /etc/profile
    (an SSH login runs a login shell); BusyBox ash keeps no line that repeats the one before
    (ignoredups), but keeps a line with a leading space (busybox 1.27.2, 1.31.1, 1.36.1); DSM none
    (unverified)."""
    if persona.family == "ubuntu":
        return "ignoredups:ignorespace", "ignoreboth"
    if persona.family in ("debian", "raspios"):
        return "", "ignoreboth"
    if persona.family in ("rhel", "centos") or persona.family in ASH_FAMILIES:
        return "ignoredups", "ignoredups"
    return "", ""


def history_file_sizes(persona, release):
    """(root, other users): HISTFILESIZE of an interactive bash. Ubuntu's /root/.bashrc and /etc/skel/.bashrc
    set 2000 (ubuntu:24.04), Debian's /etc/skel/.bashrc 2000 and its /root/.bashrc none (debian:12;
    Raspberry Pi OS as Debian); EdgeOS 10000 for both, from vyatta-cfg (the ER-e50 image); without one
    bash takes the HISTSIZE after its startup files (Red Hat, Debian's root, DSM)."""
    if persona.family in ("ubuntu", "ubiquiti"):
        return (2000, 2000) if persona.family == "ubuntu" else (10000, 10000)
    sizes = history_sizes(persona, release)
    if persona.family in ("debian", "raspios"):
        return sizes[0], 2000
    return sizes


def history_exports(persona):
    """The history variables the login exports: /etc/profile of Red Hat exports HISTSIZE and HISTCONTROL
    (centos:7, almalinux:9), vyatta-cfg of EdgeOS HISTSIZE, HISTFILESIZE, HISTCONTROL (empty) and
    HISTTIMEFORMAT (the ER-e50 image); the .bashrc of Debian and Ubuntu sets them as shell variables only."""
    if persona.family == "ubiquiti":
        return "HISTSIZE", "HISTFILESIZE", "HISTCONTROL", "HISTTIMEFORMAT"
    return ("HISTSIZE", "HISTCONTROL") if persona.family in ("rhel", "centos") else ()


def history_time_format(persona):
    """HISTTIMEFORMAT of the login: '%FT%T%z ' of vyatta-cfg on EdgeOS (the ER-e50 image; `history` then
    writes the time of each line), none elsewhere."""
    return "%FT%T%z " if persona.family == "ubiquiti" else ""


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
    "openbsd-inetd": "Start or stop the inetd daemon.",
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
    if persona.family == "ubiquiti":
        return ()   # systemd starts the services of EdgeOS
    for rel in init_scripts(persona):
        name = rel.rsplit("/", 1)[-1]
        if _boot_script(name):
            found.append((f"etc/rcS.d/S01{name}", rel))
        else:
            found += [(f"etc/rc{level}.d/S01{name}", rel) for level in (2, 3, 4, 5)]
            found += [(f"etc/rc{level}.d/K01{name}", rel) for level in (0, 1, 6)]
    return tuple(found)


# /etc/init.d of EdgeOS 2.0.9 (the squashfs of ER-e50.v2.0.9-hotfix.7; hotfix.2 taken as the same,
#   unverified); README and skeleton are no scripts and left out. EdgeOS boots with systemd: its rc
#   directories hold no links
EDGEOS_INIT = ("atd", "avahi-daemon", "bootlogs", "bootmisc.sh", "checkfs.sh", "checkroot-bootclean.sh",
               "checkroot.sh", "cron", "dbus", "ddclient", "dhcpd", "dhcpdv6", "dnsmasq", "ebtables", "halt",
               "hostname.sh", "hwclock.sh", "ipsec", "killprocs", "kmod", "linux-igd", "miniupnpd", "motd",
               "mountall-bootclean.sh", "mountall.sh", "mountdevsubfs.sh", "mountkernfs.sh", "mountnfs-bootclean.sh",
               "mountnfs.sh", "netplug", "networking", "ntp", "openvpn", "pmacct", "pppd-dns", "pppoe-server", "pptpd",
               "procps", "radvd", "rc", "rc.local", "rcS", "reboot", "rmnologin", "rsyslog", "sendsigs", "single",
               "snmpd", "ssh", "sudo", "udapi-bridge", "udev", "umountfs", "umountnfs.sh", "umountroot", "urandom",
               "uuidd", "vyatta-dhcp3-relay", "vyatta-dhcp3-server", "vyatta-keepalived", "vyatta-ppp",
               "vyatta-quagga", "wide-dhcpv6-client", "xl2tpd")


def init_scripts(persona):
    """/etc/init.d of a Debian server: a script with its LSB header for every entry of service
    --status-all that Cowrie's tree has not (shortened; its status is the sign of the entry). EdgeOS:
    the scripts of its image (EDGEOS_INIT), as such stand-ins."""
    if persona.family == "ubiquiti":
        return {f"etc/init.d/{name}": _INIT_SCRIPT.format(
            provides=name.removesuffix(".sh"), description=INIT_DESCRIPTIONS.get(name, name.removesuffix(".sh")),
            start="S" if _boot_script(name) else "2 3 4 5", stop="" if _boot_script(name) else "0 1 6", status=0)
            for name in EDGEOS_INIT if name not in TREE_INIT}
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
    if "etc/init.d/openbsd-inetd" in scripts:
        # the script of the package (2444 bytes in debian:12, debian:13, ubuntu:22.04 and ubuntu:24.04)
        scripts["etc/init.d/openbsd-inetd"] = OPENBSD_INETD_INIT
    return scripts


OPENBSD_INETD_INIT = r"""#!/bin/sh -e
### BEGIN INIT INFO
# Provides:          openbsd-inetd
# Required-Start:    $local_fs $remote_fs
# Required-Stop:     $local_fs $remote_fs
# Should-Start:      $syslog
# Should-Stop:       $syslog
# Default-Start:     2 3 4 5
# Default-Stop:      0 1 6
# Short-Description: Start or stop the inetd daemon.
### END INIT INFO

DAEMON=/usr/sbin/inetd

[ -x $DAEMON -a -e /etc/inetd.conf ] || exit 0

[ -e /etc/default/openbsd-inetd ] && . /etc/default/openbsd-inetd

. /lib/lsb/init-functions

checkportmap () {
  if ! grep -v -s "^ *#" /etc/inetd.conf | grep -q -s 'rpc/'; then
    return 0
  fi

  if [ ! -x /usr/bin/rpcinfo ]; then
    log_action_msg "WARNING: rpcinfo not available - RPC services may be unavailable!"
    log_action_msg "         (Commenting out the rpc services in inetd.conf will"
    log_action_msg "         disable this message)"
  elif ! /usr/bin/rpcinfo -u localhost portmapper >/dev/null 2>&1; then
    log_action_msg "WARNING: portmapper inactive - RPC services unavailable!"
    log_action_msg "         (Commenting out the rpc services in inetd.conf will"
    log_action_msg "         disable this message)"
  fi
} 

checknoservices () {
    if ! grep -q "^[[:alnum:][/]" /etc/inetd.conf; then
	log_action_msg "Not starting internet superserver: no services enabled"
	exit 0
    fi
}

case "$1" in
    start)
	checknoservices
        checkportmap
	log_daemon_msg "Starting internet superserver" "inetd"
	start-stop-daemon --start --quiet --pidfile /var/run/inetd.pid \
	    --oknodo --exec $DAEMON -- $OPTIONS
	log_end_msg 0
	;;
    stop)
	log_daemon_msg "Stopping internet superserver" "inetd"
	start-stop-daemon --stop --quiet --pidfile /var/run/inetd.pid \
	    --oknodo
	log_end_msg 0
	;;
    reload|force-reload)
	log_daemon_msg "Reloading internet superserver" "inetd"
	start-stop-daemon --stop --quiet --pidfile /var/run/inetd.pid \
	    --oknodo --signal 1
	log_end_msg 0
	;;
    restart)
	checkportmap
	log_daemon_msg "Restarting internet superserver" "inetd"
	start-stop-daemon --stop --quiet --pidfile /var/run/inetd.pid \
	    --oknodo
	checknoservices
	sleep 1
	start-stop-daemon --start --quiet --pidfile /var/run/inetd.pid \
	    --exec $DAEMON -- $OPTIONS
	log_end_msg 0
	;;
    status)
	status_of_proc -p /var/run/inetd.pid $DAEMON inetd && exit 0 || exit $?
	;;
    *)
	echo "Usage: /etc/init.d/openbsd-inetd {start|stop|reload|force-reload|restart|status}"
	exit 2
	;;
esac

exit 0

"""


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
    if persona.family == "ubiquiti":
        # an admin of EdgeOS is in the groups sudo and users (01cfg-system: %sudo ALL=NOPASSWD: ALL,
        #   %users ALL=NOPASSWD: /opt/vyatta/bin/sudo-users/); root's rule of /etc/sudoers
        return {"defaults": [_listed_default(*entry) for entry in SUDOERS[persona.family]],
                "rules": {persona.user: ["(root) NOPASSWD: ALL", "(root) NOPASSWD: /opt/vyatta/bin/sudo-users/"],
                          "root": ["(ALL) ALL"]}}
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


# the files of the daemons of ps as dpkg -S names them (refs t12 *-own-dpkg-S: dpkg records /sbin/init on 22.04 and
#   Debian 12, /usr/sbin/init on 24.04 and Debian 13), per release; Raspberry Pi OS has Debian 12's and those of its
#   own daemons
_DEB12_DAEMONS = {"systemd-sysv": ("/sbin/init",), "systemd": ("/lib/systemd/systemd-journald",
                  "/lib/systemd/systemd-networkd", "/lib/systemd/systemd-logind"), "udev": ("/lib/systemd/systemd-udevd",),
                  "systemd-timesyncd": ("/lib/systemd/systemd-timesyncd",), "dbus-daemon": ("/usr/bin/dbus-daemon",),
                  "cron": ("/usr/sbin/cron",), "openssh-server": ("/usr/sbin/sshd",), "openbsd-inetd": ("/usr/sbin/inetd",),
                  "inetutils-telnetd": ("/usr/sbin/telnetd",), "util-linux": ("/sbin/agetty",), "bash": ("/bin/bash",),
                  "tcpd": ("/usr/sbin/tcpd",)}
DEB_DAEMON_FILES = {
    "noble": {"systemd-sysv": ("/usr/sbin/init",), "systemd": ("/usr/lib/systemd/systemd-journald",
              "/usr/lib/systemd/systemd-networkd", "/usr/lib/systemd/systemd-logind"),
              "udev": ("/usr/lib/systemd/systemd-udevd",), "systemd-timesyncd": ("/usr/lib/systemd/systemd-timesyncd",),
              "systemd-resolved": ("/usr/lib/systemd/systemd-resolved",), "dbus-daemon": ("/usr/bin/dbus-daemon",),
              "rsyslog": ("/usr/sbin/rsyslogd",), "cron": ("/usr/sbin/cron",), "openssh-server": ("/usr/sbin/sshd",),
              "openbsd-inetd": ("/usr/sbin/inetd",), "inetutils-telnetd": ("/usr/sbin/telnetd",),
              "snapd": ("/usr/lib/snapd/snapd",), "python3-minimal": ("/usr/bin/python3",),
              "unattended-upgrades": ("/usr/share/unattended-upgrades/unattended-upgrade-shutdown",),
              "irqbalance": ("/usr/sbin/irqbalance",), "util-linux": ("/usr/sbin/agetty",), "bash": ("/usr/bin/bash",),
              "tcpd": ("/usr/sbin/tcpd",)},
    "jammy": {"systemd-sysv": ("/sbin/init",), "systemd": ("/lib/systemd/systemd-journald",
              "/lib/systemd/systemd-networkd", "/lib/systemd/systemd-resolved", "/lib/systemd/systemd-logind"),
              "udev": ("/lib/systemd/systemd-udevd",), "systemd-timesyncd": ("/lib/systemd/systemd-timesyncd",),
              "dbus": ("/usr/bin/dbus-daemon",), "rsyslog": ("/usr/sbin/rsyslogd",), "cron": ("/usr/sbin/cron",),
              "openssh-server": ("/usr/sbin/sshd",), "openbsd-inetd": ("/usr/sbin/inetd",),
              "inetutils-telnetd": ("/usr/sbin/telnetd",), "snapd": ("/usr/lib/snapd/snapd",),
              "python3-minimal": ("/usr/bin/python3",),
              "unattended-upgrades": ("/usr/share/unattended-upgrades/unattended-upgrade-shutdown",),
              "irqbalance": ("/usr/sbin/irqbalance",), "util-linux": ("/sbin/agetty",), "bash": ("/bin/bash",),
              "tcpd": ("/usr/sbin/tcpd",)},
    "trixie": {"systemd-sysv": ("/usr/sbin/init",), "systemd": ("/usr/lib/systemd/systemd-journald",
               "/usr/lib/systemd/systemd-networkd", "/usr/lib/systemd/systemd-logind"),
               "udev": ("/usr/lib/systemd/systemd-udevd",), "systemd-timesyncd": ("/usr/lib/systemd/systemd-timesyncd",),
               "dbus-daemon": ("/usr/bin/dbus-daemon",), "cron": ("/usr/sbin/cron",),
               "openssh-server": ("/usr/sbin/sshd", "/usr/lib/openssh/sshd-session"),
               "openbsd-inetd": ("/usr/sbin/inetd",), "inetutils-telnetd": ("/usr/sbin/telnetd",),
               "util-linux": ("/usr/sbin/agetty",), "bash": ("/usr/bin/bash",), "tcpd": ("/usr/sbin/tcpd",)},
    "bookworm": _DEB12_DAEMONS,
    "raspios": {**_DEB12_DAEMONS, "network-manager": ("/usr/sbin/NetworkManager",),
                "avahi-daemon": ("/usr/sbin/avahi-daemon",), "wpasupplicant": ("/sbin/wpa_supplicant",),
                "modemmanager": ("/usr/sbin/ModemManager",), "bluez": ("/usr/libexec/bluetooth/bluetoothd",),
                "triggerhappy": ("/usr/sbin/thd",)},
}
# the index rows (apt_meta's columns) of the packages of the daemons the release's data leaves out (apt-cache show
#   and policy of ubuntu:24.04, ubuntu:22.04 and debian:12, arm64, 2026-10-19; refs t12 *-t12-show-arm64;
#   unverified: their pockets, the sizes of amd64)
_UBUNTU = "Ubuntu Developers <ubuntu-devel-discuss@lists.ubuntu.com>"
DEB_DAEMON_META = {
    "noble": {"snapd": ["2.77.1+ubuntu24.04", "main", "updates", 111100, 29350676, "-", "devel", _UBUNTU, "optional", "-",
                        "Daemon and tooling that enable snap packages"],
              "irqbalance": ["1.9.3-2ubuntu5", "main", "-", 238, 54604, "-", "utils", _UBUNTU, "optional", "-",
                             "Daemon to balance interrupts for SMP systems"],
              "unattended-upgrades": ["2.9.1+nmu4ubuntu1", "main", "updates", 412, 51198, "all", "admin", _UBUNTU,
                                      "optional", "-", "automatic installation of security upgrades"]},
    "jammy": {"snapd": ["2.76.3+ubuntu22.04.1", "main", "updates", 117284, 30821072, "-", "devel", _UBUNTU, "optional",
                        "-", "Daemon and tooling that enable snap packages"],
              "irqbalance": ["1.8.0-1ubuntu0.2", "main", "updates", 150, 53044, "-", "utils", _UBUNTU, "standard", "-",
                             "Daemon to balance interrupts for SMP systems"],
              "unattended-upgrades": ["2.8ubuntu1", "main", "-", 436, 49412, "all", "admin", _UBUNTU, "optional", "-",
                                      "automatic installation of security upgrades"]},
    "raspios": {"triggerhappy": ["0.5.0-1.1+b2", "main", "-", 190, 31820, "-", "utils",
                                 "Stefan Tomanek <stefan.tomanek+debian@wertarbyte.de>", "optional", "-",
                                 "global hotkey daemon for Linux"]},
}
# the packages of one source with systemd: always at its version
SYSTEMD_BINARIES = ("systemd-sysv", "udev", "systemd-timesyncd", "systemd-resolved")


def deb_daemon_files(persona, ident):
    """{package: (paths)} of the daemons of the persona's ps as dpkg records them (DEB_DAEMON_FILES)."""
    if persona.family not in ("ubuntu", "debian", "raspios"):
        return {}
    found = dict(DEB_DAEMON_FILES.get("raspios" if persona.family == "raspios" else _codename(persona, ident), {}))
    found.update({name: (path,) for name, (path, _) in nvidia_packages(ident).items()})
    return found


def nvidia_packages(ident):
    """{package: (program, index row)} of the NVIDIA driver of a GPU server: Ubuntu's packages of the branch
    (nvidia-graphics-drivers-<branch> of Launchpad, restricted; unverified: the revision of each driver version,
    the sizes)."""
    driver = ident.release.extra.get("nvidia_driver")
    if not driver:
        return {}
    branch, version = driver.split(".", 1)[0], f"{driver}-0ubuntu0.{ident.release.os_version_id}.1"
    maintainer = "Ubuntu Core Developers <ubuntu-devel-discuss@lists.ubuntu.com>"
    return {f"nvidia-compute-utils-{branch}": ("/usr/bin/nvidia-persistenced", [
                version, "restricted", "updates", 512, 121000, "-", "restricted/libs", maintainer, "optional", "-",
                "NVIDIA compute utilities"]),
            f"nvidia-utils-{branch}": ("/usr/bin/nvidia-smi", [
                version, "restricted", "updates", 1720, 498000, "-", "restricted/libs", maintainer, "optional", "-",
                "NVIDIA driver support binaries"])}


# the program of a process title (setproctitle) or of a daemon that runs a program of another name
TITLE_PROGRAMS = {"sshd:": "/usr/sbin/sshd", "@dbus-daemon": "/usr/bin/dbus-daemon", "avahi-daemon:": "/usr/sbin/avahi-daemon",
                  "dbus-broker": "/usr/bin/dbus-broker", "qmgr": "/usr/libexec/postfix/qmgr",
                  "pickup": "/usr/libexec/postfix/pickup"}
# where nothing of a package manager lives (installed by hand or by an installer of its own)
UNPACKAGED = ("/usr/local/", "/opt/", "/home/")


def ps_programs(persona, ident, command):
    """The programs of a process of ps: its program (a login shell's bash, the program of a title), and the
    script an interpreter runs."""
    words = str(command).split()
    if not words:
        return []
    first = words[0]
    if first.startswith("-"):
        bash = "/usr/bin/bash" if is_el(persona) or _codename(persona, ident) in ("noble", "trixie") else "/bin/bash"
        return [bash] if first[1:] == "bash" else []
    if first in TITLE_PROGRAMS:
        return [TITLE_PROGRAMS[first]]
    found = [first] if first.startswith("/") else []
    if first.rsplit("/", 1)[-1].startswith(("python", "perl")):
        script = next((word for word in words[1:] if not word.startswith("-")), None)
        if script and script.startswith("/"):
            found.append(script)
    return found


def path_spellings(path):
    """A path and its other spelling of the merged /usr (/sbin/init, /usr/sbin/init); a path below /usr that the
    root has no link of (/usr/libexec) has one only."""
    for short in ("/bin/", "/sbin/", "/lib/", "/lib64/"):
        if path.startswith(short):
            return [path, "/usr" + path]
        if path.startswith("/usr" + short):
            return [path, path[4:]]
    return [path]


def program_owner(persona, ident, path, alias=True):
    """The package dpkg -S / rpm -qf names for a program (alias: its other spelling below /usr too, as the merged
    /usr has it), None if no installed package owns it."""
    spellings = path_spellings(path) if alias else [path]
    if has_dpkg(persona):
        data = apt_packages(persona, ident)
        for name in data["installed"]:
            if any(spelling in data["removes"].get(name, ()) for spelling in spellings):
                return name
        return None
    if persona.family == "centos":
        installed = centos_installed(ident)
        owners = dict(line.split(" ", 1) for line in package_meta_files(persona, ident)[RPM_OWNERS])
    elif persona.family == "rhel":
        from .servers import dnf_packages
        data = dnf_packages(persona, ident)
        installed, owners = data["installed"], data["owners"]
    else:
        return None
    for spelling in path_spellings(path):
        if owners.get(spelling) in installed:
            return owners[spelling]
    return None


def apt_packages(persona, ident):
    """What apt knows on a Debian server: the installed packages (the release's, APT_INSTALLED), the
    versions of the packages it would install (APT_AVAILABLE), their programs (those that answer then:
    session_answers), the packages it has no candidate for (APT_UNAVAILABLE), the candidates of the
    release's indexes ("meta", apt_meta, with its kernel packages), the archive names of its pockets,
    the files dpkg counts (apt_files) and the packages that keep their conffiles after a remove."""
    code = ident.release.extra["codename"]
    kernel, kernel_available = kernel_packages(persona, ident)
    meta = {**apt_meta(persona, ident), **kernel_meta(persona, ident)}
    base = {name: meta[name][0] for name in apt_base(persona, ident) if name in meta}
    installed = {**base, **APT_INSTALLED[code], **ident.release.packages, **kernel}
    # the packages of the daemons ps shows, at the release's candidate (dpkg -l and -S know them); the NVIDIA
    #   driver at the version of the running kernel module
    rows = {**DEB_DAEMON_META.get("raspios" if persona.family == "raspios" else code, {}),
            **{name: row for name, (_, row) in nvidia_packages(ident).items()}}
    meta.update({name: list(row) for name, row in rows.items() if name not in meta or name.startswith("nvidia-")})
    # the covered service packages (package_units.SERVICE_META)
    from .package_units import SERVICE_META
    #   win over the rows of the index data, which is older: all binaries of one source package from one table
    meta.update({name: list(row) for name, row in SERVICE_META.get("bookworm" if persona.family == "raspios" else code,
                                                                   {}).items()})
    # the packages the recorded installs brought (package_installs.json.gz): their version, pocket, component and
    #   size as the install's Get lines name them, the rest of the index row where the data has one; the packages
    #   the persona has (and those of its daemons) keep the rows of its index
    from .package_units import deb_release
    release = deb_release(persona, ident)
    kept = set(installed) | set(deb_daemon_files(persona, ident))
    for install in release.get("installs", {}).values():
        for name, version, arch_all, pocket, component, size in install["new"]:
            if name in kept:
                continue
            row = list(meta.get(name) or [version, component, pocket, max(1, size * 22 // 10240), size,
                                          "-", "misc", "", "optional", "-", name])
            row[:6] = [version, component, pocket, row[3], size, "all" if arch_all else "-"]
            meta[name] = row
    for name in deb_daemon_files(persona, ident):
        if name not in installed and name in meta:
            installed[name] = meta[name][0]
    for name in SYSTEMD_BINARIES:
        if name in installed and "systemd" in installed:
            installed[name] = installed["systemd"]
    installed = dict(sorted(installed.items()))
    available = {name: version for name, version in sorted({**APT_AVAILABLE[code], **kernel_available}.items())
                 if name not in installed}
    # the programs and unit files the recorded packages ship (dpkg -L of the release: package_installs.json.gz)
    shipped = {name: [path for path in paths if "/systemd/system/" not in path]
               for name, paths in release.get("files", {}).items()}
    return {"installed": installed, "available": available,
            "programs": {**{name: paths for name, paths in shipped.items() if name not in installed},
                         **{name: apt_programs(name) for name in available}},
            "unit_files": {name: [path for path in paths if "/systemd/system/" in path]
                           for name, paths in release.get("files", {}).items()
                           if any("/systemd/system/" in path for path in paths)},
            "answers": session_answers(path for name in available for path in apt_programs(name)),
            "unavailable": [name for name in APT_UNAVAILABLE.get(code, ()) if name not in installed
                            and name not in available],
            "meta": meta, "archives": apt_archives(persona, ident), "files": apt_files(persona, ident),
            "conffiles": sorted(APT_CONFFILES), "removes": apt_removes(persona, ident, installed),
            "essential": sorted(name for name in installed if name in meta and meta[name][9] == "yes")}


def apt_origins(persona, ident):
    """[shell] apt_origin / apt_origin_security: the origin and version apt-get -s names (Debian:12.13,
    Debian-Security:12, Ubuntu:24.04)."""
    if persona.os_id == "ubuntu":
        origin = f"Ubuntu:{ident.release.os_version_id}"
        return {"apt_origin": origin, "apt_origin_security": origin}
    point = ident.release.extra.get("debian_version") or ident.release.os_version_id
    return {"apt_origin": f"Debian:{point}", "apt_origin_security": f"Debian-Security:{point.split('.')[0]}"}


def _base_letter(persona, ident):
    """The letter of the recorded image of the release (Raspberry Pi OS: Debian 12's)."""
    letter = _apt_letter(persona, ident)
    return "b" if letter == "r" else letter


def apt_base(persona, ident):
    """The base system of the release (deb-base.txt.gz): its Priority required and important packages and those
    of its official image."""
    letter = _apt_letter(persona, ident)
    return {row[0] for row in _name_rows("deb-base.txt.gz") if letter in row[1]}


def deb_files(persona, ident):
    """{package: [paths]} of the programs the release's packages record (deb-files.txt.gz, dpkg -L of the
    official image: /bin/ls on Debian 12 and Ubuntu 22.04, /usr/bin/ls on Debian 13 and Ubuntu 24.04)."""
    letter = _base_letter(persona, ident)
    found = {}
    for path, letters, package in _name_rows("deb-files.txt.gz"):
        if letter in letters:
            found.setdefault(package, []).append(path)
    return found


def apt_removes(persona, ident, installed):
    """{package: [programs]} of the persona's installed packages (what dpkg -L / -S list and a remove takes
    away): the programs the release's image records for them (deb_files), else the tools of TOOL_PROGRAMS
    and the program of PACKAGE_PROGRAMS."""
    recorded = deb_files(persona, ident)
    for name, paths in deb_daemon_files(persona, ident).items():
        recorded[name] = sorted(set(recorded.get(name, [])) | set(paths))
    found = {}
    for name in installed:
        if name in recorded:
            found[name] = sorted(recorded[name])
            continue
        paths = ["/" + rel for rel in TOOL_PROGRAMS.get(name, {}).values()]
        if name in PACKAGE_PROGRAMS and name not in ("bash", "tar", "gzip"):
            program = PACKAGE_PROGRAMS[name]
            paths.append(f"/usr/sbin/{program}" if program == "sshd" else f"/usr/bin/{program}")
        if paths:
            found[name] = sorted(set(paths))
    return found


def _apt_letter(persona, ident):
    return "r" if persona.family == "raspios" else APT_NAME_RELEASES[ident.release.extra["codename"]]


def apt_meta(persona, ident):
    """The candidates of the release's indexes (apt-meta.txt.gz): {name: [version, component, pocket,
    installed size (KiB), size (bytes), "all" or "-", section, maintainer, priority, essential ("yes" or "-"),
    description]}."""
    letter = _apt_letter(persona, ident)
    found = {}
    with gzip.open(NAMES_DIR / "apt-meta.txt.gz", "rt", encoding="utf-8") as f:
        for line in f:
            row = line.rstrip("\n").split("\t")
            if len(row) == 13 and letter in row[1]:
                found[row[0]] = [row[2], row[3], row[4], int(row[5] or 0), int(row[6] or 0), *row[7:]]
    return found


def kernel_meta(persona, ident):
    """The rows of apt_meta for the packages of the persona's running kernel (the indexes leave out
    the names of a kernel ABI): the descriptions of the release's kernel packages (unverified for
    the meta-packages of Raspberry Pi OS); sizes as a generic kernel of the release."""
    installed, available = kernel_packages(persona, ident)
    release = ident.release.kernel_version
    upstream = release.split("-", 1)[0]
    series = ".".join(upstream.split(".")[:2])
    found = {}
    for name, version in {**installed, **available}.items():
        if persona.family == "ubuntu":
            abi, _, flavour = release.rpartition("-")
            text = {f"linux-image-{release}": f"Signed kernel image {flavour}",
                    f"linux-modules-{release}": f"Linux kernel extra modules for version {upstream} on 64 bit x86 SMP",
                    f"linux-modules-extra-{release}":
                        f"Linux kernel extra modules for version {upstream} on 64 bit x86 SMP",
                    f"linux-headers-{release}": f"Linux kernel headers for version {upstream} on 64 bit x86 SMP",
                    f"linux-headers-{abi}": f"Header files related to Linux kernel version {upstream}"}.get(name, "")
        elif persona.family == "raspios":
            text = _raspios_kernel_text(name, series)
        else:
            text = {f"linux-image-{release}": f"Linux {series} for 64-bit PCs (signed)",
                    f"linux-headers-{release}": f"Header files for Linux {release}"}.get(name, "")
        size = 60000 if "image" in name or "modules" in name else 12000
        found[name] = [version, "main", "updates" if persona.family == "ubuntu" else "-", size, size * 300, "-",
                       "kernel", KERNEL_MAINTAINERS.get(persona.family, ""), "optional", "-", text]
    return found


KERNEL_MAINTAINERS = {"ubuntu": "Ubuntu Kernel Team <kernel-team@lists.ubuntu.com>",
                      "debian": "Debian Kernel Team <debian-kernel@lists.debian.org>"}


def _raspios_kernel_text(name, series):
    found = re.fullmatch(r"linux-(image|headers)-(\S+)-rpi-(v8|2712)", name)
    if found and found.group(1) == "image":
        return f"Linux {series} for Raspberry Pi {found.group(3)}, Raspberry Pi"
    if found:
        return f"Header files for Linux {found.group(2)}-rpi-{found.group(3)}"
    found = re.fullmatch(r"linux-(image|headers)-rpi-(v8|2712)", name)
    if found:
        what = "image" if found.group(1) == "image" else "headers"
        return f"Linux for Raspberry Pi {found.group(2)} ({what} meta-package)"
    found = re.fullmatch(r"linux-headers-(\S+)-common-rpi", name)
    if found:
        return f"Common header files for Linux {found.group(1)}-rpi"
    if name.startswith("linux-kbuild-"):
        return f"Kbuild infrastructure for Linux {series}"
    return ""


# the archive names apt list and apt search show (the Suite of the Release files in 2026: Debian 12 is
#   oldstable, Debian 13 stable; Ubuntu names its suites by codename; unverified: the Raspberry Pi archive)
DEBIAN_SUITES = {"bookworm": "oldstable", "trixie": "stable"}


def apt_archives(persona, ident):
    """{pocket of apt_meta: archive name}."""
    code = ident.release.extra["codename"]
    base = code if persona.os_id == "ubuntu" else DEBIAN_SUITES.get(code, code)
    return {"-": base, "updates": f"{base}-updates", "security": f"{base}-security",
            "backports": f"{code}-backports", "rpi": code}


# the files and directories dpkg has installed, by family (unverified: the range of the cloud and Lite
#   images of the releases; a container has far fewer)
APT_FILES = {"ubuntu": (70_000, 96_000), "debian": (26_000, 38_000), "raspios": (44_000, 58_000)}


def apt_files(persona, ident):
    """The files and directories dpkg counts at the start ("Reading database ... N files"), the same for an
    identity on every start."""
    low, high = APT_FILES[persona.family]
    digest = hashlib.sha256(f"{persona.id} {ident.hostname} {ident.release.label}".encode()).digest()
    return low + int.from_bytes(digest[:4], "big") % (high - low)


# packages that leave their conffiles after `apt remove` (dpkg -l: rc); the others are gone (tree, in the
#   refs of every release); unverified beyond the services that ship files below /etc
APT_CONFFILES = frozenset({"openssh-server", "openssh-client", "nginx", "nginx-common", "apache2", "sudo", "cron",
                           "rsyslog", "openbsd-inetd", "inetutils-telnetd", "isc-dhcp-client", "network-manager",
                           "tor", "screen", "bind9", "vsftpd", "proftpd-basic", "mariadb-server", "postfix",
                           "samba", "squid", "wget"})


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
            "opkg_help": f"{runtime_dir}/{OPKG_HELP_FILE}", "package_names": f"{runtime_dir}/{PACKAGE_NAMES}",
            "opkg_meta": f"{runtime_dir}/{OPKG_META}"}


# the runtime files of the versions, sizes and descriptions of the packages (yum 3, opkg) and of rpm's owners
YUM_META = "yum-meta.txt"
RPM_OWNERS = "rpm-owners.txt"
OPKG_META = "opkg-meta.txt"
DNF_META = "dnf-meta.txt"
# the manual pages of the programs of the release's rpm packages ("path package", rpm-man.txt.gz)
RPM_MANUALS = "rpm-manuals.txt"


def rpm_manuals(letter):
    """The manual pages of the release's rpm packages (c CentOS 7, r RHEL 9): ["path package"]."""
    with gzip.open(NAMES_DIR / "rpm-man.txt.gz", "rt", encoding="utf-8") as f:
        return [f"{row[0]} {row[2]}" for row in (line.split() for line in f) if len(row) == 3 and letter in row[1]]
# the packages of a CentOS 7.9 image besides the release's and CENTOS_BASE (unverified: the @core group of
#   the GenericCloud image, at the version of the updates repository; openssh at the version of its server)
CENTOS_CORE = ("acl", "audit", "basesystem", "bash", "ca-certificates", "centos-release", "chkconfig", "coreutils",
               "cpio", "cronie", "dbus", "device-mapper", "diffutils", "dracut", "e2fsprogs", "ethtool", "filesystem",
               "findutils", "gawk", "glibc", "glibc-common", "grep", "gzip", "hostname", "initscripts", "iptables", "iputils",
               "kmod", "less", "logrotate", "openssh", "openssh-clients", "openssl", "openssl-libs", "pam", "passwd",
               "procps-ng", "python", "sed", "setup", "shadow-utils", "sudo", "systemd", "tar", "tzdata",
               "util-linux", "vim-minimal", "which", "xz", "yum-plugin-fastestmirror",
               # the packages of the daemons of ps (refs t12 centos-7-t12-owners)
               "NetworkManager", "chrony", "polkit", "postfix", "rsyslog")
# the files of CentOS 7's packages rpm -qf names besides RPM_OWNED (dbus-daemon of dbus, Python 2, polkitd, postfix's
#   programs, the helpers of NetworkManager and systemd; refs t12 centos-7-t12-owners, centos-7-t12-postfix)
CENTOS_OWNED = {"dbus": ("dbus-daemon", ""), "python": ("python python2 python2.7", ""),
                "postfix": ("mailq.postfix newaliases.postfix rmail.postfix", "postalias postcat postconf postdrop postfix "
                            "postkick postlock postlog postmap postmulti postqueue postsuper sendmail.postfix")}
CENTOS_OWNED_FILES = {"polkit": ("usr/lib/polkit-1/polkitd",), "NetworkManager": ("usr/libexec/nm-dhcp-helper",),
                      "postfix": ("usr/libexec/postfix/master", "usr/libexec/postfix/pickup", "usr/libexec/postfix/qmgr"),
                      "systemd": ("usr/lib/systemd/systemd", "usr/lib/systemd/systemd-journald",
                                  "usr/lib/systemd/systemd-udevd", "usr/lib/systemd/systemd-logind")}
# the programs of the daemons of ps that no unit file names (forked daemons and the helpers they run)
EL_DAEMON_PROGRAMS = {"centos": ("usr/libexec/nm-dhcp-helper", "usr/libexec/postfix/master", "usr/libexec/postfix/pickup",
                                 "usr/libexec/postfix/qmgr", "usr/sbin/postfix", "usr/sbin/logrotate"),
                      # logrotate of rsyslog (a program of its package, not of Cowrie's tree)
                      "rhel": ("usr/libexec/nm-dhcp-helper", "usr/bin/dbus-broker", "usr/sbin/logrotate")}


# the packages of CentOS 7 whose names RHEL 9 has changed (iptables-nft)
CENTOS_TOOLS = {"iptables": "iptables"}


def _yum_meta_rows():
    return [row for row in (line.split("\t") for line in gzip.open(NAMES_DIR / "yum-meta.txt.gz", "rt",
                                                                    encoding="utf-8").read().splitlines())
            if len(row) == 12]


def centos_installed(ident):
    """{name: evr} of the installed packages of CentOS 7 (yum 3 and rpm of the session start with them)."""
    meta = {row[0]: row[2] for row in _yum_meta_rows()}
    found = {name: meta[name].replace("0:", "", 1) if meta[name].startswith("0:") else meta[name]
             for name in CENTOS_CORE if name in meta}
    ssh = ident.release.packages.get("openssh-server")
    if ssh:
        found.update({"openssh": ssh, "openssh-clients": ssh})
    return {**found, **CENTOS_BASE, **ident.release.packages}


def package_meta_files(persona, ident):
    """{runtime file: lines} of the package data of the release the patched commands read: the builds of
    CentOS 7 (yum-meta) and the owners of its programs for rpm -qf, the feeds of OpenWrt (opkg-meta)."""
    if persona.family == "centos":
        installed = centos_installed(ident)
        owners = []
        for package, (programs, sbin) in RPM_OWNED.items():
            if package in installed:
                owners += [f"/usr/bin/{name} {package}" for name in programs.split()]
                owners += [f"/usr/sbin/{name} {package}" for name in sbin.split()]
        for package, (programs, sbin) in CENTOS_OWNED.items():
            if package in installed:
                owners += [f"/usr/bin/{name} {package}" for name in programs.split()]
                owners += [f"/usr/sbin/{name} {package}" for name in sbin.split()]
        for package, files in CENTOS_OWNED_FILES.items():
            if package in installed:
                owners += [f"/{rel} {package}" for rel in files]
        for tool in installed_tools(persona, ident):
            package = CENTOS_TOOLS.get(tool, RPM_TOOLS[tool])
            if package in installed:
                owners += [f"/{rel} {package}" for rel in tool_paths(persona, tool).values()]
        return {YUM_META: ["\t".join(row) for row in _yum_meta_rows()], RPM_OWNERS: sorted(set(owners)),
                RPM_MANUALS: rpm_manuals("c")}
    if persona.family == "rhel":
        with gzip.open(NAMES_DIR / "dnf-meta.txt.gz", "rt", encoding="utf-8") as f:
            return {DNF_META: f.read().splitlines(), RPM_MANUALS: rpm_manuals("r")}
    if persona.family == "openwrt":
        letter = OPKG_NAME_RELEASES[opkg_branch(ident)]
        found, description = {}, {}
        with gzip.open(NAMES_DIR / "opkg-meta.txt.gz", "rt", encoding="utf-8") as f:
            for line in f:
                row = line.rstrip("\n").split("\t")
                if len(row) != 4:
                    continue
                if row[3]:
                    description.setdefault(row[0], row[3])
                if letter in row[1]:
                    found[row[0]] = row[2]
        return {OPKG_META: [f"{name}\t{version}\t{description.get(name, '')}" for name, version in sorted(found.items())]}
    return {}


def kernel_packages(persona, ident):
    """The packages of the persona's running kernel (uname -r) at the version of its build (uname -v):
    ({installed}, {available}). Ubuntu's cloud images have its image, modules and headers (the cloud
    image manifests of 22.04 / 24.04), Debian its image; the headers (and Ubuntu's modules-extra)
    install at the same version. Raspberry Pi OS has the kernels of both boards (rpi-v8 of the Pi 4,
    rpi-2712 of the Pi 5) with their headers, meta-packages, common headers and kbuild, as the
    package lists of its images (*.info) name them."""
    release, build = ident.release.kernel_version, ident.release.kernel_build
    if persona.family == "ubuntu":
        abi, flavour = release.rsplit("-", 1)
        found = re.search(r"#(\S+)-Ubuntu", build)
        version = f"{abi}.{found.group(1) if found else '1'}"
        installed = {f"linux-image-{release}", f"linux-modules-{release}", f"linux-headers-{release}"}
        if flavour == "generic":
            installed.add(f"linux-headers-{abi}")
        available = {f"linux-modules-extra-{release}"}
    elif persona.family == "raspios":
        found = re.search(r"Debian (\S+) \(", build)
        version = found.group(1) if found else release
        base = release.rsplit("-rpi-", 1)[0]
        installed = {f"linux-headers-{base}-common-rpi", f"linux-kbuild-{base}"}
        for flavor in sorted(set(ident.release.extra.get("flavors", {}).values()) or {"v8", "2712"}):
            installed |= {f"linux-image-{base}-rpi-{flavor}", f"linux-headers-{base}-rpi-{flavor}",
                          f"linux-image-rpi-{flavor}", f"linux-headers-rpi-{flavor}"}
        available = set()
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
    if persona.family == "rhel":
        return [" ".join(row) for row in _name_rows("dnf.txt.gz")]
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
    if persona.family == "synology":
        # DSM 7: systemctl of its systemd
        return {"systemctl_units": f"{runtime_dir}/{SYSTEMD_UNITS}"}
    if not found:
        return {}
    perl, year = found["perl"]
    keys = {"perl_version": perl, "perl_archname": perl_archname(persona), "perl_copyright": year}
    if persona.family == "ubiquiti":
        # apt-get of EdgeOS: apt 1.4 of Debian 9 without sources (the image names none): no name is found
        keys.update({"apt_version": ident.release.packages["apt"], "deb_arch": ident.release.extra["deb_arch"],
                     "apt_sources": "", "package_names": f"{runtime_dir}/{PACKAGE_NAMES}"})
    if persona.family == "ubiquiti":
        keys.update({"sudo_version": "1.8.19p1", "sudo_grammar": sudo_grammar("1.8.19"),
                     "sudo_list": f"{runtime_dir}/{SUDO_LIST}", "systemctl_units": f"{runtime_dir}/{SYSTEMD_UNITS}"})
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
                     "dpkg_version": ident.release.packages["dpkg"], **apt_origins(persona, ident),
                     "apt_sources": ",".join(f"{url} {suite}" for url, suite in apt_sources(files)),
                     "apt_packages": f"{runtime_dir}/{APT_PACKAGES}",
                     "apt_fallback": "ubuntu" if persona.os_id == "ubuntu" else "debian"})
    if has_dpkg(persona) or is_el(persona):
        keys["package_names"] = f"{runtime_dir}/{PACKAGE_NAMES}"
    if persona.family == "centos":
        keys["yum_plugins"] = YUM_PLUGINS
        keys["yum_installed"] = ",".join(f"{name} {evr}" for name, evr in sorted(centos_installed(ident).items()))
        keys.update({"yum_meta": f"{runtime_dir}/{YUM_META}", "rpm_owners": f"{runtime_dir}/{RPM_OWNERS}",
                     "rpm_manuals": f"{runtime_dir}/{RPM_MANUALS}",
                     "rpm_version": ident.release.packages["rpm"].split("-")[0]})
    if persona.family == "rhel":
        keys["dnf_packages"] = f"{runtime_dir}/{DNF_PACKAGES}"
        keys["dnf_meta"] = f"{runtime_dir}/{DNF_META}"
        keys["rpm_manuals"] = f"{runtime_dir}/{RPM_MANUALS}"
    # what the maintainer scripts of the packages do to the units (package_units.PACKAGE_UNITS)
    keys["package_units"] = f"{runtime_dir}/package-units.json"
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
#   lsmod listings, not taken from the images); the sizes are whole pages only with STRICT_MODULE_RWX
#   (debug_align of kernel/module.c): not on the 2.6 / 3.x vendor kernels and never on MIPS (arch/mips
#   has no ARCH_HAS_STRICT_MODULE_RWX in 4.14 and 5.10: EdgeOS, OpenWrt)
_MODULES_DEVICE = {
    "dlink": (("ath_pktlog", 19648, 0, ()), ("umac", 588720, 0, ()), ("ath_dev", 212364, 1, ("umac",)),
              ("ath_rate_atheros", 31392, 2, ("umac", "ath_dev")),
              ("ath_hal", 406720, 3, ("umac", "ath_dev", "ath_rate_atheros")),
              ("asf", 5008, 4, ("ath_pktlog", "umac", "ath_dev", "ath_hal")),
              ("adf", 54448, 4, ("ath_pktlog", "umac", "ath_dev", "ath_hal"))),
    # the Archer C7 v5: names, load order (/etc/modules.d) and dependencies of the modules of the image, the
    #   sizes the allocated sections of their .ko files add up to (unverified against a running lsmod)
    "tplink": (("qca_ssdk", 584112, 0, ()), ("nf_nat_pptp", 1520, 0, ()),
               ("nf_conntrack_pptp", 3408, 1, ("nf_nat_pptp",)), ("nf_nat_proto_gre", 992, 1, ("nf_nat_pptp",)),
               ("nf_conntrack_proto_gre", 2512, 1, ("nf_conntrack_pptp",)), ("shortcut_fe", 50176, 0, ()),
               ("ath_pktlog", 13088, 0, ()), ("umac", 2708464, 1, ("ath_pktlog",)),
               ("ath_dev", 253408, 2, ("umac", "ath_pktlog")),
               ("ath_rate_atheros", 33840, 3, ("umac", "ath_dev", "ath_pktlog")),
               ("ath_hal", 715520, 4, ("umac", "ath_dev", "ath_rate_atheros", "ath_pktlog"))),
    "zyxel": (("ufsd", 640114, 0, ()), ("jnl", 21302, 1, ("ufsd",)), ("hfsplus", 77370, 0, ()),
              ("usb_storage", 41022, 0, ()), ("fuse", 72290, 1, ()), ("nfsd", 238116, 13, ()),
              ("exportfs", 3152, 1, ("nfsd",))),
    "openwrt": (("mt7603e", 92448, 0, ()), ("mt76", 58032, 1, ("mt7603e",)),
                ("mac80211", 501616, 2, ("mt7603e", "mt76")), ("cfg80211", 280624, 3, ("mt7603e", "mt76", "mac80211")),
                ("compat", 3168, 2, ("mac80211", "cfg80211")), ("nf_conntrack", 78288, 0, ())),
    "qnap": (("fbdisk", 36864, 0, ()), ("ufsd", 991232, 0, ()), ("jnl", 36864, 1, ("ufsd",)),
             ("usb_storage", 77824, 0, ()), ("fuse", 155648, 1, ()), ("nfsd", 425984, 13, ())),
    "synology": (("synoacl_vfs", 20480, 1, ()), ("btrfs", 1015808, 1, ()), ("fuse", 94208, 1, ()),
                 ("usb_storage", 57344, 0, ()), ("loop", 28672, 2, ())),
    "ubiquiti": (("ubnt_nf_app", 9764, 0, ()), ("tdts", 503824, 1, ("ubnt_nf_app",)), ("ipt_NETFLOW", 46708, 0, ()),
                 ("imq", 8320, 0, ()), ("ubnt_platform", 33712, 0, ())),
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


def lsmod_text(persona, modules):
    """What lsmod prints for the /proc/modules text of the persona: kmod's lsmod ("%-19s %8lu  %d" and
    the users) or, on the BusyBox devices, BusyBox's (FEATURE_LSMOD_PRETTY_2_6_OUTPUT: "%-19s %8s %2s %s"
    with the users less their last comma, and the taint of /proc/sys/kernel/tainted; recorded with the
    BusyBox v1.19.4 of the Archer C7 v5). The kernels of the QCA routers taint with their proprietary
    Wi-Fi drivers (P; unverified)."""
    if persona.family == "openwrt":
        return kmodloader_lsmod(modules)
    busybox = persona.family in ASH_FAMILIES
    if busybox:
        taint = "    Tainted: P  " if persona.family in ("tplink", "dlink") else "    Not tainted"
        out = f"{'Module':<24}Size  Used by{taint}\n"
    else:
        out = "Module                  Size  Used by\n"
    for line in modules.splitlines():
        name, size, count, users = line.split()[:4]
        users = "" if users == "-" else users.rstrip(",")
        if busybox:
            out += f"{name:<19} {size:>8} {count:>2} {users}\n"
        else:
            out += f"{name:<19} {size:>8}  {count}" + (f" {users}" if users else "") + "\n"
    return out


def kmodloader_lsmod(modules):
    """lsmod of OpenWrt (/sbin/lsmod -> /sbin/kmodloader of ubox; main_lsmod of kmodloader.c at the ubox of
    21.02 and 22.03, recorded in openwrt/rootfs:armvirt-64-21.02.7): no header, "%-20s%8d%3d " and the users
    joined by commas, the modules in the order of its tree of names, which weighs "_" as "-"."""
    rows = [line.split()[:4] for line in modules.splitlines()]
    rows.sort(key=lambda row: row[0].replace("_", "-"))
    return "".join(f"{name:<20}{int(size):8d}{int(count):3d} " + ("" if users == "-" else users.rstrip(",")) + "\n"
                   for name, size, count, users in rows)


# OpenWrt's module tools: links to kmodloader of ubox (the 21.02.7 and 22.03.7 images)
KMODLOADER_LINKS = ("insmod", "lsmod", "modinfo", "modprobe", "rmmod")


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


# Ubuntu's command-not-found (installed on the Ubuntu cloud and server images): the table of
#   tools/build_command_not_found.py, the runtime file of the release ([shell] command_not_found)
COMMAND_NOT_FOUND = "command-not-found.txt"
COMMAND_NOT_FOUND_TABLE = Path(__file__).resolve().parent / "command_not_found" / "ubuntu.txt.gz"
COMMAND_NOT_FOUND_RELEASES = {"noble": "n", "jammy": "j"}


def command_not_found_lines(persona, ident):
    """The lines of the runtime file of command-not-found ("<command> install <pkg>=<ver>,..." or
    "<command> similar ..."), for the release of an Ubuntu persona; None elsewhere."""
    if persona.family != "ubuntu":
        return None
    letter = COMMAND_NOT_FOUND_RELEASES[ident.release.extra["codename"]]
    with gzip.open(COMMAND_NOT_FOUND_TABLE, "rt", encoding="utf-8") as f:
        return [line.rstrip("\n").split(" ", 1)[1] for line in f if line.startswith(letter + " ")]


def sudo_users(group_text):
    """The members of the groups sudo and admin of an /etc/group (who command-not-found tells sudo)."""
    users = []
    for line in group_text.splitlines():
        fields = line.split(":")
        if len(fields) >= 4 and fields[0] in ("sudo", "admin"):
            users += [name for name in fields[3].split(",") if name and name not in users]
    return users
