"""Server personas (bash, systemd): package managers, command inventory,
processes, package logs and one builder per family."""
import calendar
import time

from .common import (assign_processes, base_files, base_txtcmds, deb_arch, el_group, el_home_files, el_hosts,
                     el_passwd, el_shadow, gm, lscpu, pi_partuuid, ps_entry, rng_for, root_uuid, server_df,
                     server_group, server_mount, server_passwd, server_shadow, server_top, shadow_ctime)
from .identity import log_window
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

_DNF_HELP = """dnf {version}
usage: dnf [options] COMMAND

List of Main Commands:
install                 install a package or packages on your system
remove                  remove a package or packages from your system
upgrade                 upgrade a package or packages on your system
repolist                display the configured software repositories
module                  interact with modular content
"""

_YUM_HELP = """Loaded plugins: fastestmirror
You need to give some command
Usage: yum [options] COMMAND

List of Commands:

check          Check for problems in the rpmdb
check-update   Check for available package updates
clean          Remove cached data
erase          Remove a package or packages from your system
info           Display details about a package or group of packages
install        Install a package or packages on your system
list           List a package or groups of packages
repolist       Display the configured software repositories
update         Update a package or packages on your system
"""

_LSPCI_KVM = ("00:00.0 Host bridge: Intel Corporation 440FX - 82441FX PMC [Natoma] (rev 02)\n"
              "00:01.0 ISA bridge: Intel Corporation 82371SB PIIX3 ISA [Natoma/Triton II]\n"
              "00:01.1 IDE interface: Intel Corporation 82371SB PIIX3 IDE [Natoma/Triton II]\n"
              "00:01.3 Bridge: Intel Corporation 82371AB/EB/MB PIIX4 ACPI (rev 03)\n"
              "00:02.0 VGA compatible controller: Device 1234:1111 (rev 02)\n"
              "00:03.0 Ethernet controller: Red Hat, Inc. Virtio network device\n")
_LSPCI_VIRTIO_DISK = {"/dev/sd": "00:04.0 SCSI storage controller: Red Hat, Inc. Virtio SCSI\n",
                      "/dev/vd": "00:04.0 SCSI storage controller: Red Hat, Inc. Virtio block device\n"}
# AWS Nitro (t3 / m6a): ENA network, EBS volumes as NVMe; unverified: the exact list of a t3/m6a guest
_LSPCI_AWS = ("00:00.0 Host bridge: Intel Corporation 440FX - 82441FX PMC [Natoma]\n"
              "00:01.0 ISA bridge: Intel Corporation 82371SB PIIX3 ISA [Natoma/Triton II]\n"
              "00:01.3 Non-VGA unclassified device: Intel Corporation 82371AB/EB/MB PIIX4 ACPI (rev 08)\n"
              "00:03.0 VGA compatible controller: Amazon.com, Inc. Device 1111\n"
              "00:04.0 Non-Volatile memory controller: Amazon.com, Inc. NVMe EBS Controller\n"
              "00:05.0 Ethernet controller: Amazon.com, Inc. Elastic Network Adapter (ENA)\n")
# bare-metal AMD servers: root complex of the CPU generation, NVMe boot disk, ConnectX NIC (unverified: typical)
_LSPCI_BARE = {
    "Rome": "00:00.0 Host bridge: Advanced Micro Devices, Inc. [AMD] Starship/Matisse Root Complex\n",
    "Genoa": "00:00.0 Host bridge: Advanced Micro Devices, Inc. [AMD] Genoa/Bergamo Root Complex\n",
}
_LSPCI_BARE_DEVICES = ("01:00.0 Non-Volatile memory controller: Samsung Electronics Co Ltd NVMe SSD Controller "
                       "PM9A1/PM9A3/980PRO\n"
                       "41:00.0 Ethernet controller: Mellanox Technologies MT28908 Family [ConnectX-6]\n")
# unverified: the Pi 5 domain numbering of lspci
_LSPCI_PI = {
    "Raspberry Pi 4 Model B Rev 1.5": ("00:00.0 PCI bridge: Broadcom Inc. and subsidiaries BCM2711 PCIe Bridge (rev 20)\n"
                                       "01:00.0 USB controller: VIA Technologies, Inc. VL805/806 xHCI USB 3.0 "
                                       "Controller (rev 01)\n"),
    "Raspberry Pi 5 Model B Rev 1.0": ("0001:00:00.0 PCI bridge: Broadcom Inc. and subsidiaries BCM2712 PCIe Bridge "
                                       "(rev 21)\n"
                                       "0001:01:00.0 Ethernet controller: Raspberry Pi Ltd RP1 PCIe 2.0 South Bridge\n"),
}

# GPU name -> lspci class and device, fan, idle power / cap (W), idle P-state, ECC, MIG column.
# lspci names from the PCI ID database; idle values are typical, not measured (unverified).
GPU_INFO = {
    "NVIDIA A100-SXM4-80GB": ("3D controller", "NVIDIA Corporation GA100 [A100 SXM4 80GB] (rev a1)",
                              "N/A", 62, 400, "P0", "0", "Disabled"),
    "NVIDIA L40S": ("3D controller", "NVIDIA Corporation AD102GL [L40S] (rev a1)",
                    "N/A", 35, 350, "P8", "0", "N/A"),
    # a GeForce card is a display controller, lspci calls it VGA (not 3D)
    "NVIDIA GeForce RTX 4090": ("VGA compatible controller", "NVIDIA Corporation AD102 [GeForce RTX 4090] (rev a1)",
                                "30%", 12, 450, "P8", "Off", "N/A"),
}
# PCI buses of the eight SXM4 slots of an HGX A100 board; one or two GPUs take the first ones
GPU_BUSES = ("07", "0f", "47", "4e", "87", "90", "b7", "bd")


