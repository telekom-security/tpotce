"""Device personas (sh/BusyBox, embedded firmware): one builder per device.
Every device has no package updates: it runs the firmware it got (Release
extra["firmware"]), so kernel, BusyBox and the SSH server are those of that
image. Mount, /proc/mounts and df come from one table of file systems, dmesg
names the root that table mounts, and the delay loop of dmesg is the BogoMIPS
of cpuinfo."""
import re

from .common import (ARM_CORES, MIPS_CORES, ULIMIT, X86_CPUS, assign_processes, base_files, bogomips,
                     embedded_group, embedded_passwd, embedded_shadow, fs_row, kernel_line, kernel_series, lscpu,
                     mount_text, nproc, proc_mounts_text, ps_entry, rng_for, shadow_ctime, shadow_days, with_inodes)
from .model import Content
from .system import busybox_applets, proc_modules


# --- shared parts of the device builders -------------------------------------------

def _lpj(value, hz):
    return round(float(value) * 500000 / hz)


def calibrate_line(persona, ident):
    """The delay loop line: x86 takes the TSC (HZ 1000), the embedded kernels
    calibrate (HZ 100 on the 2.6 vendor kernels, 250 later; unverified per device)."""
    value = bogomips(persona, ident.hardware)
    if persona.hardware_platform == "x86_64":
        return ("Calibrating delay loop (skipped), value calculated using timer frequency.. "
                f"{value} BogoMIPS (lpj={_lpj(value, 1000)})")
    hz = 100 if kernel_series(ident.release.kernel_version) < (3, 0) else 250
    return f"Calibrating delay loop... {value} BogoMIPS (lpj={_lpj(value, hz)})"


def cpu_line(persona, ident):
    """The line in which the kernel names the boot CPU (None if unknown)."""
    hw, kernel = ident.hardware, ident.release.kernel_version
    if persona.hardware_platform == "mips" and hw.cpu_model in MIPS_CORES:
        cpu = "CPU0 revision" if kernel_series(kernel) >= (3, 0) else "CPU revision"
        return f"{cpu} is: {MIPS_CORES[hw.cpu_model][0]} ({hw.cpu_model.rsplit(' ', 1)[0]})"
    if persona.hardware_platform == "armv7l" and hw.cpu_model in ARM_CORES:
        variant, part, revision, _ = ARM_CORES[hw.cpu_model]
        return (f"CPU: ARMv7 Processor [41{variant[-1]}f{part[2:]}{revision}] revision {revision} (ARMv7), "
                "cr=10c5387d")
    if persona.hardware_platform == "x86_64" and hw.cpu_model in X86_CPUS:
        family, model, stepping = X86_CPUS[hw.cpu_model][:3]
        return (f"smpboot: CPU0: {hw.cpu_model} (family: {family:#x}, model: {model:#x}, "
                f"stepping: {stepping:#x})")
    return None


def device_dmesg(persona, ident, cmdline, boot):
    """dmesg of a device: kernel banner, boot CPU, command line, delay loop, then
    the boot lines of the builder (the root mount among them)."""
    hw = ident.hardware
    lines = [(0.0, kernel_line(ident.release))]
    cpu = cpu_line(persona, ident)
    if cpu:
        lines.append((0.0, cpu))
    lines += [(0.0, f"Kernel command line: {cmdline}"), (0.01, calibrate_line(persona, ident))]
    if persona.hardware_platform == "x86_64" and hw.cpus > 1:
        total = float(bogomips(persona, hw)) * hw.cpus
        lines.append((0.12, f"smpboot: Total of {hw.cpus} processors activated ({total:.2f} BogoMIPS)"))
    lines += [(0.5 + i * 0.37, text) for i, text in enumerate(boot)]
    return "".join(f"[{t:12.6f}] {text}\n" for t, text in lines)


def fs_entry(dev, mount, fstype, options, size=0, used=0):
    """One mounted file system; size 0 keeps it out of df (proc, sysfs, ...)."""
    return dev, mount, fstype, options, size, used


def fs_tables(ident, entries):
    """(mount, /proc/mounts, the table of the patched df) of the same file systems."""
    rows = [with_inodes(ident, fs_row(*entry)) for entry in entries]
    return mount_text(rows), proc_mounts_text(rows), rows


def used_kb(ident, purpose, size, low, high):
    return int(size * rng_for(ident, purpose).uniform(low, high))


# BusyBox 1.14-1.20 print the copyright of their year, 1.21 and later one text
# (unverified: the exact wording per release)
_BB_YEARS = {(1, 14): "2008", (1, 19): "2011"}


