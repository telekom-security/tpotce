"""Server personas (bash, systemd): package managers, command inventory,
processes, package logs and one builder per family."""
from .common import (assign_processes, base_files, base_txtcmds, deb_arch, gm, lscpu, ps_entry,
                     rng_for, server_df, server_group, server_mount, server_passwd, server_shadow,
                     server_top)
from .model import Content

# Package names of the tools themselves: versions for --version output, not for the logs.
TOOL_PACKAGES = frozenset({"apt", "dpkg", "dnf", "rpm", "yum"})

_APT_HELP = """apt {version} ({arch})
Usage: apt [options] command

apt is a commandline package manager and provides commands for
searching and managing as well as querying information about packages.

Most used commands:
  list - list packages based on package names
  search - search in package descriptions
  show - show package details
  update - update list of available packages
  install - install packages
  remove - remove packages
  upgrade - upgrade the system by installing/upgrading packages
"""

_DNF_HELP = {
    "fedora": """dnf {version}
usage: dnf [options] COMMAND

List of Main Commands:
install                 install a package or packages on your system
remove                  remove a package or packages from your system
upgrade                 upgrade a package or packages on your system
search                  search package details for the given string
repolist                display the configured software repositories
""",
    "rhel": """dnf {version}
usage: dnf [options] COMMAND

List of Main Commands:
install                 install a package or packages on your system
remove                  remove a package or packages from your system
upgrade                 upgrade a package or packages on your system
repolist                display the configured software repositories
module                  interact with modular content
""",
}

_LSPCI_KVM = ("00:00.0 Host bridge: Intel Corporation 440FX - 82441FX PMC\n"
              "00:01.0 ISA bridge: Intel Corporation 82371SB PIIX3 ISA\n"
              "00:01.1 IDE interface: Intel Corporation 82371SB PIIX3 IDE\n"
              "00:02.0 VGA compatible controller: Device 1234:1111\n")


def package_manager_txtcmds(persona, ident):
    p = ident.release.packages
    if persona.family in ("ubuntu", "debian"):
        arch = deb_arch(persona)
        return {
            "usr/bin/apt": _APT_HELP.format(version=p["apt"], arch=arch),
            "usr/bin/apt-get": f"apt {p['apt']} ({arch})\n",
            "usr/bin/dpkg": f"Debian 'dpkg' package management program version {p['dpkg']} ({arch}).\n",
        }
    if persona.family in ("fedora", "rhel"):
        dnf = _DNF_HELP[persona.family].format(version=p["dnf"])
        return {"usr/bin/dnf": dnf, "usr/bin/yum": dnf, "usr/bin/rpm": f"RPM version {p['rpm']}\n"}
    return {}


def command_inventory_txtcmds(persona):
    return {
        "usr/bin/lspci": _LSPCI_KVM,
        "usr/bin/sudo": "sudo: a password is required\n",
        "usr/sbin/service": "Usage: service < option > | --status-all | [ service_name [ command | --full-restart ] ]\n",
    }


