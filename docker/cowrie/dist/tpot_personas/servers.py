"""Server personas (bash, systemd): package managers, command inventory,
processes, package logs and one builder per family."""
import calendar
import hashlib
import time

from .common import (assign_processes, base_files, base_txtcmds, deb_arch, el_group, el_home_files, el_hosts,
                     el_passwd, el_shadow, gm, lscpu, pi_partuuid, ps_entry, rng_for, root_uuid, server_df,
                     server_group, server_mount, server_passwd, server_shadow, shadow_ctime)
from .identity import drawn_at, log_window, updated_at
from . import system
from .system import proc_modules
from .model import Content

# Package names of the tools themselves: versions for --version output, not for the logs.
TOOL_PACKAGES = frozenset({"apt", "dpkg", "dnf", "rpm", "yum"})

# /usr/bin/dnf-3 of dnf 4 (unverified: the script of dnf 4.14, its licence header shortened)
DNF_SCRIPT = """#!/usr/bin/python3
# The dnf executable script.
#
# Copyright (C) 2012-2016 Red Hat, Inc.
#
# This copyrighted material is made available to anyone wishing to use,
# modify, copy, or redistribute it subject to the terms and conditions of
# the GNU General Public License v.2, or (at your option) any later version.
#

import sys


def suppress_keyboard_interrupt_message():
    \"\"\"Prevent unsightly KeyboardInterrupt tracebacks.

    Nothing will be printed to the terminal after an uncaught
    :class:`exceptions.KeyboardInterrupt`.

    \"\"\"
    old_excepthook = sys.excepthook

    def new_hook(type, value, traceback):
        if type != KeyboardInterrupt:
            old_excepthook(type, value, traceback)
        else:
            pass

    sys.excepthook = new_hook


# do this ASAP to prevent tracebacks after ^C during imports
suppress_keyboard_interrupt_message()

if __name__ != "__main__":
    sys.stderr.write('The executable DNF module must not be imported.')
    sys.exit(1)

here = sys.path[0]
if here != '/usr/bin':
    # git checkout
    import os
    dnf_toplevel = os.path.dirname(here)
    sys.path[0] = dnf_toplevel

from dnf.cli import main
main.user_main(sys.argv[1:], exit_code=True)
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
    """The package managers Cowrie does not emulate: dpkg and the rpm of CentOS 7. apt / apt-get
    of Debian, yum 3 of CentOS 7 and dnf 4 / rpm of RHEL 9 are Cowrie's (system.python_answers)."""
    p = ident.release.packages
    if persona.family in ("ubuntu", "debian", "raspios"):
        arch = deb_arch(persona)
        return {
            "usr/bin/dpkg": f"Debian 'dpkg' package management program version {p['dpkg']} ({arch}).\n",
        }
    if persona.family == "centos":
        return {"usr/bin/rpm": f"RPM version {p['rpm']}\n"}
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
    # sudo and service are Cowrie's (system.python_answers)
    return {"usr/bin/lspci": lspci}


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
            ps_entry("nobody", 0, "/usr/sbin/thd --triggers /etc/triggerhappy/triggers.d/ --socket /run/thd.socket "
                               "--user nobody --deviceglob /dev/input/event*", mem=0.0, vsz=3316, rss=1536, stat="Ss"),
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
            ps_entry("root", 0, "/usr/sbin/irqbalance --foreground", mem=0.1, vsz=82840, rss=3840, stat="Ssl"),
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


def _rpm_size(name, kb):
    """Bytes of a package of about kb kB (the same for a name on every render)."""
    return kb * 1024 - int.from_bytes(hashlib.sha256(name.encode()).digest()[:2], "big") % 1024