def busybox_help(banner, applets):
    """What `busybox` without arguments prints, for "BusyBox v1.19.2 (2025-03-28 14:13:40 CST)" and the
    applets of system.busybox_applets."""
    major, minor = (int(x) for x in re.match(r"BusyBox v(\d+)\.(\d+)", banner).groups())
    if (major, minor) < (1, 21):
        year = _BB_YEARS.get((major, minor), "2012")
        notice = (f"Copyright (C) 1998-{year} Erik Andersen, Rob Landley, Denys Vlasenko\n"
                  "and others. Licensed under GPLv2.\nSee source distribution for full notice.\n")
    else:
        notice = ("BusyBox is copyrighted by many authors between 1998-2015.\n"
                  "Licensed under GPLv2. See source distribution for detailed\ncopyright notices.\n")
    applets = "\n".join("\t" + line.strip() for line in re.findall(r".{1,64}(?:, |$)", ", ".join(applets)))
    return (f"{banner} multi-call binary.\n{notice}\n"
            "Usage: busybox [function] [arguments]...\n"
            "   or: busybox --list\n"
            "   or: function [arguments]...\n\n"
            "\tBusyBox is a multi-call binary that combines many common Unix\n"
            "\tutilities into a single executable.\n\n"
            f"Currently defined functions:\n{applets}\n")


# lspci of the bare metal NAS boards, per CPU (unverified: the exact device lists)
NAS_LSPCI = {
    "Intel(R) Celeron(R) CPU  J1900  @ 1.99GHz": (
        "00:00.0 Host bridge: Intel Corporation Atom Processor Z36xxx/Z37xxx Series SoC Transaction Register (rev 0e)\n"
        "00:02.0 VGA compatible controller: Intel Corporation Atom Processor Z36xxx/Z37xxx Series Graphics & Display (rev 0e)\n"
        "00:13.0 SATA controller: Intel Corporation Atom Processor E3800 Series SATA AHCI Controller (rev 0e)\n"
        "00:14.0 USB controller: Intel Corporation Atom Processor Z36xxx/Z37xxx, Celeron N2000 Series USB xHCI (rev 0e)\n"),
    "Intel(R) Celeron(R) N5105 @ 2.00GHz": (
        "00:00.0 Host bridge: Intel Corporation Device 4e24\n"
        "00:02.0 VGA compatible controller: Intel Corporation JasperLake [UHD Graphics] (rev 01)\n"
        "00:14.0 USB controller: Intel Corporation Jasper Lake USB 3.10 XHCI Host Controller (rev 01)\n"
        "00:17.0 SATA controller: Intel Corporation Jasper Lake SATA AHCI Controller (rev 01)\n"),
    "Intel(R) Atom(TM) CPU C3538 @ 2.10GHz": (
        "00:00.0 Host bridge: Intel Corporation Atom Processor C3000 Series System Agent (rev 11)\n"
        "00:12.0 System peripheral: Intel Corporation Atom Processor C3000 Series SMBus Contoller - Host (rev 11)\n"
        "00:13.0 SATA controller: Intel Corporation Atom Processor C3000 Series SATA Controller 0 (rev 11)\n"
        "00:15.0 USB controller: Intel Corporation Atom Processor C3000 Series USB 3.0 xHCI Controller (rev 11)\n"),
    "Intel(R) Celeron(R) J4125 CPU @ 2.00GHz": (
        "00:00.0 Host bridge: Intel Corporation Gemini Lake Host Bridge (rev 06)\n"
        "00:02.0 VGA compatible controller: Intel Corporation GeminiLake [UHD Graphics 600] (rev 06)\n"
        "00:12.0 SATA controller: Intel Corporation Celeron/Pentium Silver Processor SATA Controller (rev 06)\n"
        "00:15.0 USB controller: Intel Corporation Celeron/Pentium Silver Processor USB 3.0 xHCI Controller (rev 06)\n"),
}


def device_content(persona, ident, files, entries, cmdline, boot, processes, accounts=None, coreutils=False,
                   txtcmds=None):
    """The Content of a device: accounts, kernel files, the file system table
    (mount, /proc/mounts, the patched df), dmesg, CPU commands, the style of top (BusyBox, or procps when
    coreutils) and the vendor's commands (txtcmds); with extra["busybox"] Cowrie's
    busybox runs the applets and prints the help of that BusyBox."""
    hw, r = ident.hardware, ident.release
    processes = assign_processes(ident, processes)
    passwd, group, shadow = accounts or (embedded_passwd(persona.user, persona.uid, persona.gid, persona.shell),
                                         embedded_group(persona.user, persona.gid),
                                         embedded_shadow(persona.user, ident))
    all_files = base_files(persona, ident, passwd, group, shadow)
    mount, proc_mounts, filesystems = fs_tables(ident, entries)
    all_files["proc/mounts"] = proc_mounts
    all_files["proc/modules"] = proc_modules(persona, ident)
    all_files.update(files)
    commands = {
        "bin/dmesg": device_dmesg(persona, ident, cmdline, boot),
        "bin/mount": mount,
        "bin/ulimit": ULIMIT,
        "usr/bin/lscpu": lscpu(persona, hw, with_bogomips=persona.hardware_platform != "x86_64"),
        "usr/bin/nproc": nproc(hw),
    }
    if hw.cpu_model in NAS_LSPCI:
        commands["usr/bin/lspci"] = NAS_LSPCI[hw.cpu_model]
    commands.update(txtcmds or {})
    # /etc/shadow was last written on its newest lastchange day
    banner = r.extra.get("busybox")
    return Content(files=all_files, txtcmds=commands, processes=processes,
                   ctimes={"etc/shadow": shadow_ctime(ident)},
                   programs=("bin/busybox",) if banner else (),
                   busybox_help=busybox_help(banner, busybox_applets(persona, ident)) if banner else "",
                   # the kernel threads and helpers of a small system (top counts them, ps lists them not)
                   tasks=len(processes) + (60 if coreutils else 40), top_style="procps" if coreutils else "busybox",
                   filesystems=filesystems)


