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
# x86_64 only: the dynamic loader and the multiarch directory of Debian
X86_PATHS = ("lib64", "usr/lib64", "usr/bin/x86_64", "usr/bin/ld.so")
MULTIARCH = "x86_64-linux-gnu"
TRIPLETS = {"x86_64": "x86_64-linux-gnu", "aarch64": "aarch64-linux-gnu", "mips": "mipsel-linux-gnu",
            "armv7l": "arm-linux-gnueabihf"}
# systemd: servers, Synology DSM 7 and the Debian 9 based EdgeOS 2 boot with it
SYSTEMD_PATHS = ("etc/systemd", "usr/lib/systemd", "var/lib/systemd")
SYSTEMD_DEVICES = frozenset({"synology", "ubiquiti"})
# OpenSSH: not on a dropbear system
OPENSSH_PATHS = ("etc/ssh", "etc/default/ssh", "etc/init.d/ssh", "usr/lib/openssh", "usr/sbin/sshd",
                 "usr/share/openssh", "usr/bin/ssh-*", "usr/bin/scp", "usr/bin/sftp",
                 "etc/systemd/system/sshd.service", "etc/systemd/system/multi-user.target.wants/ssh.service")
# Python 3.11 and Perl of Cowrie's tree: renamed to the persona's Python, removed where none is
PYTHON_PATHS = ("etc/python3", "etc/python3.11", "usr/lib/python3", "usr/lib/python3.11", "usr/share/python3",
                "usr/bin/py3*", "usr/bin/pydoc3*", "usr/bin/pdb3*", "usr/bin/pygettext3*", "usr/bin/python3*",
                "var/lib/python")
PERL_PATHS = ("usr/share/perl5", "usr/bin/perl*", "usr/lib/x86_64-linux-gnu/perl*")
# /proc/modules of Cowrie's tree lists the modules of a Debian 6.x container host
PROC_MODULES = "proc/modules"

# interpreters of the server releases (codename, or the family for Red Hat): Perl and its
# copyright year of `perl -v`, Python 3 (2.7 on CentOS 7, which has no python3); unverified: the
# copyright years of 5.38.2 / 5.40.1 and the Python 3.9 of each RHEL 9 minor
PERL = {"noble": ("5.38.2", "2023"), "jammy": ("5.34.0", "2021"), "trixie": ("5.40.1", "2025"),
        "bookworm": ("5.36.0", "2022"), "rhel": ("5.32.1", "2021"), "centos": ("5.16.3", "2012")}
PYTHON = {"noble": "3.12.3", "jammy": "3.10.12", "trixie": "3.13.5", "bookworm": "3.11.2",
          "rhel": "3.9.21", "centos": "2.7.5"}
RHEL_PYTHON = {"9.4": "3.9.18", "9.5": "3.9.19", "9.6": "3.9.21"}
# sudo of the releases without a sudo package in the catalog; the sudoers grammar of `sudo -V`
#   (unverified: 46 for 1.8.23, 48 for 1.9.0 to 1.9.11, 50 from 1.9.12)
SUDO = {"noble": "1.9.15p5", "jammy": "1.9.9", "rhel": "1.9.5p2", "centos": "1.8.23"}
# CentOS 7 yum loads fastestmirror only
YUM_PLUGINS = "fastestmirror"
SERVICE_STATUS = "service-status.txt"
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
SERVER_COMMANDS = ("sudo", "service")
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
        paths += DEBIAN_PATHS
    if persona.hardware_platform != "x86_64":
        paths += X86_PATHS
    if not has_systemd(persona):
        paths += SYSTEMD_PATHS
    if persona.ssh_profile.startswith("dropbear"):
        paths += OPENSSH_PATHS
    if triplet(persona) is None:
        paths += (f"usr/lib/{MULTIARCH}",)
    if persona.kind == "device":
        paths += PYTHON_PATHS + PERL_PATHS + (PROC_MODULES,)
    elif persona.family == "centos":
        paths += PYTHON_PATHS
    return paths