def dnf_packages(persona, ident):
    """What dnf 4 and rpm know on RHEL 9 ([shell] dnf_packages): dnf and rpm (with their install and build
    times for dnf --version), the installed packages (from: @System for those of the image, the repository
    for those dnf.log installed or upgraded), the packages the repositories have besides and the
    repositories (system.rhel_repos)."""
    r = ident.release
    minor = r.os_version_id
    repos = system.rhel_repos(ident)
    repo_id = {repo["id"].split("-")[4]: repo["id"] for repo in repos if repo["enabled"]}
    kernel = r.kernel_version.rsplit(".", 1)[0]
    openssh = r.packages["openssh-server"]
    versions = {"cloud-init": system.cloud_init_version(persona, ident)[0], "dnf": system.RHEL_DNF[minor],
                "kernel": kernel, "kernel-core": kernel, "openssh": openssh, "openssh-clients": openssh,
                "openssl": system.RHEL_OPENSSL[minor], "perl-interpreter": system.RHEL_PERL,
                "python3": system.RHEL_PYTHON_RPM[minor], "rpm": system.RHEL_RPM[minor], "sudo": system.RHEL_SUDO,
                "systemd": systemd_version(persona, ident)[1], "yum": system.RHEL_DNF[minor],
                "systemd-udev": systemd_version(persona, ident)[1], "redhat-release": system.RHEL_RELEASE_RPM[minor],
                **{name: version for name, version in r.packages.items() if name not in TOOL_PACKAGES}}
    logged = {name for _, names, _, _ in _log_entries(ident, "dnf-log") for name in names}
    installed = {}
    for name, (arch, repo, summary, evr) in system.RPM_INSTALLED.items():
        installed[name] = {"evr": system.rpm_evr(evr, minor), "arch": arch, "repo": repo, "summary": summary}
    versions["glibc-common"] = system.rpm_evr(system.RPM_INSTALLED["glibc"][3], minor)
    versions["vim-minimal"] = versions["vim-enhanced"]
    for name, evr in versions.items():
        arch, repo, summary = system.RPM_PERSONA[name]
        installed[name] = {"evr": evr, "arch": arch, "repo": repo, "summary": summary}
    for name, entry in installed.items():
        repo = entry.pop("repo")
        entry["from"] = f"@{repo_id[repo]}" if name in logged else "@System"
        entry["size"] = _rpm_size(name, 200 + int.from_bytes(hashlib.sha256(name.encode()).digest()[2:4], "big")
                                  % 4000)
    available = {}
    for name, (arch, repo, summary, evr, (size, unpacked), requires) in system.RPM_AVAILABLE.items():
        evr = system.rpm_evr(evr, minor)
        if evr.startswith("="):
            evr = installed[evr[1:]]["evr"]
        available[name] = {"evr": evr, "arch": arch, "repo": repo_id[repo], "summary": summary,
                           "download": _rpm_size(name, size), "size": _rpm_size(name + "/", unpacked),
                           "requires": list(requires),
                           "programs": ["/" + rel for rel in system.RPM_AVAILABLE_PROGRAMS.get(name, ())]}
    owners = {}
    for package, (programs, sbin) in system.RPM_OWNED.items():
        owners.update({f"/usr/bin/{name}": package for name in programs.split()})
        owners.update({f"/usr/sbin/{name}": package for name in sbin.split()})
    for package, files in system.RPM_OWNED_FILES.items():
        owners.update({"/" + rel: package for rel in files})
    for tool in system.installed_tools(persona, ident):
        for rel in system.TOOL_PROGRAMS[tool].values():
            owners["/" + rel] = system.RPM_TOOLS[tool]
    rng = rng_for(ident, "dnf-version")
    built = updated_at(r) or ident.installed_at

    def tool(name):
        return {"nevra": f"{name}-0:{installed[name]['evr']}.{installed[name]['arch']}",
                "installed": max(ident.installed_at, min(built + rng.randint(28_800, 72_000), ident.booted_at)),
                "built": built - rng.randint(10, 40) * 86400 - rng.randint(0, 86399)}

    return {"version": r.packages["dnf"], "rpm_version": r.packages["rpm"], "dnf": tool("dnf"), "rpm": tool("rpm"),
            "packager": system.RHEL_PACKAGER, "repos": repos, "installed": dict(sorted(installed.items())),
            "available": dict(sorted(available.items())), "provides": dict(system.RPM_PROVIDES),
            "owners": dict(sorted(owners.items())),
            "answers": system.session_answers(path for entry in available.values() for path in entry["programs"])}


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
    this_year = time.gmtime(drawn_at(ident)).tm_year
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
            ctimes[f"var/log/yum.log-{year + 1}0101"] = min(rotated, drawn_at(ident))
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
    all_files["proc/modules"] = proc_modules(persona, ident)
    all_files.update(system.files(persona))
    all_files.update(system.init_scripts(persona))
    sudoers = system.sudoers_files(persona, ident)
    all_files.update(sudoers)
    all_files.update(files)
    txtcmds = base_txtcmds(persona, ident, cmdline=cmdline,
                           df=server_df(ident, root_dev=root_dev, run_lock=run_lock, boot=boot),
                           mount=server_mount(ident, root_dev=root_dev, run_lock=run_lock, boot=boot,
                                              root_type=root_type, root_options=root_options),
                           lscpu_text=lscpu(persona, hw, op_modes="32-bit, 64-bit"))
    txtcmds.update(package_manager_txtcmds(persona, ident))
    txtcmds.update(command_inventory_txtcmds(persona, ident))
    all_ctimes = {"etc/shadow": shadow_ctime(ident)}
    all_ctimes.update({rel: ident.installed_at for rel in sudoers})
    all_ctimes.update(ctimes or {})
    return Content(files=all_files, txtcmds=txtcmds, processes=processes, ctimes=all_ctimes, tasks=tasks)


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
    """Ubuntu with NVIDIA GPUs, Ollama and Jupyter as one system."""
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
        "usr/bin/dnf-3": DNF_SCRIPT,
        "etc/yum.repos.d/redhat.repo": system.redhat_repo(ident, rng_for(ident, "entitlement").randrange(
            10 ** 18, 10 ** 19)),
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