# --- builders --------------------------------------------------------------------------

def _mtd_root(cmdline):
    """The MTD minor of root=/dev/mtdblockN."""
    return int(re.search(r"root=/dev/mtdblock(\d+)", cmdline).group(1))


def build_dlink(persona, ident):
    """D-Link DIR-859 (QCA9563, the wrgac37 SDK of Alpha Networks): the version
    files under /etc/config, a squashfs root on the MTD partition of the built-in
    command line, xmldb and telnetd in ps."""
    r, hw = ident.release, ident.hardware
    x = r.extra
    files = {
        "etc/config/buildver": f"{r.os_version}\n",
        "etc/config/buildrev": f"{x['buildrev']}\n",
        "etc/config/image_sign": f"{x['image_sign']}\n",
    }
    # the built-in command line of the 1.05B03 kernel
    cmdline = "console=ttyS0,115200 root=/dev/mtdblock0 rw mem=64m"
    entries = [
        fs_entry("rootfs", "/", "rootfs", "rw"),
        fs_entry("/dev/root", "/", "squashfs", "ro,relatime", hw.disk_kb, hw.disk_kb),
        fs_entry("proc", "/proc", "proc", "rw,relatime"),
        fs_entry("sysfs", "/sys", "sysfs", "rw,relatime"),
        fs_entry("ramfs", "/var", "ramfs", "rw,relatime"),
    ]
    boot = [f"VFS: Mounted root (squashfs filesystem) readonly on device 31:{_mtd_root(cmdline)}.",
            "Freeing unused kernel memory: 184k freed"]
    processes = [
        ps_entry("root", 0, "init", vsz=1584, rss=472),
        ps_entry("root", 0, f"xmldb -n {x['image_sign']} -t", vsz=1812, rss=900),
        ps_entry("root", 0, "/usr/sbin/httpd -f /var/run/httpd.conf", vsz=1632, rss=588),
        ps_entry("root", 0, "/usr/sbin/udhcpd /var/servd/LAN-1-udhcpd.conf", vsz=1580, rss=404),
        ps_entry("root", 0, "telnetd -i br0", vsz=1584, rss=388),
        ps_entry("root", 0, "/usr/sbin/dropbear -p 22", vsz=1180, rss=432),
        ps_entry(persona.user, 0, "-sh", vsz=1588, rss=480, tty="pts/0"),
    ]
    return device_content(persona, ident, files, entries, cmdline, boot, processes)


def build_tplink(persona, ident):
    """TP-Link TL-WR841N v14 (MT7628, the 2.6.36 SDK of MediaTek): squashfs root on
    mtdblock5 of the built-in command line, cos / httpd / dropbear in ps.
    Unverified: the process list."""
    hw = ident.hardware
    cmdline = "console=ttyS1,57600n8 root=/dev/mtdblock5"
    entries = [
        fs_entry("rootfs", "/", "rootfs", "rw"),
        fs_entry("/dev/root", "/", "squashfs", "ro,relatime", hw.disk_kb, hw.disk_kb),
        fs_entry("proc", "/proc", "proc", "rw,relatime"),
        fs_entry("ramfs", "/var", "ramfs", "rw,relatime"),
        fs_entry("sysfs", "/sys", "sysfs", "rw,relatime"),
    ]
    boot = [f"VFS: Mounted root (squashfs filesystem) readonly on device 31:{_mtd_root(cmdline)}.",
            "Freeing unused kernel memory: 132k freed"]
    processes = [
        ps_entry("root", 0, "init", vsz=1220, rss=396),
        ps_entry("root", 0, "/usr/bin/cos", vsz=2896, rss=1112),
        ps_entry("root", 0, "/usr/bin/httpd", vsz=2184, rss=812),
        ps_entry("root", 0, "/usr/bin/dnsProxy", vsz=1240, rss=392),
        ps_entry("root", 0, "dropbear -p 22 -r /var/tmp/dropbear/dropbear_rsa_host_key -A /var/tmp/dropbear/dropbearpwd",
                 vsz=1032, rss=372),
        ps_entry(persona.user, 0, "-sh", vsz=1224, rss=404, tty="pts/0"),
    ]
    return device_content(persona, ident, {}, entries, cmdline, boot, processes)