def package_manager_txtcmds(persona, ident):
    p = ident.release.packages
    if persona.family in ("ubuntu", "debian", "raspios"):
        arch = deb_arch(persona)
        return {
            "usr/bin/apt": _APT_HELP.format(version=p["apt"], arch=arch),
            "usr/bin/apt-get": f"apt {p['apt']} ({arch})\n",
            "usr/bin/dpkg": f"Debian 'dpkg' package management program version {p['dpkg']} ({arch}).\n",
        }
    if persona.family == "rhel":
        dnf = _DNF_HELP.format(version=p["dnf"])
        return {"usr/bin/dnf": dnf, "usr/bin/yum": dnf, "usr/bin/rpm": f"RPM version {p['rpm']}\n"}
    if persona.family == "centos":
        return {"usr/bin/yum": _YUM_HELP, "usr/bin/rpm": f"RPM version {p['rpm']}\n"}
    return {}


def gpu_lspci(hw):
    lines = []
    for bus, name in zip(GPU_BUSES, hw.gpus):
        kind, device = GPU_INFO[name][:2]
        lines.append(f"{bus}:00.0 {kind}: {device}\n")
    return "".join(lines)


def root_disk(persona, ident):
    """(device df and mount show, fs type, mount options) of the root file system,
    as the provider's disks and the family's installer lay it out."""
    provider, family = ident.hardware.provider, persona.family
    el = family in ("rhel", "centos")
    part = "4" if family == "rhel" else "1"   # RHEL 9 images: BIOS boot, EFI, /boot, then /
    if provider == "pi":
        return "/dev/mmcblk0p2", "ext4", "rw,noatime"
    if provider == "aws":
        # Ubuntu's AMIs boot without initramfs, so the kernel names the root /dev/root
        dev = "/dev/root" if family == "ubuntu" else f"/dev/nvme0n1p{part}"
        return dev, ("xfs" if el else "ext4"), ("rw,relatime,seclabel,attr2,inode64,noquota" if el
                                                else "rw,relatime,discard,errors=remount-ro,commit=30")
    if provider in ("dgx", "gpu"):
        dev = "/dev/nvme0n1p2"
    elif provider == "do" or el:
        dev = f"/dev/vda{part}"
    else:
        dev = "/dev/sda1"
    if el:
        options = "rw,relatime,seclabel,attr2,inode64," + ("logbufs=8,logbsize=32k,noquota" if family == "rhel"
                                                             else "noquota")
        return dev, "xfs", options
    return dev, "ext4", "rw,relatime,errors=remount-ro"


def pi_cmdline(ident):
    """/boot/firmware/cmdline.txt as pi-gen writes it; the kernel gets the same line."""
    return (f"console=serial0,115200 console=tty1 root=PARTUUID={pi_partuuid(ident)}-02 rootfstype=ext4 "
            "fsck.repair=yes rootwait quiet splash plymouth.ignore-serial-consoles")


def boot_config(persona, ident):
    """(kernel command line, /etc/fstab) that fit root_disk(): the root= of the
    command line is the / entry of fstab, except on Ubuntu's AWS images, whose
    kernel mounts root=PARTUUID without initramfs (df shows /dev/root) while
    fstab names the cloudimg label. Unverified: the exact option lists."""
    r, provider, family = ident.release, ident.hardware.provider, persona.family
    kernel = r.kernel_version
    dev, fs, options = root_disk(persona, ident)
    uuid = root_uuid(ident)
    if provider == "pi":
        part = pi_partuuid(ident)
        return pi_cmdline(ident), ("proc            /proc           proc    defaults          0       0\n"
                                   f"PARTUUID={part}-01  /boot/firmware  vfat    defaults          0       2\n"
                                   f"PARTUUID={part}-02  /               ext4    defaults,noatime  0       1\n")
    if family == "ubuntu" and provider in ("aws", "kvm", "do"):
        fstab = ("LABEL=cloudimg-rootfs\t/\t ext4\tdiscard,errors=remount-ro\t0 1\n"
                 "LABEL=UEFI\t/boot/efi\tvfat\tumask=0077\t0 1\n")
        if provider == "aws":
            return (f"BOOT_IMAGE=/vmlinuz-{kernel} root=PARTUUID={root_uuid(ident, 'root-partuuid')} ro "
                    "console=tty1 console=ttyS0 nvme_core.io_timeout=4294967295 panic=-1"), fstab
        return f"BOOT_IMAGE=/vmlinuz-{kernel} root=LABEL=cloudimg-rootfs ro console=tty1 console=ttyS0", fstab
    if family == "rhel":
        return (f"BOOT_IMAGE=(hd0,gpt3)/vmlinuz-{kernel} root=UUID={uuid} console=tty0 console=ttyS0,115200n8 "
                "no_timer_check net.ifnames=0 crashkernel=1G-4G:192M,4G-64G:256M,64G-:512M"), \
            f"UUID={uuid}\t/\txfs\tdefaults\t0 0\n"
    if family == "centos":
        return (f"BOOT_IMAGE=/boot/vmlinuz-{kernel} root=UUID={uuid} ro console=tty0 console=ttyS0,115200n8 "
                "crashkernel=auto net.ifnames=0 console=ttyS0 LANG=en_US.UTF-8"), \
            f"UUID={uuid} /                       xfs     defaults        0 0\n"
    fstab = ("# /etc/fstab: static file system information.\n#\n"
             "# <file system> <mount point>   <type>  <options>       <dump>  <pass>\n"
             f"UUID={uuid} /               {fs}    {'errors=remount-ro' if family in ('debian', 'ubuntu') else 'defaults'} 0       1\n")
    return f"BOOT_IMAGE=/boot/vmlinuz-{kernel} root=UUID={uuid} ro quiet", fstab


def provider_lspci(persona, ident):
    hw = ident.hardware
    if hw.provider == "pi":
        return _LSPCI_PI[hw.board]
    if hw.provider == "aws":
        return _LSPCI_AWS
    if hw.provider in ("dgx", "gpu"):
        generation = "Genoa" if " 9" in hw.cpu_model else "Rome"
        return _LSPCI_BARE[generation] + _LSPCI_BARE_DEVICES
    dev = root_disk(persona, ident)[0]
    return _LSPCI_KVM + _LSPCI_VIRTIO_DISK["/dev/vd" if dev.startswith("/dev/vd") else "/dev/sd"]