# --- systemd ------------------------------------------------------------------------
# systemd of the releases: (version, package version for --version) and its compile features
#   (unverified: the package version of each patch level, the feature lists)
SYSTEMD = {"noble": (255, "255.4-1ubuntu8.10"), "jammy": (249, "249.11-0ubuntu3.16"),
           "trixie": (257, "257.9-1~deb13u1"), "bookworm": (252, "252.39-1~deb12u1"), "centos": (219, "")}
RHEL_SYSTEMD = {"9.4": "252-32.el9_4.7", "9.5": "252-46.el9_5.3", "9.6": "252-51.el9_6.1"}
_FEATURES_DEBIAN = ("+PAM +AUDIT +SELINUX +APPARMOR +IMA +SMACK +SECCOMP +GCRYPT -GNUTLS +OPENSSL +ACL +BLKID +CURL "
                    "+ELFUTILS +FIDO2 +IDN2 -IDN +IPTC +KMOD +LIBCRYPTSETUP +LIBFDISK +PCRE2 -PWQUALITY +P11KIT "
                    "+QRENCODE +TPM2 +BZIP2 +LZ4 +XZ +ZLIB +ZSTD -BPF_FRAMEWORK -XKBCOMMON +UTMP +SYSVINIT "
                    "default-hierarchy=unified")
SYSTEMD_FEATURES = {
    "jammy": ("+PAM +AUDIT +SELINUX +APPARMOR +IMA +SMACK +SECCOMP +GCRYPT +GNUTLS +OPENSSL +ACL +BLKID +CURL "
              "+ELFUTILS +FIDO2 +IDN2 -IDN +IPTC +KMOD +LIBCRYPTSETUP -LIBFDISK +PCRE2 -PWQUALITY -P11KIT -QRENCODE "
              "+BZIP2 +LZ4 +XZ +ZLIB +ZSTD -XKBCOMMON +UTMP +SYSVINIT default-hierarchy=unified"),
    "rhel": ("+PAM +AUDIT +SELINUX -APPARMOR +IMA +SMACK +SECCOMP +GCRYPT +GNUTLS +OPENSSL +ACL +BLKID +CURL "
             "+ELFUTILS -FIDO2 +IDN2 -IDN -IPTC +KMOD +LIBCRYPTSETUP +LIBFDISK +PCRE2 -PWQUALITY +P11KIT -QRENCODE "
             "+TPM2 +BZIP2 +LZ4 +XZ +ZLIB +ZSTD -BPF_FRAMEWORK +XKBCOMMON +UTMP +SYSVINIT default-hierarchy=unified"),
    "centos": ("+PAM +AUDIT +SELINUX +IMA -APPARMOR +SMACK +SYSVINIT +UTMP +LIBCRYPTSETUP +GCRYPT +GNUTLS +ACL +XZ "
               "+LZ4 -SECCOMP +BLKID +ELFUTILS +KMOD +IDN"),
}
SSH_LOG = ("Server listening on 0.0.0.0 port 22.", "Server listening on :: port 22.")

# the services of a booted cloud image, as list-units --type=service shows them: (unit, description,
#   the text of its process in ps (None: a oneshot that has exited), unit file state); unverified: the
#   exact lists and the descriptions of each systemd version
_CORE = (
    ("kmod-static-nodes.service", "Create List of Static Device Nodes", None, "static"),
    ("systemd-journal-flush.service", "Flush Journal to Persistent Storage", None, "static"),
    ("systemd-journald.service", "Journal Service", "systemd-journald", "static"),
    ("systemd-logind.service", "User Login Management", "systemd-logind", "static"),
    ("systemd-random-seed.service", "Load/Save Random Seed", None, "static"),
    ("systemd-remount-fs.service", "Remount Root and Kernel File Systems", None, "static"),
    ("systemd-sysctl.service", "Apply Kernel Variables", None, "static"),
    ("systemd-tmpfiles-setup-dev.service", "Create Static Device Nodes in /dev", None, "static"),
    ("systemd-tmpfiles-setup.service", "Create Volatile Files and Directories", None, "static"),
    ("systemd-udev-trigger.service", "Coldplug All udev Devices", None, "static"),
    ("systemd-udevd.service", "Rule-based Manager for Device Events and Files", "systemd-udevd", "static"),
    ("systemd-update-utmp.service", "Record System Boot/Shutdown in UTMP", None, "static"),
    ("systemd-user-sessions.service", "Permit User Sessions", None, "static"),
)
# systemd 219 of CentOS 7 names them as it did then
_CORE_219 = {"kmod-static-nodes.service": "Create list of required static device nodes for the current kernel",
             "systemd-logind.service": "Login Service", "systemd-udev-trigger.service": "udev Coldplug all Devices",
             "systemd-udevd.service": "udev Kernel Device Manager",
             "systemd-update-utmp.service": "Update UTMP about System Boot/Shutdown"}
