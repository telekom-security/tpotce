"""Device personas (sh/BusyBox, embedded firmware): package managers, command
inventory, processes, disks and one builder per family. Firmware details
(model, firmware strings, BusyBox build) come from release.extra."""
from .common import (assign_processes, base_files, base_txtcmds, deb_arch, device_top, embedded_group,
                     embedded_passwd, embedded_shadow, lscpu, ps_entry, rng_for, shadow_days)
from .model import Content

_LSPCI = {
    "qnap": ("00:00.0 Host bridge: Intel Corporation Atom Processor Z36xxx/Z37xxx Series SoC Transaction Register (rev 0e)\n"
             "00:13.0 SATA controller: Intel Corporation Atom Processor E3800 Series SATA AHCI Controller (rev 0e)\n"
             "00:14.0 USB controller: Intel Corporation Atom Processor Z36xxx/Z37xxx, Celeron N2000 Series USB xHCI (rev 0e)\n"),
    "synology": ("00:00.0 Host bridge: Intel Corporation Atom Processor C3000 Host Bridge\n"
                 "00:14.0 USB controller: Intel Corporation Atom Processor C3000 USB 3.0 xHCI Controller\n"
                 "00:17.0 SATA controller: Intel Corporation Atom Processor C3000 SATA Controller\n"),
}


def device_package_manager_txtcmds(persona, ident):
    family = persona.family
    if family == "openwrt":
        return {"bin/opkg": """opkg must have one sub-command argument
usage: opkg [options...] sub-command [arguments...]

Package Manipulation:
\tupdate\t\t\tUpdate list of available packages
\tinstall <pkg>\t\tInstall package(s)
\tremove <pkg>\t\tRemove package(s)
\tlist-installed\t\tList installed packages
"""}
    if family == "ubiquiti":
        return {
            "usr/bin/apt-get": "Reading package lists... Done\nBuilding dependency tree... Done\nE: Unable to locate package\n",
            "usr/bin/dpkg": f"Debian 'dpkg' package management program version "
                            f"{ident.release.packages['dpkg']} ({deb_arch(persona)}).\n",
        }
    if family == "qnap":
        return {"sbin/qpkg_cli": """Usage: qpkg_cli [options]
  --list           list installed packages
  --status NAME    show package status
  --help           show this help
"""}
    if family == "synology":
        return {"usr/syno/bin/synopkg": """Copyright (c) 2003-2023 Synology Inc. All rights reserved.
Usage: synopkg <command> [package]
  list
  status <package>
  start <package>
  stop <package>
"""}
    return {}


def embedded_command_inventory_txtcmds(persona, files):
    txtcmds = {}
    if persona.family in _LSPCI:
        txtcmds["usr/bin/lspci"] = _LSPCI[persona.family]
    if "bin/busybox" in files:
        txtcmds["bin/busybox"] = files["bin/busybox"]
    return txtcmds