def server_processes(persona, ident):
    user = persona.user
    if persona.family == "ubuntu":
        entries = [
            ps_entry("root", 0, "/sbin/init", mem=0.6, vsz=168520, rss=8140, stat="Ss"),
            ps_entry("root", 0, "/lib/systemd/systemd-journald", mem=0.3, vsz=40632, rss=5192, stat="Ss"),
            ps_entry("root", 0, "/lib/systemd/systemd-udevd", mem=0.2, vsz=28844, rss=3588, stat="Ss"),
            ps_entry("systemd+", 0, "/lib/systemd/systemd-networkd", mem=0.2, vsz=24148, rss=4140, stat="Ss"),
            ps_entry("message+", 0, "/usr/bin/dbus-daemon --system --address=systemd:", mem=0.1, vsz=9240, rss=2984, stat="Ss"),
            ps_entry("root", 0, "sshd: /usr/sbin/sshd -D [listener] 0 of 10-100 startups", mem=0.2, vsz=15492, rss=4980, stat="Ss"),
            ps_entry("root", 0, "/usr/sbin/cron -f -P", mem=0.1, vsz=6816, rss=2196, stat="Ss"),
            ps_entry("root", 0, "/usr/bin/python3 /usr/share/unattended-upgrades/unattended-upgrade-shutdown --wait-for-signal", mem=0.2, vsz=27520, rss=6420, stat="S"),
            ps_entry(user, 0, "-bash", mem=0.1, vsz=8688, rss=3376, stat="Ss", tty="pts/0"),
        ]
    elif persona.family == "fedora":
        entries = [
            ps_entry("root", 0, "/usr/lib/systemd/systemd --switched-root --system --deserialize 31", mem=0.6, vsz=177248, rss=8120, stat="Ss"),
            ps_entry("root", 0, "/usr/lib/systemd/systemd-journald", mem=0.3, vsz=45328, rss=5120, stat="Ss"),
            ps_entry("root", 0, "/usr/lib/systemd/systemd-udevd", mem=0.2, vsz=28576, rss=3776, stat="Ss"),
            ps_entry("dbus", 0, "/usr/bin/dbus-broker-launch --scope system --audit", mem=0.2, vsz=12840, rss=2560, stat="Ss"),
            ps_entry("root", 0, "/usr/sbin/sshd -D", mem=0.2, vsz=19352, rss=4376, stat="Ss"),
            ps_entry("root", 0, "/usr/sbin/crond -n", mem=0.1, vsz=7312, rss=1712, stat="Ss"),
            ps_entry(user, 0, "-bash", mem=0.1, vsz=9456, rss=3240, stat="Ss", tty="pts/0"),
        ]
    elif persona.family == "rhel":
        entries = [
            ps_entry("root", 0, "/usr/lib/systemd/systemd --switched-root --system --deserialize 30", mem=0.5, vsz=178108, rss=8260, stat="Ss"),
            ps_entry("root", 0, "/usr/lib/systemd/systemd-journald", mem=0.3, vsz=45452, rss=5384, stat="Ss"),
            ps_entry("root", 0, "/usr/lib/systemd/systemd-udevd", mem=0.2, vsz=28964, rss=3900, stat="Ss"),
            ps_entry("dbus", 0, "/usr/bin/dbus-broker-launch --scope system --audit", mem=0.1, vsz=12844, rss=2636, stat="Ss"),
            ps_entry("root", 0, "/usr/sbin/chronyd -F 2", mem=0.2, vsz=29184, rss=3508, stat="S"),
            ps_entry("root", 0, "sshd: /usr/sbin/sshd -D [listener] 0 of 10-100 startups", mem=0.2, vsz=21432, rss=5160, stat="Ss"),
            ps_entry(user, 0, "-bash", mem=0.1, vsz=9632, rss=3428, stat="Ss", tty="pts/0"),
        ]
    else:
        entries = [
            ps_entry("root", 0, "/lib/systemd/systemd --system --deserialize=31", mem=0.6, vsz=166200, rss=7904, stat="Ss"),
            ps_entry("root", 0, "/lib/systemd/systemd-journald", mem=0.3, vsz=40376, rss=5028, stat="Ss"),
            ps_entry("root", 0, "/lib/systemd/systemd-udevd", mem=0.2, vsz=28772, rss=3512, stat="Ss"),
            ps_entry("systemd+", 0, "/lib/systemd/systemd-networkd", mem=0.2, vsz=24124, rss=4072, stat="Ss"),
            ps_entry("message+", 0, "/usr/bin/dbus-daemon --system --address=systemd:", mem=0.1, vsz=9192, rss=2900, stat="Ss"),
            ps_entry("root", 0, "sshd: /usr/sbin/sshd -D [listener] 0 of 10-100 startups", mem=0.2, vsz=15824, rss=5044, stat="Ss"),
            ps_entry("root", 0, "/usr/sbin/cron -f", mem=0.1, vsz=6812, rss=2180, stat="Ss"),
            ps_entry(user, 0, "-bash", mem=0.1, vsz=8672, rss=3312, stat="Ss", tty="pts/0"),
        ]
    return assign_processes(ident, entries)


# --- package logs ---------------------------------------------------------------

def _log_entries(ident, purpose):
    """2-5 (kind, names, time) entries between installation and now: installs of
    packages of the release, the upgrades of release.extra["upgrades"] last."""
    r = ident.release
    rng = rng_for(ident, purpose)
    upgrades = r.extra.get("upgrades", {})
    installs = [n for n in sorted(r.packages) if n not in TOOL_PACKAGES and n not in upgrades]
    count = rng.randint(2, 5)
    entries = [("install", [name]) for name in rng.sample(installs, min(len(installs), count - bool(upgrades)))]
    if upgrades:
        entries.append(("upgrade", sorted(upgrades)))
    times = sorted(rng.randint(ident.installed_at, ident.now - 3600) for _ in entries)
    return [(kind, names, ts, rng.randint(4, 40)) for (kind, names), ts in zip(entries, times)]