def command_inventory_txtcmds(persona, ident):
    hw = ident.hardware
    lspci = provider_lspci(persona, ident) + gpu_lspci(hw)
    return {
        "usr/bin/lspci": lspci,
        "usr/bin/sudo": "sudo: a password is required\n",
        "usr/sbin/service": "Usage: service < option > | --status-all | [ service_name [ command | --full-restart ] ]\n",
    }


def _systemd_paths(ident):
    """Debian/Ubuntu releases after the /usr merge show /usr/lib/systemd in ps."""
    return "/usr/lib/systemd" if ident.release.extra.get("codename") in ("noble", "trixie") else "/lib/systemd"


def server_processes(persona, ident, extra=()):
    """ps of a freshly booted server: daemons from boot, the extra ones, the login shell last."""
    user, family, sd = persona.user, persona.family, _systemd_paths(ident)
    if family == "rhel":
        entries = [
            ps_entry("root", 0, "/usr/lib/systemd/systemd --switched-root --system --deserialize 31", mem=0.5, vsz=178108, rss=8260, stat="Ss"),
            ps_entry("root", 0, "/usr/lib/systemd/systemd-journald", mem=0.3, vsz=45452, rss=5384, stat="Ss"),
            ps_entry("root", 0, "/usr/lib/systemd/systemd-udevd", mem=0.2, vsz=28964, rss=3900, stat="Ss"),
            ps_entry("root", 0, "/sbin/auditd", mem=0.1, vsz=25012, rss=2288, stat="S<sl"),
            ps_entry("dbus", 0, "/usr/bin/dbus-broker-launch --scope system --audit", mem=0.1, vsz=12844, rss=2636, stat="Ss"),
            ps_entry("chrony", 0, "/usr/sbin/chronyd -F 2", mem=0.2, vsz=84564, rss=3508, stat="S"),
            ps_entry("root", 0, "/usr/sbin/NetworkManager --no-daemon", mem=0.9, vsz=258392, rss=18208, stat="Ssl"),
            ps_entry("root", 0, "/usr/lib/systemd/systemd-logind", mem=0.3, vsz=17736, rss=7492, stat="Ss"),
            ps_entry("root", 0, "sshd: /usr/sbin/sshd -D [listener] 0 of 10-100 startups", mem=0.2, vsz=16304, rss=9176, stat="Ss"),
            ps_entry("root", 0, "/usr/sbin/crond -n", mem=0.1, vsz=221304, rss=3544, stat="Ss"),
            ps_entry("root", 0, "/usr/sbin/rsyslogd -n", mem=0.3, vsz=160340, rss=6352, stat="Ssl"),
        ]
    elif family == "centos":
        entries = [
            ps_entry("root", 0, "/usr/lib/systemd/systemd --switched-root --system --deserialize 22", mem=0.3, vsz=193800, rss=6820, stat="Ss"),
            ps_entry("root", 0, "/usr/lib/systemd/systemd-journald", mem=0.2, vsz=39060, rss=4380, stat="Ss"),
            ps_entry("root", 0, "/usr/lib/systemd/systemd-udevd", mem=0.1, vsz=45740, rss=2384, stat="Ss"),
            ps_entry("root", 0, "/sbin/auditd", mem=0.0, vsz=55532, rss=1092, stat="S<sl"),
            ps_entry("root", 0, "/usr/lib/systemd/systemd-logind", mem=0.0, vsz=26376, rss=1744, stat="Ss"),
            ps_entry("dbus", 0, "/usr/bin/dbus-daemon --system --address=systemd: --nofork --nopidfile --systemd-activation", mem=0.1, vsz=58112, rss=2312, stat="Ssl"),
            ps_entry("polkitd", 0, "/usr/lib/polkit-1/polkitd --no-debug", mem=0.6, vsz=612236, rss=13360, stat="Ssl"),
            ps_entry("chrony", 0, "/usr/sbin/chronyd", mem=0.0, vsz=117804, rss=1772, stat="S"),
            ps_entry("root", 0, "/usr/sbin/NetworkManager --no-daemon", mem=0.4, vsz=614300, rss=9028, stat="Ssl"),
            ps_entry("root", 0, "/usr/sbin/crond -n", mem=0.0, vsz=126388, rss=1612, stat="Ss"),
            ps_entry("root", 0, "/usr/sbin/sshd -D", mem=0.2, vsz=112984, rss=4324, stat="Ss"),
            ps_entry("root", 0, "/usr/sbin/rsyslogd -n", mem=0.2, vsz=216404, rss=4964, stat="Ssl"),
            ps_entry("root", 0, "/usr/libexec/postfix/master -w", mem=0.1, vsz=89804, rss=2100, stat="Ss"),
            ps_entry("postfix", 0, "qmgr -l -t unix -u", mem=0.1, vsz=90084, rss=4044, stat="S"),
        ]
    elif family == "raspios":
        entries = [
            ps_entry("root", 0, "/sbin/init splash", mem=0.3, vsz=169520, rss=12416, stat="Ss"),
            ps_entry("root", 0, "/lib/systemd/systemd-journald", mem=0.5, vsz=49040, rss=19248, stat="Ss"),
            ps_entry("root", 0, "/lib/systemd/systemd-udevd", mem=0.1, vsz=26880, rss=6744, stat="Ss"),
            ps_entry("avahi", 0, f"avahi-daemon: running [{ident.hostname}.local]", mem=0.1, vsz=7584, rss=3168, stat="Ss"),
            ps_entry("root", 0, "/usr/sbin/cron -f", mem=0.0, vsz=6740, rss=2560, stat="Ss"),
            ps_entry("message+", 0, "/usr/bin/dbus-daemon --system --address=systemd: --nofork --nopidfile --systemd-activation --syslog-only", mem=0.1, vsz=9112, rss=4740, stat="Ss"),
            ps_entry("root", 0, "/usr/sbin/NetworkManager --no-daemon", mem=0.4, vsz=258956, rss=17920, stat="Ssl"),
            ps_entry("root", 0, "/lib/systemd/systemd-logind", mem=0.2, vsz=17456, rss=7552, stat="Ss"),
            ps_entry("avahi", 0, "avahi-daemon: chroot helper", mem=0.0, vsz=7424, rss=1408, stat="S"),
            ps_entry("root", 0, "/sbin/wpa_supplicant -u -s -O DIR=/run/wpa_supplicant GROUP=netdev", mem=0.2, vsz=16752, rss=8576, stat="Ss"),
            ps_entry("root", 0, "/usr/sbin/ModemManager", mem=0.2, vsz=239500, rss=9984, stat="Ssl"),
            ps_entry("root", 0, "/usr/libexec/bluetooth/bluetoothd", mem=0.1, vsz=9616, rss=5504, stat="Ss"),
            ps_entry("root", 0, "sshd: /usr/sbin/sshd -D [listener] 0 of 10-100 startups", mem=0.2, vsz=15492, rss=8448, stat="Ss"),
        ]
    elif family == "ubuntu":
        entries = [
            ps_entry("root", 0, "/sbin/init", mem=0.6, vsz=168520, rss=8140, stat="Ss"),
            ps_entry("root", 0, f"{sd}/systemd-journald", mem=0.3, vsz=40632, rss=5192, stat="Ss"),
            ps_entry("root", 0, f"{sd}/systemd-udevd", mem=0.2, vsz=28844, rss=3588, stat="Ss"),
            ps_entry("systemd+", 0, f"{sd}/systemd-networkd", mem=0.2, vsz=24148, rss=4140, stat="Ss"),
            ps_entry("systemd+", 0, f"{sd}/systemd-resolved", mem=0.3, vsz=25552, rss=7896, stat="Ss"),
            ps_entry("message+", 0, "/usr/bin/dbus-daemon --system --address=systemd: --nofork --nopidfile --systemd-activation --syslog-only", mem=0.1, vsz=9240, rss=2984, stat="Ss"),
            ps_entry("root", 0, f"{sd}/systemd-logind", mem=0.2, vsz=17972, rss=6380, stat="Ss"),
            ps_entry("syslog", 0, "/usr/sbin/rsyslogd -n -iNONE", mem=0.1, vsz=222508, rss=4536, stat="Ssl"),
            ps_entry("root", 0, "/usr/sbin/cron -f -P", mem=0.1, vsz=6816, rss=2196, stat="Ss"),
            ps_entry("root", 0, "sshd: /usr/sbin/sshd -D [listener] 0 of 10-100 startups", mem=0.2, vsz=15492, rss=4980, stat="Ss"),
            ps_entry("root", 0, "/usr/lib/snapd/snapd", mem=1.6, vsz=1986784, rss=31672, stat="Ssl"),
            ps_entry("root", 0, "/usr/bin/python3 /usr/share/unattended-upgrades/unattended-upgrade-shutdown --wait-for-signal", mem=0.2, vsz=27520, rss=6420, stat="Ssl"),
        ]
    else:  # debian
        entries = [
            ps_entry("root", 0, "/sbin/init", mem=0.6, vsz=166200, rss=7904, stat="Ss"),
            ps_entry("root", 0, f"{sd}/systemd-journald", mem=0.3, vsz=40376, rss=5028, stat="Ss"),
            ps_entry("root", 0, f"{sd}/systemd-udevd", mem=0.2, vsz=28772, rss=3512, stat="Ss"),
            ps_entry("systemd+", 0, f"{sd}/systemd-networkd", mem=0.2, vsz=24124, rss=4072, stat="Ss"),
            ps_entry("message+", 0, "/usr/bin/dbus-daemon --system --address=systemd: --nofork --nopidfile --systemd-activation --syslog-only", mem=0.1, vsz=9192, rss=2900, stat="Ss"),
            ps_entry("root", 0, f"{sd}/systemd-logind", mem=0.2, vsz=17852, rss=6012, stat="Ss"),
            ps_entry("root", 0, "sshd: /usr/sbin/sshd -D [listener] 0 of 10-100 startups", mem=0.2, vsz=15824, rss=5044, stat="Ss"),
            ps_entry("root", 0, "/usr/sbin/cron -f", mem=0.1, vsz=6812, rss=2180, stat="Ss"),
        ]
    entries += list(extra)
    entries.append(ps_entry(user, 0, "-bash", mem=0.1, vsz=8688, rss=3376, stat="Ss", tty="pts/0"))
    return assign_processes(ident, entries)