_CLOUD = (("cloud-config.service", "Cloud-init: Config Stage", None, "enabled"),
          ("cloud-final.service", "Cloud-init: Final Stage", None, "enabled"),
          ("cloud-init-local.service", "Cloud-init: Local Stage (pre-network)", None, "enabled"),
          ("cloud-init.service", "Cloud-init: Network Stage", None, "enabled"))
_CLOUD_EL7 = (("cloud-config.service", "Apply the settings specified in cloud-config", None, "enabled"),
              ("cloud-final.service", "Execute cloud user/final scripts", None, "enabled"),
              ("cloud-init-local.service", "Initial cloud-init job (pre-networking)", None, "enabled"),
              ("cloud-init.service", "Initial cloud-init job (metadata service crawler)", None, "enabled"))
_CRON = ("cron.service", "Regular background program processing daemon", "/usr/sbin/cron", "enabled")
_DBUS = ("dbus.service", "D-Bus System Message Bus", "dbus-daemon", "static")
_SSH = ("ssh.service", "OpenBSD Secure Shell server", "sshd: ", "enabled")
_NETWORKD = ("systemd-networkd.service", "Network Configuration", "systemd-networkd", "enabled")
_E2SCRUB = ("e2scrub_reap.service", "Remove Stale Online ext4 Metadata Check Snapshots", None, "enabled")
_NM = (("NetworkManager-wait-online.service", "Network Manager Wait Online", None, "enabled"),
       ("NetworkManager.service", "Network Manager", "/usr/sbin/NetworkManager", "enabled"))
_EL = (("auditd.service", "Security Auditing Service", "/sbin/auditd", "enabled"),
       ("chronyd.service", "NTP client/server", "/usr/sbin/chronyd", "enabled"),
       ("crond.service", "Command Scheduler", "/usr/sbin/crond", "enabled"),
       ("kdump.service", "Crash recovery kernel arming", None, "enabled"),
       ("rsyslog.service", "System Logging Service", "/usr/sbin/rsyslogd", "enabled")) + _NM
SERVICE_UNITS = {
    "ubuntu": _CLOUD + (("apparmor.service", "Load AppArmor profiles", None, "enabled"),
                        ("apport.service", "LSB: automatic crash report generation", None, "enabled"), _CRON, _DBUS,
                        ("irqbalance.service", "irqbalance daemon", "/usr/sbin/irqbalance", "enabled"),
                        ("rsyslog.service", "System Logging Service", "/usr/sbin/rsyslogd", "enabled"),
                        ("snapd.service", "Snap Daemon", "/usr/lib/snapd/snapd", "enabled"), _SSH, _NETWORKD,
                        ("systemd-resolved.service", "Network Name Resolution", "systemd-resolved", "enabled"),
                        ("ufw.service", "Uncomplicated firewall", None, "enabled"),
                        ("unattended-upgrades.service", "Unattended Upgrades Shutdown", "unattended-upgrade-shutdown",
                         "enabled")),
    "debian": _CLOUD + (_CRON, _DBUS, _E2SCRUB, _SSH, _NETWORKD),
    "raspios": (("avahi-daemon.service", "Avahi mDNS/DNS-SD Stack", "avahi-daemon: running", "enabled"),
                ("bluetooth.service", "Bluetooth service", "bluetoothd", "enabled"), _CRON, _DBUS,
                ("dphys-swapfile.service", "dphys-swapfile - set up, mount/unmount, and delete a swap file", None,
                 "enabled"), _E2SCRUB,
                ("fake-hwclock.service", "Restore / save the current clock", None, "enabled"),
                ("ModemManager.service", "Modem Manager", "/usr/sbin/ModemManager", "enabled")) + _NM + (
                ("rpi-eeprom-update.service", "Check for Raspberry Pi EEPROM updates", None, "enabled"), _SSH,
                ("triggerhappy.service", "triggerhappy global hotkey daemon", "/usr/sbin/thd", "enabled"),
                ("wpa_supplicant.service", "WPA supplicant", "/sbin/wpa_supplicant", "enabled")),
    "rhel": _CLOUD + _EL + (
        ("dbus-broker.service", "D-Bus System Message Bus", "dbus-broker-launch", "enabled"),
        ("dracut-shutdown.service", "Restore /run/initramfs on shutdown", None, "static"),
        ("import-state.service", "Import network configuration from initramfs", None, "enabled"),
        ("selinux-autorelabel-mark.service", "Mark the need to relabel after reboot", None, "enabled"),
        ("sshd.service", "OpenSSH server daemon", "sshd: ", "enabled")),
    "centos": _CLOUD_EL7 + _EL + (
        _DBUS, ("network.service", "LSB: Bring up/down networking", None, "bad"),
        ("polkit.service", "Authorization Manager", "polkitd", "static"),
        ("postfix.service", "Postfix Mail Transport Agent", "postfix/master", "enabled"),
        ("rhel-dmesg.service", "Dump dmesg to /var/log/dmesg", None, "enabled"),
        ("rhel-domainname.service", "Read and set NIS domainname from /etc/sysconfig/network", None, "enabled"),
        ("rhel-import-state.service", "Import network configuration from initramfs", None, "enabled"),
        ("rhel-readonly.service", "Configure read-only root support", None, "enabled"),
        ("sshd.service", "OpenSSH server daemon", "/usr/sbin/sshd -D", "enabled"),
        ("systemd-vconsole-setup.service", "Setup Virtual Console", None, "static")),
}
# the sockets of systemd and D-Bus, and ssh.socket on 24.04: (unit, description, the service it starts,
#   what it listens on)
SOCKETS = (("dbus.socket", "D-Bus System Message Bus Socket", "dbus.service", ("/run/dbus/system_bus_socket (Stream)",)),
           ("systemd-journald-dev-log.socket", "Journal Socket (/dev/log)", "systemd-journald.service",
            ("/run/systemd/journal/dev-log (Datagram)",)),
           ("systemd-journald.socket", "Journal Socket", "systemd-journald.service",
            ("/run/systemd/journal/socket (Datagram)", "/run/systemd/journal/stdout (Stream)")),
           ("systemd-udevd-control.socket", "udev Control Socket", "systemd-udevd.service",
            ("/run/udev/control (SequentialPacket)",)),
           ("systemd-udevd-kernel.socket", "udev Kernel Socket", "systemd-udevd.service", ("kobject-uevent 1 (Netlink)",)))