def triplet(persona):
    """The multiarch directory of a Debian system, None for other systems (no such directory)."""
    return TRIPLETS.get(persona.hardware_platform) if has_dpkg(persona) else None


def _codename(persona, ident):
    return persona.family if persona.family in ("rhel", "centos") else ident.release.extra.get("codename")


def _upstream(version):
    """The upstream part of a package version: 1:2.43.0-1ubuntu7.3 -> 2.43.0."""
    return version.split(":", 1)[-1].split("-", 1)[0]


def tools(persona, ident):
    """{"perl": (version, copyright year), "python": version, "git": version} of a server, {} on devices."""
    if persona.kind != "server":
        return {}
    code = _codename(persona, ident)
    python = PYTHON[code]
    if persona.family == "rhel":
        python = RHEL_PYTHON.get(ident.release.os_version_id, python)
    return {"perl": PERL[code], "python": python, "git": _upstream(ident.release.packages["git"])}


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
    """The commands of Cowrie's Python a server has as programs: perl, git, its Python,
    sudo, service and its package manager."""
    found = tools(persona, ident)
    if not found:
        return ()
    paths = ["usr/bin/git", "usr/bin/perl"]
    name = python3_name(found["python"])
    paths.append(f"usr/bin/{name}" if name else "usr/bin/python2.7")
    paths += [command_path(command) for command in python_answers(persona)]
    return tuple(paths)


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


def python_links(persona, ident):
    """(link, target) of the unversioned Python names: python3 -> python3.12; on CentOS 7
    python -> python2 -> python2.7 (targets from the root, as Cowrie's pickle stores them)."""
    found = tools(persona, ident)
    if not found:
        return ()
    name = python3_name(found["python"])
    if name:
        return (("usr/bin/python3", f"usr/bin/{name}"),)
    return (("usr/bin/python2", "usr/bin/python2.7"), ("usr/bin/python", "usr/bin/python2"))


def perl_archname(persona):
    if persona.family in ("rhel", "centos"):
        return f"{persona.hardware_platform}-linux-thread-multi"
    return f"{TRIPLETS[persona.hardware_platform]}-thread-multi"


def shell_keys(persona, ident, files, runtime_dir):
    """[shell] keys of the patched perl.py, python.py, git.py, sudo.py, service.py, apt.py and
    yum.py ({} on devices). files: the persona's files (its apt sources)."""
    found = tools(persona, ident)
    if not found:
        return {}
    perl, year = found["perl"]
    sudo = sudo_version(persona, ident)
    keys = {"perl_version": perl, "perl_archname": perl_archname(persona), "perl_copyright": year,
            "python_version": found["python"], "git_version": found["git"],
            "sudo_version": sudo, "sudo_grammar": sudo_grammar(sudo),
            "service_status_all": f"{runtime_dir}/{SERVICE_STATUS}"}
    if has_dpkg(persona):
        keys.update({"apt_version": ident.release.packages["apt"], "deb_arch": deb_arch(persona),
                     "apt_sources": ",".join(f"{url} {suite}" for url, suite in apt_sources(files))})
    if persona.family == "centos":
        keys["yum_plugins"] = YUM_PLUGINS
    return keys


def python_commands(persona, ident):
    """The Python names Cowrie must not answer: python on a system with Python 3 only,
    python3 on CentOS 7 (Python 2 only); both on devices."""
    found = tools(persona, ident)
    if not found:
        return ("python", "python3")
    return ("python",) if python3_name(found["python"]) else ("python3",)


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


def proc_modules(persona, ident):
    """/proc/modules of a server (devices have none): the modules of its kind of
    machine; addresses as a reader without CAP_SYSLOG sees them (kptr_restrict)."""
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


def modules_problems(text):
    """A module named as a dependency is listed itself; the format of the kernel."""
    out = []
    names = set()
    for line in text.splitlines():
        fields = line.split()
        if len(fields) != 6 or fields[4] != "Live" or not re.fullmatch(r"0x[0-9a-f]{16}", fields[5]):
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