def build_zyxel(persona, ident):
    """Zyxel NAS326 (Armada 380): the firmware version under /firmware/mnt/info,
    the system image on /ram_bin, the data volume on /i-data/<volume id>.
    Unverified: the ramdisk root (the image frees /dev/ram1 after boot), the
    process list."""
    r, hw = ident.release, ident.hardware
    volume = f"/i-data/{rng_for(ident, 'volume').getrandbits(32):08x}"
    cmdline = "console=ttyS0,115200 root=/dev/ram0 rw initrd=0x2000040,48M"
    entries = [
        fs_entry("/dev/root", "/", "ext2", "rw,relatime,errors=continue", 98304, used_kb(ident, "root", 98304, 0.55, 0.7)),
        fs_entry("proc", "/proc", "proc", "rw,relatime"),
        fs_entry("sysfs", "/sys", "sysfs", "rw,relatime"),
        fs_entry("/dev/loop0", "/ram_bin", "ext2", "ro,relatime", 144384, 139020),
        fs_entry("/dev/md0", volume, "ext4", "rw,relatime,user_xattr,barrier=1,data=ordered", hw.disk_kb,
                 used_kb(ident, "volume", hw.disk_kb, 0.25, 0.65)),
    ]
    boot = ["RAMDISK: gzip image found at block 0",
            "VFS: Mounted root (ext2 filesystem) on device 1:0.",
            "Freeing unused kernel memory: 228K (c0866000 - c089f000)"]
    files = {"firmware/mnt/info/fwversion": f"{r.os_version}\n"}
    processes = [
        ps_entry("root", 0, "init", vsz=1760, rss=612),
        ps_entry("root", 0, "/sbin/syslog-ng", vsz=5616, rss=1876),
        ps_entry("root", 0, "/usr/sbin/sshd", vsz=5128, rss=1092),
        ps_entry("root", 0, "/usr/local/apache/bin/httpd -f /etc/service_conf/httpd.conf", vsz=13632, rss=4980),
        ps_entry("root", 0, "/usr/sbin/smbd -D", vsz=21540, rss=6312),
        ps_entry("root", 0, "/usr/sbin/nmbd -D", vsz=11080, rss=3240),
        ps_entry(persona.user, 0, "-sh", vsz=1764, rss=640, tty="pts/0"),
    ]
    return device_content(persona, ident, files, entries, cmdline, boot, processes)


OPENWRT_BANNER = """  _______                     ________        __
 |       |.-----.-----.-----.|  |  |  |.----.|  |_
 |   -   ||  _  |  -__|     ||  |  |  ||   _||   _|
 |_______||   __|_____|__|__||________||__|  |____|
          |__| W I R E L E S S   F R E E D O M
 -----------------------------------------------------
 OpenWrt {version}, {revision}
 -----------------------------------------------------
"""
OPENWRT_PASSWD = """root:x:0:0:root:/root:/bin/ash
daemon:*:1:1:daemon:/var:/bin/false
ftp:*:55:55:ftp:/home/ftp:/bin/false
network:*:101:101:network:/var:/bin/false
nobody:*:65534:65534:nobody:/var:/bin/false
ntp:x:123:123:ntp:/var/run/ntp:/bin/false
dnsmasq:x:453:453:dnsmasq:/var/run/dnsmasq:/bin/false
logd:x:514:514:logd:/var/run/logd:/bin/false
ubus:x:81:81:ubus:/var/run/ubus:/bin/false
"""
OPENWRT_GROUP = """root:x:0:
daemon:x:1:
adm:x:4:
mail:x:8:
audio:x:29:
www-data:x:33:
ftp:x:55:
users:x:100:
network:x:101:
nogroup:x:65534:
ntp:x:123:ntp
dnsmasq:x:453:dnsmasq
logd:x:514:logd
ubus:x:81:ubus
"""