SSH_SOCKET = ("ssh.socket", "OpenBSD Secure Shell server socket", "ssh.service", ("[::]:22 (Stream)",))
# the ExecStart= of each unit file of the image ({sd}: systemd's directory); unverified: the command
#   lines of each release
UNIT_EXEC = {
    "kmod-static-nodes.service": "/bin/kmod static-nodes --format=tmpfiles --output=/run/tmpfiles.d/static-nodes.conf",
    "systemd-journal-flush.service": "journalctl --smart-relinquish-var",
    "systemd-random-seed.service": "{sd}/systemd-random-seed load",
    "systemd-remount-fs.service": "{sd}/systemd-remount-fs",
    "systemd-sysctl.service": "{sd}/systemd-sysctl",
    "systemd-tmpfiles-setup-dev.service": "systemd-tmpfiles --prefix=/dev --create --boot",
    "systemd-tmpfiles-setup.service": "systemd-tmpfiles --create --remove --boot --exclude-prefix=/dev",
    "systemd-udev-trigger.service": "-udevadm trigger --type=all --action=add",
    "systemd-update-utmp.service": "{sd}/systemd-update-utmp reboot",
    "systemd-user-sessions.service": "{sd}/systemd-user-sessions start",
    "systemd-vconsole-setup.service": "{sd}/systemd-vconsole-setup",
    "systemd-journald.service": "{sd}/systemd-journald",
    "systemd-logind.service": "{sd}/systemd-logind",
    "systemd-udevd.service": "{sd}/systemd-udevd",
    "systemd-networkd.service": "{sd}/systemd-networkd",
    "systemd-resolved.service": "{sd}/systemd-resolved",
    "cloud-init-local.service": "/usr/bin/cloud-init init --local",
    "cloud-init.service": "/usr/bin/cloud-init init",
    "cloud-config.service": "/usr/bin/cloud-init modules --mode=config",
    "cloud-final.service": "/usr/bin/cloud-init modules --mode=final",
    "apparmor.service": "/lib/apparmor/apparmor.systemd reload",
    "apport.service": "/usr/share/apport/apport --start",
    "ufw.service": "/lib/ufw/ufw-init start quiet",
    "e2scrub_reap.service": "/sbin/e2scrub_all -A -r",
    "dphys-swapfile.service": "/sbin/dphys-swapfile setup",
    "fake-hwclock.service": "/sbin/fake-hwclock load",
    "NetworkManager-wait-online.service": "/usr/bin/nm-online -s -q",
    "rpi-eeprom-update.service": "/usr/bin/rpi-eeprom-update -s -a",
    "kdump.service": "/usr/bin/kdumpctl start",
    "dracut-shutdown.service": "/bin/true",
    "import-state.service": "/usr/libexec/import-state",
    "selinux-autorelabel-mark.service": "/usr/libexec/selinux/selinux-autorelabel-mark.sh",
    "rhel-dmesg.service": "/usr/lib/systemd/rhel-dmesg",
    "rhel-domainname.service": "/usr/lib/systemd/rhel-domainname",
    "rhel-import-state.service": "/usr/lib/systemd/rhel-import-state",
    "rhel-readonly.service": "/usr/lib/systemd/rhel-readonly",
    "cron.service": "/usr/sbin/cron -f $EXTRA_OPTS",
    "dbus.service": "/usr/bin/dbus-daemon --system --address=systemd: --nofork --nopidfile --systemd-activation "
                    "--syslog-only",
    "rsyslog.service": "/usr/sbin/rsyslogd -n -iNONE",
    "snapd.service": "/usr/lib/snapd/snapd",
    "ssh.service": "/usr/sbin/sshd -D $SSHD_OPTS",
    "unattended-upgrades.service": "/usr/share/unattended-upgrades/unattended-upgrade-shutdown --wait-for-signal",
    "irqbalance.service": "/usr/sbin/irqbalance --foreground $IRQBALANCE_ARGS",
    "avahi-daemon.service": "/usr/sbin/avahi-daemon -s",
    "bluetooth.service": "/usr/libexec/bluetooth/bluetoothd",
    "ModemManager.service": "/usr/sbin/ModemManager",
    "NetworkManager.service": "/usr/sbin/NetworkManager --no-daemon",
    "wpa_supplicant.service": '/sbin/wpa_supplicant -u -s -O "DIR=/run/wpa_supplicant GROUP=netdev"',
    "triggerhappy.service": "/usr/sbin/thd --triggers /etc/triggerhappy/triggers.d/ --socket /run/thd.socket "
                            "--user nobody --deviceglob /dev/input/event*",
    "auditd.service": "/sbin/auditd",
    "chronyd.service": "/usr/sbin/chronyd $OPTIONS",
    "crond.service": "/usr/sbin/crond -n $CRONDARGS",
    "dbus-broker.service": "/usr/bin/dbus-broker-launch --scope system --audit",
    "polkit.service": "/usr/lib/polkit-1/polkitd --no-debug",
    "postfix.service": "/usr/sbin/postfix start",
    "nvidia-persistenced.service": "/usr/bin/nvidia-persistenced --user nvidia-persistenced --no-persistence-mode "
                                   "--verbose",
    "ollama.service": "/usr/local/bin/ollama serve",
}
# the Type= of a running unit other than simple (unverified: the units of each release)
UNIT_TYPE = {"systemd-journald.service": "notify", "systemd-logind.service": "notify", "systemd-udevd.service": "notify",
             "systemd-networkd.service": "notify", "systemd-resolved.service": "notify", "rsyslog.service": "notify",
             "snapd.service": "notify", "dbus-broker.service": "notify", "NetworkManager.service": "dbus",
             "ModemManager.service": "dbus", "avahi-daemon.service": "dbus", "bluetooth.service": "dbus",
             "wpa_supplicant.service": "dbus", "polkit.service": "dbus", "auditd.service": "forking",
             "chronyd.service": "forking", "postfix.service": "forking", "nvidia-persistenced.service": "forking",
             "ssh.service": "notify"}