# --- package logs ---------------------------------------------------------------

def _log_entries(ident, purpose):
    """2-5 (kind, names, start, seconds) entries, oldest first, only while the
    release's versions were the ones the mirror gave (identity.log_window): installs
    of its packages, and the upgrades of release.extra["upgrades"] before the boot
    (upgrading openssh restarts sshd, and ps shows the sshd of the boot). None for
    a system installed from an image after the next patch level came out."""
    r = ident.release
    rng = rng_for(ident, purpose)
    window = log_window(ident)
    if window is None:
        return []
    first, last = window
    upgrades = r.extra.get("upgrades", {})
    installs = [n for n in sorted(r.packages) if n not in TOOL_PACKAGES and n not in upgrades]
    count = rng.randint(2, 5)
    entries = []
    for name in rng.sample(installs, min(len(installs), count - bool(upgrades))):
        took = rng.randint(4, 40)
        start = rng.randint(first, max(first, last - took))
        entries.append(("install", [name], start, max(0, min(took, last - start))))
    upgrade_end = min(last, ident.booted_at)
    if upgrades and first <= upgrade_end:
        took = rng.randint(4, 40)
        start = rng.randint(first, max(first, upgrade_end - took))
        entries.append(("upgrade", sorted(upgrades), start, max(0, min(took, upgrade_end - start))))
    return sorted(entries, key=lambda e: e[2])