def build_openwrt(persona, ident):
    """OpenWrt on an MT7621 router with NOR flash: squashfs /rom and a jffs2
    overlay from the split firmware partition, the release files of base-files,
    procd and its services in ps."""
    r, hw = ident.release, ident.hardware
    x = r.extra
    version, revision = r.os_version, x["revision"]
    rootfs = x["rootfs_mtd"][hw.machine]
    _, root_day, _ = shadow_days(ident)
    files = {
        "etc/os-release": f"""NAME="OpenWrt"
VERSION="{version}"
ID="openwrt"
ID_LIKE="lede openwrt"
PRETTY_NAME="OpenWrt {version}"
VERSION_ID="{r.os_version_id}"
HOME_URL="https://openwrt.org/"
BUG_URL="https://bugs.openwrt.org/"
SUPPORT_URL="https://forum.openwrt.org/"
BUILD_ID="{revision}"
OPENWRT_BOARD="{x['target']}"
OPENWRT_ARCH="{x['pkg_arch']}"
OPENWRT_TAINTS=""
OPENWRT_DEVICE_MANUFACTURER="OpenWrt"
OPENWRT_DEVICE_MANUFACTURER_URL="https://openwrt.org/"
OPENWRT_DEVICE_PRODUCT="Generic"
OPENWRT_DEVICE_REVISION="v0"
OPENWRT_RELEASE="OpenWrt {version} {revision}"
""",
        "etc/openwrt_release": f"""DISTRIB_ID='OpenWrt'
DISTRIB_RELEASE='{version}'
DISTRIB_REVISION='{revision}'
DISTRIB_TARGET='{x['target']}'
DISTRIB_ARCH='{x['pkg_arch']}'
DISTRIB_DESCRIPTION='OpenWrt {version} {revision}'
DISTRIB_TAINTS=''
""",
        "etc/openwrt_version": f"{revision}\n",
        "etc/banner": OPENWRT_BANNER.format(version=version, revision=revision),
        "tmp/sysinfo/model": f"{hw.machine}\n",
        "tmp/sysinfo/board_name": f"{x['board_names'][hw.machine]}\n",
    }
    shadow = (f"root:$1$root$A3qH7I7U2vW0nLJ9rKaNl/:{root_day}:0:99999:7:::\n"
              + "".join(f"{name}:*:0:0:99999:7:::\n" for name in ("daemon", "ftp", "network", "nobody"))
              + "".join(f"{name}:x:0:0:99999:7:::\n" for name in ("ntp", "dnsmasq", "logd", "ubus")))
    overlay = hw.disk_kb - x["kernel_kb"] - x["rootfs_kb"]
    tmp = hw.mem_kb // 2
    entries = [
        fs_entry("/dev/root", "/rom", "squashfs", "ro,relatime", x["rootfs_kb"], x["rootfs_kb"]),
        fs_entry("proc", "/proc", "proc", "rw,nosuid,nodev,noexec,noatime"),
        fs_entry("sysfs", "/sys", "sysfs", "rw,nosuid,nodev,noexec,noatime"),
        fs_entry("tmpfs", "/tmp", "tmpfs", "rw,nosuid,nodev,noatime", tmp, used_kb(ident, "tmp", tmp, 0.001, 0.004)),
        fs_entry(f"/dev/mtdblock{rootfs + 1}", "/overlay", "jffs2", "rw,noatime", overlay, 496),
        fs_entry("overlayfs:/overlay", "/", "overlay",
                 "rw,noatime,lowerdir=/,upperdir=/overlay/upper,workdir=/overlay/work", overlay, 496),
        fs_entry("tmpfs", "/dev", "tmpfs", "rw,nosuid,relatime,size=512k,mode=755", 512, 0),
        fs_entry("devpts", "/dev/pts", "devpts", "rw,nosuid,noexec,relatime,mode=600,ptmxmode=000"),
    ]
    cmdline = "console=ttyS0,115200n8 rootfstype=squashfs,jffs2"
    boot = [f"SoC Type: {hw.board}",
            f"mtd: device {rootfs} (rootfs) set to be root filesystem",
            f"VFS: Mounted root (squashfs filesystem) readonly on device 31:{rootfs}.",
            "jffs2: notice: (1) jffs2_build_xattr_subsystem: complete building xattr subsystem."]
    processes = [
        ps_entry("root", 0, "/sbin/procd", vsz=1580, rss=1012),
        ps_entry("root", 0, "/sbin/ubusd", vsz=1212, rss=760),
        ps_entry("logd", 0, "/sbin/logd -S 64", vsz=1240, rss=788),
        ps_entry("root", 0, "/sbin/netifd", vsz=1880, rss=1276),
        ps_entry("root", 0, "/usr/sbin/odhcpd", vsz=1448, rss=840),
        ps_entry("root", 0, "/usr/sbin/dropbear -F -P /var/run/dropbear.1.pid -p 22 -K 300 -T 3", vsz=1104, rss=644),
        ps_entry("root", 0, "/usr/sbin/uhttpd -f -h /www -r OpenWrt -x /cgi-bin -u /ubus -t 60 -T 30 -k 20 -A 1 "
                            "-n 3 -N 100 -R -p 0.0.0.0:80 -p [::]:80", vsz=1880, rss=1132),
        ps_entry("dnsmasq", 0, "/usr/sbin/dnsmasq -C /var/etc/dnsmasq.conf.cfg01411c -k -x "
                               "/var/run/dnsmasq/dnsmasq.cfg01411c.pid", vsz=1380, rss=896),
        ps_entry("root", 0, "-ash", vsz=1232, rss=772, tty="pts/0"),
    ]
    content = device_content(persona, ident, files, entries, cmdline, boot, processes,
                             accounts=(OPENWRT_PASSWD, OPENWRT_GROUP, shadow))
    # opkg is a command of the patched Cowrie (system.opkg_keys)
    content.programs = (*content.programs, "bin/opkg")
    return content


# QNAP model per CPU of the catalog's hardware
QNAP_MODELS = {"Intel(R) Celeron(R) CPU  J1900  @ 1.99GHz": "TS-451+", "Intel(R) Celeron(R) N5105 @ 2.00GHz": "TS-464"}
QPKG_CLI_HELP = """Usage: qpkg_cli [options]
  -l, --list              list installed QPKGs
  -s, --status QPKG       show the status of a QPKG
  -m, --manual QPKG       install a QPKG from its file
  -h, --help              show this help
"""