# the programs of the units that are scripts on a real system: shortened text (unverified)
_SH_SCRIPT = "#!/bin/sh\n# {what}\n\nset -e\n\ncase \"$1\" in\n    *)\n        exit 0\n        ;;\nesac\n"
_PY_SCRIPT = "#!/usr/bin/python3\n# {what}\n\nimport sys\n\nfrom {module} import main\n\nif __name__ == \"__main__\":\n    sys.exit(main())\n"
SCRIPT_PROGRAMS = {
    "/usr/share/apport/apport": _PY_SCRIPT.format(what="Collect information about a crash and create a report.",
                                                  module="apport.cli"),
    "/usr/share/unattended-upgrades/unattended-upgrade-shutdown": _PY_SCRIPT.format(
        what="Wait for unattended-upgrades to finish before shutdown.", module="unattended_upgrades.shutdown"),
    "/usr/bin/cloud-init": "#!/usr/bin/python3\n# -*- coding: utf-8 -*-\nimport re\nimport sys\n"
                           "from cloudinit.cmd.main import main\nif __name__ == '__main__':\n"
                           "    sys.argv[0] = re.sub(r'(-script\\.pyw|\\.exe)?$', '', sys.argv[0])\n"
                           "    sys.exit(main())\n",
    "/lib/apparmor/apparmor.systemd": _SH_SCRIPT.format(what="Load the AppArmor profiles of the system."),
    "/lib/ufw/ufw-init": _SH_SCRIPT.format(what="Start, stop and reload the firewall rules of ufw."),
    "/sbin/dphys-swapfile": _SH_SCRIPT.format(what="Set up, mount/unmount, and delete a swap file."),
    "/sbin/fake-hwclock": _SH_SCRIPT.format(what="Save or restore the system clock from a file."),
    "/usr/bin/rpi-eeprom-update": _SH_SCRIPT.format(what="Check and update the bootloader EEPROM of the Raspberry Pi."),
    "/usr/bin/kdumpctl": _SH_SCRIPT.format(what="Arm the crash kernel of kdump."),
    "/usr/libexec/import-state": _SH_SCRIPT.format(what="Import the network configuration of the initramfs."),
    "/sbin/e2scrub_all": _SH_SCRIPT.replace("/bin/sh", "/bin/bash", 1).format(
        what="Check all mounted ext4 file systems on LVM, remove stale snapshots (-r)."),
    "/usr/libexec/selinux/selinux-autorelabel-mark.sh": _SH_SCRIPT.format(
        what="Mark the need to relabel after reboot."),
    "/usr/lib/systemd/rhel-dmesg": _SH_SCRIPT.format(what="Dump dmesg to /var/log/dmesg."),
    "/usr/lib/systemd/rhel-domainname": _SH_SCRIPT.format(what="Set the NIS domain name of /etc/sysconfig/network."),
    "/usr/lib/systemd/rhel-import-state": _SH_SCRIPT.format(what="Import the network configuration of the initramfs."),
    "/usr/lib/systemd/rhel-readonly": _SH_SCRIPT.format(what="Configure read-only root support."),
}
# the ExecStart= of a family where it differs
UNIT_EXEC_FAMILY = {
    "ubuntu": {"cron.service": "/usr/sbin/cron -f -P $EXTRA_OPTS"},
    "centos": {"dbus.service": "/usr/bin/dbus-daemon --system --address=systemd: --nofork --nopidfile "
                               "--systemd-activation", "rsyslog.service": "/usr/sbin/rsyslogd -n $SYSLOGD_OPTIONS"},
    "rhel": {"rsyslog.service": "/usr/sbin/rsyslogd -n $SYSLOGD_OPTIONS"},
}