def _newest(ident, entries):
    """ctime of a log: its newest entry, the installation for an empty one."""
    return max((start + took for _, _, start, took in entries), default=ident.installed_at)


def apt_history(persona, ident):
    """(text of /var/log/apt/history.log, its ctime)."""
    r = ident.release
    arch = deb_arch(persona)
    upgrades = r.extra.get("upgrades", {})
    entries = _log_entries(ident, "apt-history")
    blocks = []
    for kind, names, ts, took in entries:
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
    return ("\n" + "\n".join(blocks)) if blocks else "", _newest(ident, entries)


def dnf_log(persona, ident):
    """(text of /var/log/dnf.log, its ctime)."""
    r = ident.release
    arch = persona.hardware_platform
    upgrades = r.extra.get("upgrades", {})
    entries = _log_entries(ident, "dnf-log")
    lines = []
    for kind, names, ts, took in entries:
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
    return ("\n".join(lines) + "\n") if lines else "", _newest(ident, entries)


def _yum_name(name, version, arch):
    """yum.log writes the epoch in front: 2:vim-enhanced-7.4.629-8.el7_9.x86_64."""
    epoch, _, plain = version.rpartition(":")
    return f"{epoch + ':' if epoch else ''}{name}-{plain}.{arch}"


def yum_logs(persona, ident):
    """({path: text}, {path: ctime}): yum.log has no year in its lines and is
    rotated yearly, so older entries are in yum.log-<next year>0101."""
    r = ident.release
    arch = persona.hardware_platform
    upgrades = r.extra.get("upgrades", {})
    rng = rng_for(ident, "yum-rotate")
    by_year = {}
    for kind, names, ts, took in _log_entries(ident, "yum-log"):
        for n in names:
            verb, version = ("Installed", r.packages[n]) if kind == "install" else ("Updated", r.packages[n])
            line = f"{gm(ts + took, '%b %d %H:%M:%S')} {verb}: {_yum_name(n, version, arch)}"
            by_year.setdefault(time.gmtime(ts + took).tm_year, []).append((ts + took, line))
    this_year = time.gmtime(ident.now).tm_year
    files, ctimes = {"var/log/yum.log": ""}, {}
    for year, rows in sorted(by_year.items()):
        text = "".join(line + "\n" for _, line in rows)
        if year == this_year:
            files["var/log/yum.log"] = text
            ctimes["var/log/yum.log"] = max(ts for ts, _ in rows)
        else:
            # logrotate runs from cron.daily in the first hours of the new year
            rotated = calendar.timegm((year + 1, 1, 1, 0, 0, 0)) + rng.randint(3 * 3600, 4 * 3600)
            files[f"var/log/yum.log-{year + 1}0101"] = text
            ctimes[f"var/log/yum.log-{year + 1}0101"] = min(rotated, ident.now)
    if "var/log/yum.log" not in ctimes:
        ctimes["var/log/yum.log"] = max(ctimes.values(), default=ident.installed_at)
    return files, ctimes


# --- GPU ---------------------------------------------------------------------------

def _uts_day(ts):
    """date(1) style: "Thu Oct  9 14:21:07 2026"."""
    return f"{gm(ts, '%a %b')} {time.gmtime(ts).tm_mday:>2} {gm(ts, '%H:%M:%S %Y')}"


def nvidia_smi(ident):
    r, hw = ident.release, ident.hardware
    rng = rng_for(ident, "nvidia-smi")
    drv, cuda = r.extra["nvidia_driver"], r.extra["cuda"]
    rule = "+" + "-" * 89 + "+"
    split = "+" + "-" * 41 + "+" + "-" * 24 + "+" + "-" * 22 + "+"
    row = lambda a, b, c: f"|{a:<41}|{b:<24}|{c:<22}|"  # noqa: E731
    lines = [
        f"{_uts_day(ident.now)}       ",
        rule,
        "|" + (f" NVIDIA-SMI {drv:<23}Driver Version: {drv:<15}CUDA Version: {cuda:<9}").ljust(89) + "|",
        "|" + "-" * 41 + "+" + "-" * 24 + "+" + "-" * 22 + "|",
        row(" GPU  Name                 Persistence-M", " Bus-Id          Disp.A", " Volatile Uncorr. ECC"),
        row(" Fan  Temp   Perf          Pwr:Usage/Cap", "           Memory-Usage", " GPU-Util  Compute M."),
        row("", "", "               MIG M."),
        "|" + "=" * 41 + "+" + "=" * 24 + "+" + "=" * 22 + "|",
    ]
    for i, (bus, name) in enumerate(zip(GPU_BUSES, hw.gpus)):
        _, _, fan, idle, cap, perf, ecc, mig = GPU_INFO[name]
        left = f" {fan:>3}  {rng.randint(29, 41):>3}C    {perf}"
        lines += [
            row(f"{i:>4}  {name}".ljust(37) + "Off ", f"{'00000000:' + bus + ':00.0':>19} Off ", f"{ecc:>21} "),
            row(left + f"{idle + rng.randint(0, 9)}W / {cap:>4}W ".rjust(41 - len(left)),
                f"{rng.randint(1, 4):>8}MiB / {hw.gpu_mem_mib:>6}MiB ", f"{0:>7}%{'Default':>13} "),
            row("", "", f"{mig:>21} "),
            split,
        ]
    lines += [
        " " * 89,
        rule,
        "|" + " Processes:".ljust(89) + "|",
        "|" + "  GPU   GI   CI        PID   Type   Process name                              GPU Memory".ljust(89) + "|",
        "|" + "        ID   ID                                                               Usage".ljust(89) + "|",
        "|" + "=" * 89 + "|",
        "|" + "  No running processes found".ljust(89) + "|",
        rule,
    ]
    return "\n".join(lines) + "\n"