def build_qnap(persona, ident):
    """QNAP QTS on an x86 NAS: the system runs from an initramfs (tmpfs root), the
    system partition md9 on /mnt/HDA_ROOT, the data volume on CACHEDEV1_DATA.
    Unverified: command line, mount options, process list."""
    r, hw = ident.release, ident.hardware
    model = QNAP_MODELS[hw.cpu_model]
    config = (f"[System]\nModel = {model}\nVersion = {r.os_version_id}\nBuild Number = {r.extra['build']}\n"
              f"Number = {r.extra['number']}\nTime Zone = UTC\n")
    files = {
        "etc/config/uLinux.conf": config,
        "etc/default_config/uLinux.conf": f"[System]\nModel = {model}\n",
    }
    dev, shm, tmp = hw.mem_kb // 2, hw.mem_kb // 2, 65536
    entries = [
        fs_entry("none", "/", "tmpfs", "rw,mode=0755,size=409600k", 409600, used_kb(ident, "root", 409600, 0.7, 0.85)),
        fs_entry("devtmpfs", "/dev", "devtmpfs", f"rw,size={dev}k,nr_inodes=1048576,mode=755", dev, 8),
        fs_entry("/proc", "/proc", "proc", "rw,relatime"),
        fs_entry("sysfs", "/sys", "sysfs", "rw,relatime"),
        fs_entry("tmpfs", "/tmp", "tmpfs", "rw,size=65536k", tmp, used_kb(ident, "tmp", tmp, 0.02, 0.06)),
        fs_entry("tmpfs", "/dev/shm", "tmpfs", "rw", shm, 132),
        fs_entry("/dev/md9", "/mnt/HDA_ROOT", "ext3", "rw,data=ordered", 505964, used_kb(ident, "md9", 505964, 0.6, 0.8)),
        fs_entry("/dev/mapper/cachedev1", "/share/CACHEDEV1_DATA", "ext4",
                 "rw,usrjquota=aquota.user,jqfmt=vfsv0,user_xattr,data=ordered,data_err=abort,delalloc,nopriv,nodiscard,noacl",
                 hw.disk_kb, used_kb(ident, "volume", hw.disk_kb, 0.25, 0.65)),
    ]
    cmdline = "console=ttyS0,115200n8 pcie_aspm=off pci=noaer"
    boot = ["Unpacking initramfs...", "Freeing initrd memory: 70768K", "Freeing unused kernel image memory: 2140K",
            "Run /init as init process"]
    processes = [
        ps_entry("root", 0, "init", vsz=3364, rss=1088),
        ps_entry("root", 0, "/sbin/daemon_mgr", vsz=8172, rss=2316),
        ps_entry("root", 0, "/sbin/qLogEngined", vsz=19248, rss=5560),
        ps_entry("root", 0, "/usr/sbin/sshd -f /etc/config/ssh/sshd_config -p 22", vsz=13880, rss=4012),
        ps_entry("root", 0, "/usr/local/sbin/_thttpd_ -p 58080 -nor -nos -u admin -d /home/httpd", vsz=7716, rss=2096),
        ps_entry("root", 0, "/usr/local/apache/bin/apache_proxy -k start -f /etc/apache-sys-proxy.conf",
                 vsz=48224, rss=9132),
        ps_entry("admin", 0, "-sh", vsz=2472, rss=1324, tty="pts/0"),
    ]
    return device_content(persona, ident, files, entries, cmdline, boot, processes,
                          txtcmds={"sbin/qpkg_cli": QPKG_CLI_HELP})


# Synology model and platform per CPU of the catalog's hardware
SYNOLOGY_MODELS = {"Intel(R) Atom(TM) CPU C3538 @ 2.10GHz": ("DS1618+", "denverton", 4),
                   "Intel(R) Celeron(R) J4125 CPU @ 2.00GHz": ("DS920+", "geminilake", 2)}
SYNOPKG_HELP = """Usage: synopkg <command> [<args>...]

  list [--name] [--home]          List installed packages
  status <package>                Show the status of a package
  start <package>                 Start a package
  stop <package>                  Stop a package
  install <spk>                   Install a package file
"""