def apt_history(persona, ident):
    r = ident.release
    arch = deb_arch(persona)
    upgrades = r.extra.get("upgrades", {})
    blocks = []
    for kind, names, ts, took in _log_entries(ident, "apt-history"):
        if kind == "install":
            command = f"apt-get install -y {' '.join(names)}"
            body = "Install: " + ", ".join(f"{n}:{arch} ({r.packages[n]})" for n in names)
        else:
            command = "apt upgrade"
            body = "Upgrade: " + ", ".join(f"{n}:{arch} ({upgrades[n]}, {r.packages[n]})" for n in names)
        blocks.append(f"Start-Date: {gm(ts, '%Y-%m-%d  %H:%M:%S')}\n"
                      f"Commandline: {command}\n"
                      f"Requested-By: {persona.user} ({persona.uid})\n"
                      f"{body}\n"
                      f"End-Date: {gm(ts + took, '%Y-%m-%d  %H:%M:%S')}\n")
    return "\n" + "\n".join(blocks)


def dnf_log(persona, ident):
    r = ident.release
    arch = persona.hardware_platform
    upgrades = r.extra.get("upgrades", {})
    lines = []
    for kind, names, ts, took in _log_entries(ident, "dnf-log"):
        stamp = gm(ts, "%Y-%m-%dT%H:%M:%S+0000")
        done = gm(ts + took, "%Y-%m-%dT%H:%M:%S+0000")
        command = f"dnf install -y {' '.join(names)}" if kind == "install" else "dnf upgrade -y"
        lines += [f"{stamp} INFO --- logging initialized ---",
                  f"{stamp} DDEBUG Command: {command}",
                  f"{stamp} DDEBUG Installroot: /"]
        for n in names:
            if kind == "install":
                lines.append(f"{done} SUBDEBUG Installed: {n}-{r.packages[n]}.{arch}")
            else:
                lines.append(f"{done} SUBDEBUG Upgrade: {n}-{r.packages[n]}.{arch}")
                lines.append(f"{done} SUBDEBUG Upgraded: {n}-{upgrades[n]}.{arch}")
        lines.append(f"{done} INFO Complete!")
    return "\n".join(lines) + "\n"


# --- builders -------------------------------------------------------------------

def _gecos(user):
    return user.replace("-", " ").title()


def _server_content(persona, ident, files, tasks, run_lock=False):
    hw = ident.hardware
    processes = server_processes(persona, ident)
    all_files = base_files(persona, ident,
                           server_passwd(persona.user, persona.uid, persona.gid, _gecos(persona.user), persona.shell),
                           server_group(persona.user, persona.gid),
                           server_shadow(persona.user, ident))
    all_files.update(files)
    txtcmds = base_txtcmds(persona, ident,
                           df=server_df(ident, run_lock=run_lock),
                           mount=server_mount(ident, run_lock=run_lock),
                           lscpu_text=lscpu(persona, hw, op_modes="32-bit, 64-bit"),
                           top=server_top(ident, processes, tasks))
    txtcmds.update(package_manager_txtcmds(persona, ident))
    txtcmds.update(command_inventory_txtcmds(persona))
    return Content(files=all_files, txtcmds=txtcmds, processes=processes)