def nvidia_version(ident):
    r = ident.release
    return (f"NVRM version: NVIDIA UNIX x86_64 Kernel Module  {r.extra['nvidia_driver']}  "
            f"{r.extra['nvidia_built']}\n"
            f"GCC version:  {r.extra['nvidia_gcc']} \n")


# --- builders -------------------------------------------------------------------

def _gecos(user):
    return user.replace("-", " ").title()


def _server_content(persona, ident, files, tasks, run_lock=False, accounts=None, home=None, extra_ps=(),
                    boot=None, ctimes=None):
    hw = ident.hardware
    root_dev, root_type, root_options = root_disk(persona, ident)
    cmdline, fstab = boot_config(persona, ident)
    processes = server_processes(persona, ident, extra_ps)
    passwd, group, shadow = accounts or (
        server_passwd(persona.user, persona.uid, persona.gid, _gecos(persona.user), persona.shell),
        server_group(persona.user, persona.gid),
        server_shadow(persona.user, ident))
    all_files = base_files(persona, ident, passwd, group, shadow, home=home)
    all_files["etc/fstab"] = fstab
    all_files.update(files)
    txtcmds = base_txtcmds(persona, ident, cmdline=cmdline,
                           df=server_df(ident, root_dev=root_dev, run_lock=run_lock, boot=boot),
                           mount=server_mount(ident, root_dev=root_dev, run_lock=run_lock, boot=boot,
                                              root_type=root_type, root_options=root_options),
                           lscpu_text=lscpu(persona, hw, op_modes="32-bit, 64-bit"),
                           top=server_top(ident, processes, tasks))
    txtcmds.update(package_manager_txtcmds(persona, ident))
    txtcmds.update(command_inventory_txtcmds(persona, ident))
    all_ctimes = {"etc/shadow": shadow_ctime(ident)}
    all_ctimes.update(ctimes or {})
    return Content(files=all_files, txtcmds=txtcmds, processes=processes, ctimes=all_ctimes)


def _ubuntu_files(persona, ident):
    r = ident.release
    code, name = r.extra["codename"], r.extra["codename_long"]
    history, history_ctime = apt_history(persona, ident)
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
""" + ("LOGO=ubuntu-logo\n" if code == "noble" else ""),
        "etc/lsb-release": f"""DISTRIB_ID=Ubuntu
DISTRIB_RELEASE={r.os_version_id}
DISTRIB_CODENAME={code}
DISTRIB_DESCRIPTION="Ubuntu {r.os_version}"
""",
        "etc/debian_version": r.extra["debian_version"] + "\n",
        "etc/issue": f"Ubuntu {r.os_version} \\n \\l\n\n",
        "etc/issue.net": f"Ubuntu {r.os_version}\n",
        "etc/motd": f"""Welcome to Ubuntu {r.os_version} (GNU/Linux {r.kernel_version} {persona.hardware_platform})

 * Documentation:  https://help.ubuntu.com
 * Management:     https://landscape.canonical.com
 * Support:        https://ubuntu.com/pro
""",
        "var/log/apt/history.log": history,
    }
    if code == "noble":
        # 24.04 keeps its sources in deb822 format
        files["etc/apt/sources.list"] = "# Ubuntu sources have moved to /etc/apt/sources.list.d/ubuntu.sources\n"
        files["etc/apt/sources.list.d/ubuntu.sources"] = f"""Types: deb
URIs: http://archive.ubuntu.com/ubuntu/
Suites: {code} {code}-updates {code}-backports
Components: main restricted universe multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg

Types: deb
URIs: http://security.ubuntu.com/ubuntu/
Suites: {code}-security
Components: main restricted universe multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
"""
    else:
        files["etc/apt/sources.list"] = f"""deb http://archive.ubuntu.com/ubuntu {code} main restricted
deb http://archive.ubuntu.com/ubuntu {code}-updates main restricted
deb http://archive.ubuntu.com/ubuntu {code} universe
deb http://security.ubuntu.com/ubuntu {code}-security main restricted
deb http://security.ubuntu.com/ubuntu {code}-security universe
"""
    return files, {"var/log/apt/history.log": history_ctime}


def build_ubuntu(persona, ident):
    files, ctimes = _ubuntu_files(persona, ident)
    return _server_content(persona, ident, files, tasks=93, run_lock=True, ctimes=ctimes)


def build_gpu_ubuntu(persona, ident):
    """Ubuntu with NVIDIA GPUs, Ollama and Jupyter: bait for miners and model theft."""
    user = persona.user
    files, ctimes = _ubuntu_files(persona, ident)
    home = f"home/{user}"
    hf, openai = ident.tokens["hf"], ident.tokens["openai"]
    files.update({
        "proc/driver/nvidia/version": nvidia_version(ident),
        f"{home}/.cache/huggingface/token": hf,
        f"{home}/.env": f"OPENAI_API_KEY={openai}\nHF_TOKEN={hf}\nOLLAMA_HOST=127.0.0.1:11434\n",
        f"{home}/.bash_history": "\n".join((
            "nvidia-smi",
            "conda activate torch",
            "python -c 'import torch; print(torch.cuda.device_count())'",
            "ollama pull llama3.1:8b",
            "ollama list",
            "huggingface-cli whoami",
            "jupyter-lab --no-browser --port=8888",
            "nvidia-smi",
        )) + "\n",
    })
    accounts = (
        server_passwd(user, persona.uid, persona.gid, _gecos(user), persona.shell,
                      extra="nvidia-persistenced:x:121:128:NVIDIA Persistence Daemon,,,:/nonexistent:/usr/sbin/nologin\n"
                            "ollama:x:996:996::/usr/share/ollama:/bin/false\n"),
        server_group(user, persona.gid, extra="nvidia-persistenced:x:128:\nollama:x:996:\n"),
        server_shadow(user, ident, extra=("nvidia-persistenced", "ollama")),
    )
    extra_ps = (
        ps_entry("nvidia-persistenced", 0, "/usr/bin/nvidia-persistenced --user nvidia-persistenced --no-persistence-mode --verbose",
                 mem=0.0, vsz=5348, rss=2016, stat="Ss"),
        ps_entry("ollama", 0, "/usr/local/bin/ollama serve", mem=0.0, vsz=2843216, rss=61440, stat="Ssl"),
        ps_entry(user, 0, "/opt/conda/bin/python /opt/conda/bin/jupyter-lab --no-browser --port=8888",
                 mem=0.0, vsz=1152664, rss=112836, stat="Ssl"),
    )
    content = _server_content(persona, ident, files, tasks=412, run_lock=True, accounts=accounts,
                              extra_ps=extra_ps, ctimes=ctimes)
    content.txtcmds["usr/bin/nvidia-smi"] = nvidia_smi(ident)
    return content


def _debian_motd(ident, persona):
    r = ident.release
    return (f"Linux {ident.hostname} {r.kernel_version} {r.kernel_build} {persona.hardware_platform}\n"
            "\n"
            "The programs included with the Debian GNU/Linux system are free software;\n"
            "the exact distribution terms for each program are described in the\n"
            "individual files in /usr/share/doc/*/copyright.\n"
            "\n"
            "Debian GNU/Linux comes with ABSOLUTELY NO WARRANTY, to the extent\n"
            "permitted by applicable law.\n")


def _debian_files(persona, ident):
    r = ident.release
    code = r.extra["codename"]
    history, history_ctime = apt_history(persona, ident)
    full = f"DEBIAN_VERSION_FULL={r.extra['debian_version']}\n" if r.extra.get("version_full") else ""
    files = {
        "etc/os-release": f"""PRETTY_NAME="Debian GNU/Linux {r.os_version}"