def build_synology(persona, ident):
    """Synology DSM 7.2 on an x86 DiskStation: system on /dev/md0 (ext4), data on
    the btrfs volume1 of the storage pool, VERSION under /etc.defaults.
    Unverified: command line, mount options, builddate, process list."""
    r, hw = ident.release, ident.hardware
    x = r.extra
    model, platform, ports = SYNOLOGY_MODELS[hw.cpu_model]
    unique = model.lower()[2:] if model.startswith("DS") else model.lower()
    major, minor, micro = r.os_version.split(".")
    version = f"""majorversion="{major}"
minorversion="{minor}"
major="{major}"
minor="{minor}"
micro="{micro}"
buildphase="GM"
buildnumber="{x['build']}"
smallfixnumber="{x['smallfix']}"
nano="{x['smallfix']}"
base="{x['build']}"
productversion="{r.os_version}"
os_name="DSM"
builddate="{x['builddate']}"
"""
    files = {
        "etc.defaults/VERSION": version,
        "etc/VERSION": version,
        # the model without its DS: synology_geminilake_920+ (uname -a of DSM ends with it)
        "etc.defaults/synoinfo.conf": f'unique="synology_{platform}_{unique}"\nupnpmodelname="{model}"\n',
    }
    half = hw.mem_kb // 2
    entries = [
        fs_entry("/dev/md0", "/", "ext4", "rw,relatime,data=ordered", 2385528,
                 used_kb(ident, "root", 2385528, 0.55, 0.72)),
        fs_entry("devtmpfs", "/dev", "devtmpfs", f"rw,nosuid,noexec,relatime,size={half}k,mode=755", half, 0),
        fs_entry("proc", "/proc", "proc", "rw,nosuid,nodev,noexec,relatime"),
        fs_entry("sysfs", "/sys", "sysfs", "rw,nosuid,nodev,noexec,relatime"),
        fs_entry("tmpfs", "/dev/shm", "tmpfs", "rw,nosuid,nodev", half, 4),
        fs_entry("tmpfs", "/run", "tmpfs", "rw,nosuid,nodev,mode=755", half, used_kb(ident, "run", half, 0.003, 0.01)),
        fs_entry("tmpfs", "/sys/fs/cgroup", "tmpfs", "ro,nosuid,nodev,noexec,mode=755", half, 0),
        fs_entry("tmpfs", "/tmp", "tmpfs", "rw,nosuid,nodev,noexec", half, used_kb(ident, "tmp", half, 0.0005, 0.002)),
        fs_entry("/dev/mapper/cachedev_0", "/volume1", "btrfs",
                 "rw,nodev,relatime,ssd,synoacl,space_cache=v2,auto_reclaim_space,metadata_ratio=50,"
                 "block_group_cache_tree,subvolid=256,subvol=/@syno",
                 hw.disk_kb, used_kb(ident, "volume", hw.disk_kb, 0.25, 0.65)),
    ]
    cmdline = f"syno_hw_version={model} console=ttyS0,115200n8 netif_num={ports} root=/dev/md0 HddHotplug=1"
    boot = ["md: Autodetecting RAID arrays.",
            "EXT4-fs (md0): mounted filesystem with ordered data mode. Opts: (null)",
            "VFS: Mounted root (ext4 filesystem) on device 9:0."]
    processes = [
        ps_entry("root", 0, "/sbin/init", vsz=13240, rss=6508, stat="Ss"),
        ps_entry("root", 0, "/usr/lib/systemd/systemd-journald", vsz=31516, rss=7236, stat="Ss"),
        ps_entry("root", 0, "/usr/syno/sbin/synostoraged", vsz=187280, rss=9340),
        ps_entry("root", 0, "/usr/syno/sbin/synoscgi", vsz=301456, rss=27880, stat="Ss"),
        ps_entry("root", 0, "nginx: master process /usr/bin/nginx -g daemon off;", vsz=70428, rss=12160, stat="Ss"),
        ps_entry("root", 0, "/usr/bin/sshd -D", vsz=14736, rss=5228, stat="Ss"),
        ps_entry(persona.user, 0, "-sh", vsz=4772, rss=3164, tty="pts/0", stat="Ss"),
    ]
    return device_content(persona, ident, files, entries, cmdline, boot, processes, coreutils=True,
                          txtcmds={"usr/syno/bin/synopkg": SYNOPKG_HELP})


EDGEOS_MOTD = """Welcome to EdgeOS

By logging in, accessing, or using the Ubiquiti product, you
acknowledge that you have read and understood the Ubiquiti
License Agreement (available in the Web UI at, by default,
http://192.168.1.1) and agree to be bound by its terms.
"""