def wants_target(name):
    """The target an enabled unit is wanted by (the directory of its link in /etc/systemd/system)."""
    if name.endswith(".socket"):
        return "sockets.target"
    if name.startswith("cloud-"):
        return "cloud-init.target"
    if name.endswith("-wait-online.service"):
        return "network-online.target"
    return "multi-user.target"


def unit_file(persona, ident, unit):
    """The text of a unit file the image has (shortened to what systemctl shows of it; unverified)."""
    name, sd = unit["name"], unit_dir(persona, ident).rsplit("/system", 1)[0]
    if name.endswith(".socket"):
        listen = "".join(f"Listen{'Stream' if 'Stream' in entry else 'Datagram' if 'Datagram' in entry else 'Netlink'}="
                         f"{entry.rsplit(' (', 1)[0]}\n" for entry in unit["listen"])
        body = f"[Socket]\n{listen}"
    else:
        command = UNIT_EXEC_FAMILY.get(persona.family, {}).get(name, UNIT_EXEC[name]).format(sd=sd)
        kind = (f"Type={UNIT_TYPE[name]}\n" if name in UNIT_TYPE else "") if unit["sub"] == "running" \
            else "Type=oneshot\nRemainAfterExit=yes\n"
        body = f"[Service]\n{kind}ExecStart={command}\n"
    install = f"\n[Install]\nWantedBy={wants_target(name)}\n" if unit["state"] == "enabled" else ""
    return f"[Unit]\nDescription={unit['description']}\n\n{body}{install}"


GPU_UNITS = (("nvidia-persistenced.service", "NVIDIA Persistence Daemon", "nvidia-persistenced", "enabled"),
             ("ollama.service", "Ollama Service", "ollama serve", "enabled"))


def systemd_version(persona, ident):
    """(version, package version) of systemd."""
    if persona.family == "rhel":
        return 252, RHEL_SYSTEMD.get(ident.release.os_version_id, "252-51.el9_6.1")
    code = "centos" if persona.family == "centos" else ident.release.extra["codename"]
    return SYSTEMD[code]


def unit_dir(persona, ident):
    """Where the unit files are: /usr/lib/systemd/system after the /usr merge (EL, noble, trixie)."""
    return "/usr" + _systemd_paths(ident) + "/system" if persona.family in ("rhel", "centos") \
        else _systemd_paths(ident) + "/system"