NAME="Debian GNU/Linux"
VERSION_ID="{r.os_version_id}"
VERSION="{r.os_version}"
VERSION_CODENAME={code}
{full}ID=debian
HOME_URL="https://www.debian.org/"
SUPPORT_URL="https://www.debian.org/support"
BUG_REPORT_URL="https://bugs.debian.org/"
""",
        "etc/debian_version": r.extra["debian_version"] + "\n",
        "etc/issue": f"Debian GNU/Linux {r.os_version_id} \\n \\l\n\n",
        "etc/issue.net": f"Debian GNU/Linux {r.os_version_id}\n",
        "etc/motd": _debian_motd(ident, persona),
        "var/log/apt/history.log": history,
    }
    return files, {"var/log/apt/history.log": history_ctime}


def build_debian(persona, ident):
    files, ctimes = _debian_files(persona, ident)
    code = ident.release.extra["codename"]
    if code == "trixie":
        files["etc/apt/sources.list.d/debian.sources"] = f"""Types: deb deb-src
URIs: https://deb.debian.org/debian
Suites: {code} {code}-updates {code}-backports
Components: main
Signed-By: /usr/share/keyrings/debian-archive-keyring.pgp

Types: deb deb-src
URIs: https://security.debian.org/debian-security
Suites: {code}-security
Components: main
Signed-By: /usr/share/keyrings/debian-archive-keyring.pgp
"""
    else:
        files["etc/apt/sources.list"] = f"""deb http://deb.debian.org/debian {code} main
deb http://deb.debian.org/debian-security {code}-security main
deb http://deb.debian.org/debian {code}-updates main
"""
    return _server_content(persona, ident, files, tasks=87, ctimes=ctimes)


_PI_CONFIG_TXT = """# For more options and information see
# http://rptl.io/configtxt
# Some settings may impact device functionality. See link above for details

# Uncomment some or all of these to enable the optional hardware interfaces
#dtparam=i2c_arm=on
#dtparam=i2s=on
#dtparam=spi=on

# Enable audio (loads snd_bcm2835)
dtparam=audio=on

# Additional overlays and parameters are documented
# /boot/firmware/overlays/README

# Automatically load overlays for detected cameras
camera_auto_detect=1

# Automatically load overlays for detected DSI displays
display_auto_detect=1

# Automatically load initramfs files, if found
auto_initramfs=1

# Enable DRM VC4 V3D driver
dtoverlay=vc4-kms-v3d
max_framebuffers=2

# Don't have the firmware create an initial video= setting in cmdline.txt.
# Use the kernel's default instead.
disable_fw_kms_setup=1

# Run in 64-bit mode
arm_64bit=1

# Disable compensation for displays with overscan
disable_overscan=1

# Run as fast as firmware / board allows
arm_boost=1

[cm4]
# Enable host mode on the 2711 built-in XHCI USB controller.
# This line should be removed if the legacy DWC2 controller is required
# (e.g. for USB device mode) or if USB support is not required.
otg_mode=1

[cm5]
dtoverlay=dwc2,dr_mode=host

[all]
"""


def build_raspios(persona, ident):
    """Raspberry Pi OS (Bookworm, arm64) as written by pi-gen: Debian plus the Pi bits."""
    r, user = ident.release, persona.user
    files, ctimes = _debian_files(persona, ident)
    files.update({
        "etc/rpi-issue": (f"Raspberry Pi reference {r.extra['image']}\n"
                          f"Generated using pi-gen, https://github.com/RPi-Distro/pi-gen, {r.extra['pi_gen']}, stage4\n"),
        "boot/firmware/config.txt": _PI_CONFIG_TXT,
        "boot/firmware/cmdline.txt": pi_cmdline(ident) + "\n",
        # the device tree property ends with a NUL, cat shows no newline
        "proc/device-tree/model": ident.hardware.board + "\0",
        "etc/apt/sources.list": f"""deb http://deb.debian.org/debian {r.extra['codename']} main contrib non-free non-free-firmware