def build_ubuntu(persona, ident):
    r = ident.release
    code, name = r.extra["codename"], r.extra["codename_long"]
    files = {
        "etc/os-release": f"""PRETTY_NAME="Ubuntu {r.os_version}"
NAME="Ubuntu"
VERSION_ID="{r.os_version_id}"
VERSION="{r.os_version} ({name})"
VERSION_CODENAME={code}
ID=ubuntu
ID_LIKE=debian
HOME_URL="https://www.ubuntu.com/"
SUPPORT_URL="https://help.ubuntu.com/"
BUG_REPORT_URL="https://bugs.launchpad.net/ubuntu/"
PRIVACY_POLICY_URL="https://www.ubuntu.com/legal/terms-and-policies/privacy-policy"
UBUNTU_CODENAME={code}
""",
        "etc/lsb-release": f"""DISTRIB_ID=Ubuntu
DISTRIB_RELEASE={r.os_version_id}
DISTRIB_CODENAME={code}
DISTRIB_DESCRIPTION="Ubuntu {r.os_version}"
""",
        "etc/debian_version": r.extra["debian_version"] + "\n",
        "etc/issue": f"Ubuntu {r.os_version} \\n \\l\n",
        "etc/issue.net": f"Ubuntu {r.os_version}\n",
        "etc/motd": f"Welcome to Ubuntu {r.os_version} (GNU/Linux {r.kernel_version} {persona.hardware_platform})\n",
        "etc/apt/sources.list": f"""deb http://archive.ubuntu.com/ubuntu {code} main restricted
deb http://archive.ubuntu.com/ubuntu {code}-updates main restricted
deb http://archive.ubuntu.com/ubuntu {code} universe
deb http://security.ubuntu.com/ubuntu {code}-security main restricted
deb http://security.ubuntu.com/ubuntu {code}-security universe
""",
        "var/log/apt/history.log": apt_history(persona, ident),
    }
    return _server_content(persona, ident, files, tasks=93, run_lock=True)


def build_debian(persona, ident):
    r = ident.release
    files = {
        "etc/os-release": f"""PRETTY_NAME="Debian GNU/Linux {r.os_version}"
NAME="Debian GNU/Linux"
VERSION_ID="{r.os_version_id}"
VERSION="{r.os_version}"
VERSION_CODENAME={r.extra["codename"]}
ID=debian
HOME_URL="https://www.debian.org/"
SUPPORT_URL="https://www.debian.org/support"
BUG_REPORT_URL="https://bugs.debian.org/"
""",
        "etc/debian_version": r.extra["debian_version"] + "\n",
        "etc/issue": f"Debian GNU/Linux {r.os_version_id} \\n \\l\n",
        "etc/issue.net": f"Debian GNU/Linux {r.os_version_id}\n",
        "etc/motd": f"Linux {ident.hostname} {r.kernel_version} {r.kernel_build} {persona.hardware_platform}\n",
        "var/log/apt/history.log": apt_history(persona, ident),
    }
    return _server_content(persona, ident, files, tasks=87)


def build_fedora(persona, ident):
    r = ident.release
    variant = r.extra["variant"]
    files = {
        "etc/os-release": f"""NAME="Fedora Linux"
VERSION="{r.os_version}"
ID=fedora
VERSION_ID={r.os_version_id}
PLATFORM_ID="platform:f{r.os_version_id}"
PRETTY_NAME="Fedora Linux {r.os_version}"
ANSI_COLOR="0;38;2;60;110;180"
HOME_URL="https://fedoraproject.org/"
BUG_REPORT_URL="https://bugzilla.redhat.com/"
VARIANT="{variant}"
VARIANT_ID={variant.split()[0].lower()}
""",
        "etc/fedora-release": f"Fedora release {r.os_version_id} ({r.extra['codename']})\n",
        "etc/issue": f"Fedora release {r.os_version_id} ({r.extra['codename']}) \\n \\l\n",
        "etc/issue.net": f"Fedora release {r.os_version_id} ({r.extra['codename']})\n",
        "etc/motd": f"Fedora Linux {r.os_version}\n",
        "var/log/dnf.log": dnf_log(persona, ident),
    }
    return _server_content(persona, ident, files, tasks=87)


def build_rhel(persona, ident):
    r = ident.release
    major = r.os_version_id.split(".")[0]
    files = {
        "etc/os-release": f"""NAME="Red Hat Enterprise Linux"
VERSION="{r.os_version}"
ID="rhel"
ID_LIKE="fedora"
VERSION_ID="{r.os_version_id}"
PLATFORM_ID="platform:el{major}"
PRETTY_NAME="Red Hat Enterprise Linux {r.os_version}"
ANSI_COLOR="0;31"
CPE_NAME="cpe:/o:redhat:enterprise_linux:{major}::baseos"
HOME_URL="https://www.redhat.com/"
BUG_REPORT_URL="https://bugzilla.redhat.com/"
""",
        "etc/redhat-release": f"Red Hat Enterprise Linux release {r.os_version}\n",
        "etc/issue": "\\S\nKernel \\r on an \\m\n",
        "etc/issue.net": "\\S\nKernel \\r on an \\m\n",
        "etc/motd": f"Red Hat Enterprise Linux {r.os_version}\n",
        "var/log/dnf.log": dnf_log(persona, ident),
    }
    return _server_content(persona, ident, files, tasks=87)