def embedded_processes(persona, ident):
    family, user = persona.family, persona.user
    if family == "openwrt":
        entries = [
            ps_entry("root", 0, "/sbin/procd", mem=0.4, vsz=1540, rss=780),
            ps_entry("root", 0, "/sbin/ubusd", mem=0.2, vsz=1240, rss=620),
            ps_entry("root", 0, "/sbin/netifd", mem=0.3, vsz=1760, rss=900),
            ps_entry("root", 0, "/usr/sbin/dropbear -F -P /var/run/dropbear.1.pid", mem=0.2, vsz=1440, rss=720),
            ps_entry("root", 0, "/usr/sbin/uhttpd -f -h /www -r OpenWrt", mem=0.3, vsz=1708, rss=864),
            ps_entry("root", 0, "ash", mem=0.1, vsz=1224, rss=612, tty="pts/0"),
        ]
    elif family == "qnap":
        entries = [
            ps_entry("admin", 0, "init", mem=0.2, vsz=3028, rss=1040),
            ps_entry("admin", 0, "/sbin/daemon_mgr", mem=0.3, vsz=8104, rss=2236),
            ps_entry("admin", 0, "/sbin/qLogEngined", mem=0.5, vsz=18572, rss=5232),
            ps_entry("admin", 0, "/usr/sbin/sshd -f /etc/config/ssh/sshd_config -D", mem=0.2, vsz=12300, rss=3120),
            ps_entry("admin", 0, "/sbin/thttpd -p 8080 -d /home/httpd", mem=0.2, vsz=7624, rss=2024),
            ps_entry("admin", 0, "sh", mem=0.1, vsz=2184, rss=1088, tty="pts/0"),
        ]
    elif family == "synology":
        entries = [
            ps_entry("root", 0, "/sbin/init", mem=0.3, vsz=11840, rss=2424, stat="Ss"),
            ps_entry("root", 0, "/usr/syno/bin/synoservice --bootup", mem=0.4, vsz=22048, rss=6024),
            ps_entry("root", 0, "/usr/syno/sbin/synocgid", mem=0.2, vsz=14492, rss=3096),
            ps_entry("root", 0, "/usr/sbin/sshd -D", mem=0.2, vsz=12420, rss=3820, stat="Ss"),
            ps_entry("root", 0, "/usr/syno/sbin/synoscheduler", mem=0.2, vsz=16884, rss=3596),
            ps_entry("admin", 0, "sh", mem=0.1, vsz=2328, rss=1092, tty="pts/0"),
        ]
    elif family == "ubiquiti":
        entries = [
            ps_entry("root", 0, "/sbin/init", mem=0.2, vsz=2936, rss=1044, stat="Ss"),
            ps_entry("root", 0, "/sbin/ubnt-util", mem=0.2, vsz=3640, rss=1280),
            ps_entry("root", 0, "/usr/sbin/ubnt-daemon", mem=0.3, vsz=6820, rss=2300),
            ps_entry("root", 0, "/usr/sbin/sshd -D", mem=0.2, vsz=11384, rss=2784, stat="Ss"),
            ps_entry("root", 0, "/opt/vyatta/sbin/vyatta-router", mem=0.3, vsz=9488, rss=2956),
            ps_entry(user, 0, "-vbash", mem=0.1, vsz=3916, rss=1420, tty="pts/0"),
        ]
    else:
        entries = [
            ps_entry("root", 0, "init", mem=0.2, vsz=1512, rss=688),
            ps_entry("root", 0, "/sbin/syslogd -n", mem=0.1, vsz=1128, rss=516),
            ps_entry("root", 0, "/sbin/klogd -n", mem=0.1, vsz=1116, rss=508),
            ps_entry("root", 0, "/usr/sbin/dropbear -F -p 22", mem=0.2, vsz=1368, rss=672),
            ps_entry("root", 0, f"/usr/sbin/httpd -h /www -n {ident.hostname}", mem=0.2, vsz=1548, rss=760),
            ps_entry(user, 0, "sh", mem=0.1, vsz=1096, rss=548, tty="pts/0"),
        ]
    return assign_processes(ident, entries)


# --- disks ------------------------------------------------------------------

