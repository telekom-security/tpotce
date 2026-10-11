"""What of Cowrie's file system tree a persona's system has.

Cowrie's fs.pickle is a Debian 12 x86_64 container: dpkg and apt, x86_64
libraries, Python 3.11, OpenSSH, a few systemd units, runit. A persona keeps
only what its own system has (foreign_paths, the multiarch and Python renames
of render.apply_to_tree) and the versions its interpreters print (tools,
shell_keys for the patched perl.py / python.py / git.py of Cowrie)."""
import re

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
    "usr/share/lintian",
)
# ... and every name or link target with one of these words (man pages, translations, alternatives, keyrings,
#   changelog.Debian.gz, state of deb-systemd-helper): Debian itself, dpkg, apt and the packages adduser,
#   debianutils (which.debianutils) and init-system-helpers
DEBIAN_WORDS = ("debian", "Debian", "dpkg", "apt.mo", "deb-systemd", "adduser", "deluser", "addgroup",
                "delgroup")
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
# the Defaults of the sudoers of each family as sudo -l prints them
_SUDO_DEBIAN = ("env_reset", "mail_badpass",
                "secure_path=/usr/local/sbin\\:/usr/local/bin\\:/usr/sbin\\:/usr/bin\\:/sbin\\:/bin", "use_pty")
_SUDO_EL_KEEP = ('env_keep="COLORS DISPLAY HOSTNAME HISTSIZE KDEDIR LS_COLORS"',
                 'env_keep+="MAIL QTDIR USERNAME LANG LC_ADDRESS LC_CTYPE"',
                 'env_keep+="LC_COLLATE LC_IDENTIFICATION LC_MEASUREMENT LC_MESSAGES"',
                 'env_keep+="LC_MONETARY LC_NAME LC_NUMERIC LC_PAPER LC_TELEPHONE"',
                 'env_keep+="LC_TIME LC_ALL LANGUAGE LINGUAS _XKB_CHARSET XAUTHORITY"')
SUDO_DEFAULTS = {
    "ubuntu": _SUDO_DEBIAN[:2] + (_SUDO_DEBIAN[2] + "\\:/snap/bin", "use_pty"),
    "debian": _SUDO_DEBIAN,
    "raspios": _SUDO_DEBIAN,
    "rhel": ("!visiblepw", "always_set_home", "match_group_by_gid", "always_query_group_plugin", "env_reset")
            + _SUDO_EL_KEEP + ("secure_path=/usr/local/sbin\\:/usr/local/bin\\:/usr/sbin\\:/usr/bin",),
    "centos": ("!visiblepw", "always_set_home", "match_group_by_gid", "always_query_group_plugin", "env_reset")
              + _SUDO_EL_KEEP + ("secure_path=/sbin\\:/bin\\:/usr/sbin\\:/usr/bin",),
}
# man-db of the Debian releases (its trigger closes every apt install); unverified per patch level
MAN_DB = {"noble": "2.12.0-4build2", "jammy": "2.10.2-1", "trixie": "2.13.1-1", "bookworm": "2.11.2-2"}
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
        paths += DEBIAN_PATHS + DEBIAN_SYSTEM_PATHS
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


def files(persona):
    """Files of the persona's system in place of Debian's: the init directory, rc.local and the
    sshd unit of Red Hat."""
    if not is_el(persona):
        return {}
    rhel = persona.family == "rhel"
    return {"etc/rc.d/init.d/README": EL_INIT_README, "etc/rc.d/rc.local": EL_RC_LOCAL,
            "usr/lib/systemd/system/sshd.service": EL_SSHD_UNIT.format(keygen="target" if rhel else "service",
                                                                      env="-" if rhel else "")}


def links(persona):
    """(link, target) of the persona's system in place of Debian's (targets from the root, as in
    Cowrie's pickle): Red Hat's init directories, the enabled sshd and adduser (useradd), the loader
    link of CentOS 7."""
    found = []
    if is_el(persona):
        found += [("etc/init.d", "etc/rc.d/init.d"), ("etc/rc.local", "etc/rc.d/rc.local"),
                  ("etc/systemd/system/multi-user.target.wants/sshd.service", "usr/lib/systemd/system/sshd.service"),
                  ("usr/sbin/adduser", "usr/sbin/useradd")]
        found += [(f"etc/rc{level}.d", f"etc/rc.d/rc{level}.d") for level in range(7)]
    if persona.family == "centos":
        found.append((LOADER, CENTOS_LOADER))
    return tuple(found)


def ssh_units(persona, ident):
    """(paths to remove, links) of the ssh units: Ubuntu 24.04 starts sshd from ssh.socket, so
    ssh.service is not enabled (no alias sshd.service) and the socket is."""
    if ident.release.extra.get("codename") != "noble":
        return (), ()
    return (("etc/systemd/system/sshd.service", "etc/systemd/system/multi-user.target.wants/ssh.service"),
            (("etc/systemd/system/sockets.target.wants/ssh.socket", "usr/lib/systemd/system/ssh.socket"),))


def directories(persona):
    """Empty directories of the persona's system (the run level directories of Red Hat)."""
    return tuple(f"etc/rc.d/rc{level}.d" for level in range(7)) if is_el(persona) else ()


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


def service_status(persona):
    """What service --status-all prints on a server."""
    if persona.family == "rhel":
        return ""
    if persona.family == "centos":
        lines = _CENTOS_STATUS
    else:
        init = _PI_INIT if persona.family == "raspios" else _UBUNTU_INIT if persona.os_id == "ubuntu" else _DEBIAN_INIT
        lines = tuple(f" [ {entry[0]} ]  {entry[2:]}" for entry in init)
    return "".join(line + "\n" for line in lines)


def sudo_list(persona):
    """What sudo -l lists on a server: the Defaults of the family's sudoers and the rules of the
    login user (its group sudo / wheel, then NOPASSWD of cloud-init or of the Pi image) and of root."""
    el = is_el(persona)
    group = "(ALL) ALL" if el else "(ALL : ALL) ALL"
    return {"defaults": list(SUDO_DEFAULTS[persona.family]),
            "rules": {persona.user: [group, "(ALL) NOPASSWD: ALL"], "root": [group]}}


def apt_packages(persona, ident):
    """The installed packages apt knows on a Debian server (the release's, man-db of the release)."""
    found = dict(sorted(ident.release.packages.items()))
    found.setdefault("man-db", MAN_DB[ident.release.extra["codename"]])
    return found


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