def build_ubiquiti(persona, ident):
    """Ubiquiti EdgeRouter X (MT7621) on EdgeOS 2.0, a Debian 9 userland: the
    squashfs image of the firmware under an overlay on the UBI volume, Debian's
    os-release, the EdgeOS version and config.boot. Unverified: command line and
    mounts (rootsqimg / rootsqwdir as on the other EdgeRouters), process list."""
    r, hw = ident.release, ident.hardware
    x = r.extra
    files = {
        "etc/os-release": """PRETTY_NAME="Debian GNU/Linux 9 (stretch)"
NAME="Debian GNU/Linux"
VERSION_ID="9"
VERSION="9 (stretch)"
VERSION_CODENAME=stretch
ID=debian
HOME_URL="https://www.debian.org/"
SUPPORT_URL="https://www.debian.org/support"
BUG_REPORT_URL="https://bugs.debian.org/"
""",
        "etc/debian_version": "9.13\n",
        "etc/version": f"EdgeRouter.ER-e50.{x['image']}\n",
        "etc/issue": "Welcome to EdgeOS \\n \\l\n\n",
        "etc/issue.net": "Welcome to EdgeOS\n",
        "etc/motd": EDGEOS_MOTD,
        "config/config.boot": f"system {{\n    host-name {ident.hostname}\n    login {{\n"
                              f"        user {persona.user} {{\n            level admin\n        }}\n    }}\n"
                              "    time-zone UTC\n}\n",
    }
    squash = x["squashfs_kb"]
    run = hw.mem_kb // 10
    entries = [
        fs_entry("ubi0:troot", "/root.dev", "ubifs", "rw,noatime", hw.disk_kb, used_kb(ident, "ubi", hw.disk_kb, 0.38, 0.45)),
        fs_entry("/dev/loop0", "/root.ro", "squashfs", "ro,relatime", squash, squash),
        fs_entry("overlay", "/", "overlay", "rw,noatime,lowerdir=/root.ro,upperdir=/root.dev/w,workdir=/root.dev/w.work",
                 hw.disk_kb, used_kb(ident, "ubi", hw.disk_kb, 0.38, 0.45)),
        fs_entry("proc", "/proc", "proc", "rw,nosuid,nodev,noexec,relatime"),
        fs_entry("sysfs", "/sys", "sysfs", "rw,nosuid,nodev,noexec,relatime"),
        fs_entry("tmpfs", "/run", "tmpfs", f"rw,nosuid,noexec,relatime,size={run}k,mode=755", run,
                 used_kb(ident, "run", run, 0.01, 0.03)),
        fs_entry("devpts", "/dev/pts", "devpts", "rw,nosuid,noexec,relatime,gid=5,mode=620,ptmxmode=000"),
    ]
    cmdline = ("console=ttyS1,57600n8 root=ubi0:troot ubi.mtd=3 rootfstype=ubifs rootsqimg=squashfs.img "
               "rootsqwdir=w")
    boot = ["UBIFS (ubi0:0): UBIFS: mounted UBI device 0, volume 0, name \"troot\"",
            "VFS: Mounted root (ubifs filesystem) on device 0:13."]
    processes = [
        ps_entry("root", 0, "/sbin/init", vsz=2900, rss=1724, stat="Ss"),
        ps_entry("root", 0, "/usr/sbin/rsyslogd", vsz=29352, rss=2904, stat="Ssl"),
        ps_entry("root", 0, "/usr/sbin/ubnt-daemon", vsz=6820, rss=2300),
        ps_entry("root", 0, "/usr/sbin/sshd", vsz=7728, rss=3440, stat="Ss"),
        ps_entry("root", 0, "/usr/sbin/lighttpd -f /etc/lighttpd/lighttpd.conf", vsz=5992, rss=3588, stat="Ss"),
        ps_entry("nobody", 0, "/usr/sbin/dnsmasq -x /var/run/dnsmasq/dnsmasq.pid", vsz=4372, rss=1688),
        ps_entry(persona.user, 0, "-vbash", vsz=4964, rss=3164, tty="pts/0", stat="Ss"),
    ]
    arch = x["deb_arch"]
    content = device_content(persona, ident, files, entries, cmdline, boot, processes, coreutils=True, txtcmds={
        "usr/bin/dpkg": f"Debian 'dpkg' package management program version {r.packages['dpkg']} ({arch}).\n",
    })
    # apt-get is Cowrie's patched apt-get (apt 1.4 without sources: system.shell_keys)
    content.programs = (*content.programs, "usr/bin/apt-get")
    return content


IPCAM_RCS = """#!/bin/sh
# mount the system, copy the camera application from the dav partition and start it
/bin/mount -a
/bin/mount -t ubifs ubi1:dav /dav -o ro
/bin/cp -a /dav/. /home/
/usr/sbin/telnetd
/sbin/dropbear -R
/bin/sh /home/process_cfg.sh &
# process_cfg.sh restarts davinci when it stops
/home/davinci &
"""


def build_ipcam(persona, ident):
    """A Hikvision-style IP camera (one Cortex-A7, Linux 3.18): initramfs root,
    the camera application davinci from the read-only dav partition copied to
    /home and supervised by process_cfg.sh. Unverified: the whole layout is a
    composite of public descriptions, not taken from one image."""
    r, hw = ident.release, ident.hardware
    nominal = round(hw.mem_kb / 1024 / 64) * 64
    home = 32768
    entries = [
        fs_entry("rootfs", "/", "rootfs", "rw"),
        fs_entry("proc", "/proc", "proc", "rw,relatime"),
        fs_entry("sysfs", "/sys", "sysfs", "rw,relatime"),
        fs_entry("tmpfs", "/dev", "tmpfs", "rw,relatime", 1024, 0),
        fs_entry("/dev/ubi1_0", "/dav", "ubifs", "ro,relatime", hw.disk_kb, used_kb(ident, "dav", hw.disk_kb, 0.7, 0.8)),
        fs_entry("tmpfs", "/home", "tmpfs", f"rw,relatime,size={home}k", home, used_kb(ident, "home", home, 0.55, 0.62)),
    ]
    cmdline = f"mem={nominal}M console=ttyAMA0,115200 ubi.mtd=5"
    boot = ["Unpacking initramfs...", "Freeing unused kernel memory: 204K",
            'UBIFS (ubi1:0): UBIFS: mounted UBI device 1, volume 0, name "dav", R/O mode']
    files = {
        "etc/version": f"{r.extra['firmware_version']}\n",
        "etc/init.d/rcS": IPCAM_RCS,
    }
    davinci = hw.mem_kb * 3 // 10
    processes = [
        ps_entry("root", 0, "init", vsz=1916, rss=468),
        ps_entry("root", 0, "/usr/sbin/telnetd", vsz=1912, rss=352),
        ps_entry("root", 0, "/sbin/dropbear -R", vsz=1168, rss=416),
        ps_entry("root", 0, "/bin/sh /home/process_cfg.sh", vsz=1920, rss=440),
        ps_entry("root", 0, "/home/davinci", vsz=davinci * 2, rss=davinci),
        ps_entry(persona.user, 0, "-sh", vsz=1920, rss=480, tty="pts/0"),
    ]
    return device_content(persona, ident, files, entries, cmdline, boot, processes)