def _bb_row(fs, size, used, avail, mount):
    pct = -(-used * 100 // (used + avail)) if used + avail else 0
    return f"{fs:<20}{size:>10}{used:>10}{avail:>10}{pct:>4}% {mount}"


_BB_HEADER = "Filesystem           1K-blocks      Used Available Use% Mounted on"


def router_df(ident):
    rng = rng_for(ident, "df")
    disk, tmp = ident.hardware.disk_kb, ident.hardware.mem_kb // 2
    used, tmp_used = int(disk * rng.uniform(0.30, 0.60)), rng.randint(200, 600)
    return "\n".join([_BB_HEADER,
                      _bb_row("rootfs", disk, used, disk - used, "/"),
                      _bb_row("/dev/root", disk, disk, 0, "/rom"),
                      _bb_row("tmpfs", tmp, tmp_used, tmp - tmp_used, "/tmp")]) + "\n"


ROUTER_MOUNT = ("rootfs on / type rootfs (rw)\n"
                "/dev/root on /rom type squashfs (ro,relatime)\n"
                "proc on /proc type proc (rw,nosuid,nodev,noexec,noatime)\n"
                "tmpfs on /tmp type tmpfs (rw,nosuid,nodev,noatime)\n")


def nas_volume(persona, ident):
    """(device, mount point, file system) of the data volume of the vendor."""
    if persona.family == "synology":
        return "/dev/mapper/cachedev_0", "/volume1", "btrfs"
    if persona.family == "iot-nas":
        return "/dev/md0", f"/i-data/{rng_for(ident, 'volume').getrandbits(32):08x}", "ext4"
    return "/dev/mapper/cachedev1", "/share/CACHEDEV1_DATA", "ext4"


def nas_df(persona, ident):
    rng = rng_for(ident, "df")
    disk, tmp = ident.hardware.disk_kb, ident.hardware.mem_kb // 2
    used, tmp_used = int(disk * rng.uniform(0.25, 0.65)), rng.randint(1024, 4096)
    dev, mount, _ = nas_volume(persona, ident)
    return "\n".join([_BB_HEADER,
                      _bb_row("/dev/md9", 521684, 165312, 356372, "/"),
                      _bb_row("tmpfs", tmp, tmp_used, tmp - tmp_used, "/tmp"),
                      _bb_row(dev, disk, used, disk - used, mount)]) + "\n"


def nas_mount(persona, ident):
    dev, mount, fs = nas_volume(persona, ident)
    options = "rw,relatime,data=ordered" if fs == "ext4" else "rw,relatime,space_cache"
    return ("/dev/md9 on / type ext4 (rw,relatime,data=ordered)\n"
            "proc on /proc type proc (rw,nosuid,nodev,noexec,relatime)\n"
            "tmpfs on /tmp type tmpfs (rw,nosuid,nodev,relatime)\n"
            f"{dev} on {mount} type {fs} ({options})\n")


# --- builders -------------------------------------------------------------------

def _device_content(persona, ident, files, nas, passwd=None, group=None, shadow=None, gecos=None):
    processes = embedded_processes(persona, ident)
    all_files = base_files(
        persona, ident,
        passwd or embedded_passwd(persona.user, persona.uid, persona.gid, persona.shell, gecos or persona.user),
        group or embedded_group(persona.user, persona.gid),
        shadow or embedded_shadow(persona.user, ident))
    all_files.update(files)
    txtcmds = base_txtcmds(persona, ident,
                           df=nas_df(persona, ident) if nas else router_df(ident),
                           mount=nas_mount(persona, ident) if nas else ROUTER_MOUNT,
                           lscpu_text=lscpu(persona, ident.hardware, with_bogomips=not nas),
                           top=device_top(ident, processes))
    txtcmds.update(device_package_manager_txtcmds(persona, ident))
    txtcmds.update(embedded_command_inventory_txtcmds(persona, all_files))
    return Content(files=all_files, txtcmds=txtcmds, processes=processes)


def _busybox(release):
    return f"{release.extra['busybox']} multi-call binary.\n"


def build_iot_router(persona, ident):
    r = ident.release
    model = r.extra["model"]
    if persona.os_id == "dlink":
        files = {
            "etc/os-release": f'NAME="D-Link Embedded Linux"\nID=dlink\nPRETTY_NAME="D-Link {model}"\n'
                              f'VERSION_ID="{r.os_version_id}"\n',
            "etc/issue": f"D-Link {model} login: \\l\n",
            "etc/issue.net": f"D-Link {model}\n",
            "etc/motd": f"D-Link {model}\n",
            "etc_ro/version": f"Firmware External Version: {r.os_version}\n"
                              f"Firmware Internal Version: V{r.os_version_id}\n",
            "etc_ro/product.ini": f"model={model}\nvendor=D-Link\n",
        }
    else:
        files = {
            "etc/os-release": f'NAME="TP-Link Embedded Linux"\nID=tplink\nPRETTY_NAME="TP-Link {model}"\n'
                              f'VERSION_ID="{r.os_version_id}"\n',
            "etc/issue": "TP-Link Wireless Router \\n \\l\n",
            "etc/issue.net": "TP-Link Wireless Router\n",
            "etc/motd": f"{model}\n",
            "etc/product-info": f"product_name={model}\nproduct_ver={r.extra['hw_version']}\n",
            "etc/config/system": f"config system\n\toption hostname '{ident.hostname}'\n\toption timezone 'UTC'\n",
        }
    files["bin/busybox"] = _busybox(r)
    return _device_content(persona, ident, files, nas=False, gecos="admin")


def build_iot_nas(persona, ident):
    r = ident.release
    model = r.extra["model"]
    files = {
        "etc/os-release": f'NAME="Zyxel NAS"\nID=zyxel\nPRETTY_NAME="Zyxel {model}"\nVERSION_ID="{r.os_version_id}"\n',
        "etc/issue": f"Welcome to Zyxel {model} \\n \\l\n",
        "etc/issue.net": f"Zyxel {model}\n",
        "etc/motd": f"Zyxel {model}\n",
        "etc/zyxel/model": f"{model}\n",
        "firmware/mnt/info/fwversion": f"{r.os_version}\n",
        "bin/busybox": _busybox(r),
    }
    return _device_content(persona, ident, files, nas=True, gecos="admin")


def build_openwrt(persona, ident):
    r = ident.release
    x = r.extra
    _, root_day, _ = shadow_days(ident)
    files = {
        "etc/os-release": f"""NAME="OpenWrt"
VERSION="{r.os_version}"
ID="openwrt"
ID_LIKE="lede openwrt"
PRETTY_NAME="OpenWrt {r.os_version}"
VERSION_ID="{r.os_version_id}"
HOME_URL="https://openwrt.org/"
BUG_URL="https://bugs.openwrt.org/"
SUPPORT_URL="https://forum.openwrt.org/"
BUILD_ID="{x['revision']}"
LEDE_BOARD="{x['target']}"
LEDE_ARCH="{x['pkg_arch']}"
LEDE_TAINTS="no-all"
""",
        "etc/openwrt_release": f"""DISTRIB_ID='OpenWrt'
DISTRIB_RELEASE='{r.os_version}'
DISTRIB_REVISION='{x['revision']}'
DISTRIB_TARGET='{x['target']}'
DISTRIB_ARCH='{x['pkg_arch']}'
DISTRIB_DESCRIPTION='OpenWrt {r.os_version} {x['revision']}'
DISTRIB_TAINTS='no-all'
""",
        "etc/openwrt_version": f"{x['revision']}\n",
        "etc/issue": f"OpenWrt {r.os_version}, {x['revision']} \\n \\l\n",
        "etc/issue.net": f"OpenWrt {r.os_version}\n",
        "etc/banner": "  _______                     ________        __\n |       |.-----.-----.-----.|  |  |  |.----.|  |_\n"
                      " |   -   ||  _  |  -__|     ||  |  |  ||   _||   _|\n |_______||   __|_____|__|__||________||__|  |____|\n"
                      "          |__| W I R E L E S S   F R E E D O M\n",
        "bin/busybox": _busybox(r),
    }
    passwd = """root:x:0:0:root:/root:/bin/ash
daemon:x:1:1:daemon:/var:/bin/false
ftp:x:55:55:ftp:/home/ftp:/bin/false
nobody:x:65534:65534:nobody:/var:/bin/false
"""
    group = "root:x:0:\ndaemon:x:1:\nadm:x:4:\nusers:x:100:\nnobody:x:65534:\n"
    shadow = f"root:$1$root$A3qH7I7U2vW0nLJ9rKaNl/:{root_day}:0:99999:7:::\n"
    return _device_content(persona, ident, files, nas=False, passwd=passwd, group=group, shadow=shadow)


def build_qnap(persona, ident):
    r = ident.release
    model = r.extra["model"]
    files = {
        "etc/os-release": f'NAME="QTS"\nID=qnap\nPRETTY_NAME="QNAP QTS {r.os_version}"\nVERSION_ID="{r.os_version_id}"\n',
        "etc/issue": "Welcome to QNAP Systems, Inc. \\n \\l\n",
        "etc/issue.net": "QNAP Systems, Inc.\n",
        "etc/motd": "QNAP NAS\n",
        "etc/version": f"QTS {r.os_version}\n",
        "etc/config/uLinux.conf": f"[System]\nModel = {model}\nVersion = {r.os_version}\n"
                                  f"Build Number = {r.extra['build']}\n",
        "etc/default_config/uLinux.conf": f"[System]\nModel = {model}\n",
    }
    return _device_content(persona, ident, files, nas=True, gecos="admin")


def build_synology(persona, ident):
    r = ident.release
    x = r.extra
    major, minor = r.os_version_id.split(".")
    files = {
        "etc/os-release": f'NAME="Synology DSM"\nID=synology\nPRETTY_NAME="Synology DSM {r.os_version_id}"\n'
                          f'VERSION_ID="{r.os_version_id}"\n',
        "etc/issue": "Synology DiskStation \\n \\l\n",
        "etc/issue.net": "Synology DiskStation\n",
        "etc/motd": "Synology DiskStation\n",
        "etc.defaults/VERSION": f"""majorversion="{major}"
minorversion="{minor}"
productversion="{r.os_version}"
buildphase="GM"
buildnumber="{x['build']}"
smallfixnumber="{x['smallfix']}"
builddate="{x['builddate']}"
""",
        "etc/VERSION": f"""majorversion="{major}"
minorversion="{minor}"
productversion="{r.os_version}"
buildnumber="{x['build']}"
""",
    }
    return _device_content(persona, ident, files, nas=True, gecos="admin")


def build_ubiquiti(persona, ident):
    r = ident.release
    files = {
        "etc/os-release": f'PRETTY_NAME="EdgeOS"\nNAME="EdgeOS"\nID=edgeos\nVERSION_ID="{r.os_version_id}"\n'
                          'HOME_URL="https://www.ui.com/"\n',
        "etc/issue": "EdgeOS \\n \\l\n",
        "etc/issue.net": "EdgeOS\n",
        "etc/motd": "Welcome to EdgeOS\n",
        "etc/version": f"{r.os_version}\n",
        "etc/ubnt/version": f"{r.extra['model']} {r.os_version}\n",
        "opt/vyatta/etc/config.boot": f"system {{\n    host-name {ident.hostname}\n    login {{\n"
                                      f"        user {persona.user} {{\n            level admin\n        }}\n"
                                      "    }\n}\n",
    }
    return _device_content(persona, ident, files, nas=False, gecos="Ubiquiti")