deb http://deb.debian.org/debian-security/ {r.extra['codename']}-security main contrib non-free non-free-firmware
deb http://deb.debian.org/debian {r.extra['codename']}-updates main contrib non-free non-free-firmware
""",
        "etc/apt/sources.list.d/raspi.list": f"deb http://archive.raspberrypi.com/debian/ {r.extra['codename']} main\n",
    })
    pi_groups = "\n".join(f"{name}:x:{gid}:{user}" for name, gid in
                          (("netdev", 105), ("gpio", 997), ("i2c", 998), ("spi", 999), ("input", 101),
                           ("render", 106), ("lpadmin", 110))) + "\n"
    accounts = (
        server_passwd(user, persona.uid, persona.gid, ",,,", persona.shell,
                      extra="avahi:x:107:113:Avahi mDNS daemon,,,:/run/avahi-daemon:/usr/sbin/nologin\n"),
        server_group(user, persona.gid, extra=pi_groups + "avahi:x:113:\n"),
        server_shadow(user, ident, extra=("avahi",)),
    )
    boot = ("/dev/mmcblk0p1", 522232, "/boot/firmware")
    return _server_content(persona, ident, files, tasks=172, run_lock=True, accounts=accounts,
                           boot=boot, ctimes=ctimes)


def build_rhel(persona, ident):
    r = ident.release
    major = r.os_version_id.split(".")[0]
    dnf, dnf_ctime = dnf_log(persona, ident)
    files = {
        "etc/os-release": f"""NAME="Red Hat Enterprise Linux"
VERSION="{r.os_version}"
ID="rhel"
ID_LIKE="fedora"
VERSION_ID="{r.os_version_id}"
PLATFORM_ID="platform:el{major}"
PRETTY_NAME="Red Hat Enterprise Linux {r.os_version}"
ANSI_COLOR="0;31"
LOGO="fedora-logo-icon"
CPE_NAME="cpe:/o:redhat:enterprise_linux:{major}::baseos"
HOME_URL="https://www.redhat.com/"
DOCUMENTATION_URL="https://access.redhat.com/documentation/en-us/red_hat_enterprise_linux/{major}"
BUG_REPORT_URL="https://bugzilla.redhat.com/"

REDHAT_BUGZILLA_PRODUCT="Red Hat Enterprise Linux {major}"
REDHAT_BUGZILLA_PRODUCT_VERSION={r.os_version_id}
REDHAT_SUPPORT_PRODUCT="Red Hat Enterprise Linux"
REDHAT_SUPPORT_PRODUCT_VERSION="{r.os_version_id}"
""",
        "etc/redhat-release": f"Red Hat Enterprise Linux release {r.os_version}\n",
        "etc/system-release": f"Red Hat Enterprise Linux release {r.os_version}\n",
        "etc/hosts": el_hosts(ident.hostname),
        "etc/issue": "\\S\nKernel \\r on an \\m\n\n",
        "etc/issue.net": "\\S\nKernel \\r on an \\m\n",
        "etc/motd": "Activate the web console with: systemctl enable --now cockpit.socket\n\n",
        "var/log/dnf.log": dnf,
    }
    accounts = (el_passwd("rhel", persona.user, persona.uid, persona.gid),
                el_group("rhel", persona.user, persona.gid),
                el_shadow("rhel", persona.user, ident))
    return _server_content(persona, ident, files, tasks=104, accounts=accounts,
                           home=el_home_files("rhel", persona.user),
                           ctimes={"var/log/dnf.log": dnf_ctime})


_CENTOS_REPO = """# CentOS-Base.repo
#
# CentOS 7 reached its end of life on 2024-06-30; the packages stay on vault.centos.org.
#

[base]
name=CentOS-$releasever - Base
#mirrorlist=http://mirrorlist.centos.org/?release=$releasever&arch=$basearch&repo=os&infra=$infra
baseurl=http://vault.centos.org/{release}/os/$basearch/
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-CentOS-7

#released updates
[updates]
name=CentOS-$releasever - Updates
#mirrorlist=http://mirrorlist.centos.org/?release=$releasever&arch=$basearch&repo=updates&infra=$infra
baseurl=http://vault.centos.org/{release}/updates/$basearch/
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-CentOS-7

#additional packages that may be useful
[extras]
name=CentOS-$releasever - Extras
#mirrorlist=http://mirrorlist.centos.org/?release=$releasever&arch=$basearch&repo=extras&infra=$infra
baseurl=http://vault.centos.org/{release}/extras/$basearch/
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-CentOS-7
"""


def build_centos(persona, ident):
    r = ident.release
    full = r.extra["centos_release"]
    logs, log_ctimes = yum_logs(persona, ident)
    files = {
        "etc/os-release": """NAME="CentOS Linux"
VERSION="7 (Core)"
ID="centos"
ID_LIKE="rhel fedora"
VERSION_ID="7"
PRETTY_NAME="CentOS Linux 7 (Core)"
ANSI_COLOR="0;31"
CPE_NAME="cpe:/o:centos:centos:7"
HOME_URL="https://www.centos.org/"
BUG_REPORT_URL="https://bugs.centos.org/"

CENTOS_MANTISBT_PROJECT="CentOS-7"
CENTOS_MANTISBT_PROJECT_VERSION="7"
REDHAT_SUPPORT_PRODUCT="centos"
REDHAT_SUPPORT_PRODUCT_VERSION="7"

""",
        "etc/redhat-release": f"CentOS Linux release {full} (Core)\n",
        "etc/centos-release": f"CentOS Linux release {full} (Core)\n",
        "etc/system-release": f"CentOS Linux release {full} (Core)\n",
        "etc/hosts": el_hosts(ident.hostname),
        "etc/issue": "\\S\nKernel \\r on an \\m\n\n",
        "etc/issue.net": "\\S\nKernel \\r on an \\m\n",
        "etc/motd": "",
        "etc/yum.repos.d/CentOS-Base.repo": _CENTOS_REPO.format(release=full),
    }
    files.update(logs)
    accounts = (el_passwd("centos", persona.user, persona.uid, persona.gid),
                el_group("centos", persona.user, persona.gid),
                el_shadow("centos", persona.user, ident))
    return _server_content(persona, ident, files, tasks=98, accounts=accounts,
                           home=el_home_files("centos", persona.user),
                           ctimes=log_ctimes)