def process_name(command):
    """The process name systemd shows (comm): the script of an interpreter, else the program."""
    words = command.split()
    if len(words) > 1 and words[0].rsplit("/", 1)[-1].startswith("python"):
        return words[1].rsplit("/", 1)[-1][:15]
    return words[0].rstrip(":").rsplit("/", 1)[-1][:15]


def systemd_units(persona, ident, processes):
    """What the patched systemctl shows of a server ([shell] systemctl_units): the version of systemd
    and the services of the image, each daemon with its process of ps."""
    version, full = systemd_version(persona, ident)
    family = persona.family
    entries = SERVICE_UNITS[family] + (GPU_UNITS if ident.hardware.gpus else ())
    core = tuple((name, _CORE_219.get(name, text) if version < 220 else
                  ("Load/Save OS Random Seed" if name == "systemd-random-seed.service" and version >= 254 else text),
                  match, state) for name, text, match, state in _CORE)
    if version < 250:
        entries = tuple(e if e[0] != "systemd-networkd.service" else (e[0], "Network Service") + e[2:]
                        for e in entries)
    if version >= 255:
        # apport of 24.04 is a native unit
        entries = tuple(e if e[0] != "apport.service" else (e[0], "automatic crash report generation") + e[2:]
                        for e in entries)
    rng = rng_for(ident, "systemd")
    pids = {p["PID"] for p in processes}
    directory = unit_dir(persona, ident)
    noble = ident.release.extra.get("codename") == "noble"
    units = []
    for name, description, match, state in sorted(core + entries, key=lambda e: e[0].lower()):
        unit = {"name": name, "description": description, "state": state, "preset": "enabled",
                "path": f"{directory}/{name}"}
        if name == "apport.service" and version < 255:
            # before 24.04 apport is an LSB script, the SysV generator makes its unit
            unit.update({"path": "/etc/init.d/apport", "state": "generated"})
        if name == "network.service":
            # CentOS 7 runs the network init script through systemd's SysV generator
            unit.update({"path": "/etc/rc.d/init.d/network", "preset": "disabled"})
        process = next((p for p in processes if match and match in p["COMMAND"]), None)
        if match and process is None:
            raise ValueError(f"{persona.id}: no process of {name} in ps")
        if process is None:
            pid = rng.randint(150, 700)
            while pid in pids:
                pid += 1
            unit.update({"sub": "exited", "start": round(rng.uniform(0.8, 4.0), 3), "pid": pid,
                         "cpu_ms": rng.randint(4, 420)})
        else:
            memory = int(process["RSS"] * 1024 * rng.uniform(1.1, 1.5))
            unit.update({"sub": "running", "start": round(rng.uniform(2.0, 9.0), 3), "pid": process["PID"],
                         "comm": process_name(process["COMMAND"]), "command": process["COMMAND"],
                         "tasks": rng.randint(2, 12) if "l" in process["STAT"] else 1, "memory": memory,
                         "peak": int(memory * rng.uniform(1.05, 1.4)),
                         "cpu_ms": rng.randint(30, 900) + int(ident.boot_offset / 3600 * rng.uniform(2, 60))})
        if name in ("ssh.service", "sshd.service"):
            unit["log"] = list(SSH_LOG)
            if noble:
                # 24.04 starts sshd from ssh.socket; ssh.service itself is disabled
                unit.update({"state": "disabled", "triggered_by": "ssh.socket"})
        units.append(unit)
    services = {u["name"] for u in units}
    dbus = "dbus-broker.service" if "dbus-broker.service" in services else "dbus.service"
    for name, description, triggers, listen in SOCKETS + ((SSH_SOCKET,) if noble else ()):
        triggers = dbus if name == "dbus.socket" else triggers
        static = name != "ssh.socket"
        units.append({"name": name, "description": description, "state": "static" if static else "enabled",
                      "preset": "enabled", "path": f"{directory}/{name}", "sub": "listening",
                      "start": round(rng.uniform(0.3, 1.5), 3), "triggers": triggers, "listen": list(listen),
                      "memory": rng.choice((4096, 8192, 12288)), "peak": rng.choice((262144, 393216, 524288)),
                      "cpu_ms": rng.randint(1, 9)})
    units.sort(key=lambda u: (u["name"].rsplit(".", 1)[-1], u["name"].lower()))
    data = {"version": version, "full": full, "features": SYSTEMD_FEATURES.get(
                "centos" if family == "centos" else "rhel" if family == "rhel" else ident.release.extra["codename"],
                _FEATURES_DEBIAN),
            "unit_dir": directory, "tasks_max": int(ident.hardware.mem_kb / 128 * 0.15),
            "init": processes[0]["COMMAND"], "units": units}
    if family in ("ubuntu", "debian", "raspios") and not noble:
        data["aliases"] = {"sshd.service": "ssh.service"}
    if family in ("rhel", "centos"):
        data["redirect"] = True
    return data
