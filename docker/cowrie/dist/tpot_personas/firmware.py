"""The programs of firmware images, as the images have them (no Debian tree): the TP-Link Archer C7 v5 and
the Zyxel NAS326.

The program folders (bin, sbin, usr/bin, usr/sbin: path -> link target or size) of the squashfs of its
US firmware 211210, 220715 and 260821 (www.tp-link.com/us/support/download/archer-c7/v5/) are in
firmware_images/archer-c7-v5.json; read with unsquashfs in a container, nothing of the images was run
on the host (its BusyBox only under qemu-mips-static, for its help and its builtins).
Those of the NAS326 V5.21(AAZF.18)C0 (download.zyxel.com/NAS326/firmware/) are in
firmware_images/nas326-v5.21.json: /bin and /sbin of the initramfs with /bin and /sbin of its system image
copied over them, /usr the system image (its /etc/init.d/rcS); the persona keeps the rest of Cowrie's
tree and its BusyBox rules (applet_links, the commands its BusyBox has not).
TPLINK_APPLETS: what `busybox` of its BusyBox v1.19.4 lists (the same in the three images).
TPLINK_ASH_BUILTINS: the builtin table of its ash (no history; help, printf and getopts are there)."""
import functools
import json
from pathlib import Path

IMAGES = Path(__file__).resolve().parent / "firmware_images"
TPLINK_APPLETS = (
    "[", "[[", "addgroup", "adduser", "arping", "ash", "awk", "basename", "cat", "chgrp", "chmod", "chown", "chroot",
    "clear", "cmp", "cp", "crond", "crontab", "cut", "date", "dd", "delgroup", "deluser", "dirname", "dmesg", "echo",
    "egrep", "env", "expr", "false", "fgrep", "find", "free", "fsync", "grep", "gunzip", "gzip", "halt", "head",
    "hexdump", "hostid", "id", "ifconfig", "init", "insmod", "kill", "killall", "klogd", "ln", "lock", "logger", "ls",
    "lsmod", "mac_addr", "md5sum", "mkdir", "mkfifo", "mknod", "mktemp", "mount", "mv", "nice", "passwd", "pgrep",
    "pidof", "ping", "ping6", "pivot_root", "poweroff", "printf", "ps", "pwd", "readlink", "reboot", "reset", "rm",
    "rmdir", "rmmod", "route", "sed", "seq", "sh", "sleep", "sort", "start-stop-daemon", "strings", "switch_root",
    "sync", "sysctl", "tail", "tar", "tee", "telnet", "test", "tftp", "time", "top", "touch", "tr", "traceroute",
    "true", "udhcpc", "umount", "uname", "uniq", "uptime", "vconfig", "vi", "watchdog", "wc", "wget", "which",
    "xargs", "yes", "zcat")
TPLINK_ASH_BUILTINS = (".", ":", "[", "[[", "alias", "bg", "break", "cd", "chdir", "command", "continue", "echo",
                       "eval", "exec", "exit", "export", "false", "fg", "getopts", "hash", "help", "jobs", "kill",
                       "let", "local", "printf", "pwd", "read", "readonly", "return", "set", "shift", "source", "test",
                       "times", "trap", "true", "type", "ulimit", "umask", "unalias", "unset", "wait")
# the PATH of the image's /etc/profile (every user) and the shell of root in its /etc/passwd
TPLINK_PATH = "/bin:/sbin:/usr/bin:/usr/sbin"
TPLINK_SHELL = "/bin/ash"
# the folders a firmware table replaces
PROGRAM_FOLDERS = ("bin", "sbin", "usr/bin", "usr/sbin")
# the programs of the images the persona leaves out on purpose: the opkg of OpenWrt 12.09 (the images
#   have no package database, its feed is that of 12.09-rc1; Cowrie's opkg answers with the packages
#   of a later OpenWrt, so the persona has none, as if the vendor had left it out)
LEFT_OUT = ("bin/opkg",)
# the PATH of /etc/profile of the NAS326's initramfs (every login; BASE_PATH is /firmware, the value of its
#   /etc/settings/firmware_info_path): its folders are those of its firmware table, /usr/local/zy-pkgs/bin is an
#   empty folder of the persona (system.directories; the package folder of the NAS326 is not in the image)
ZYXEL_PATH = ("/bin:/sbin:/usr/bin:/usr/sbin:/usr/local/bin:/usr/local/sbin:/firmware/sbin:/usr/local/zy-pkgs/bin:"
              "/firmware/htp")
# the folders at the root of the images (the same in the three); home stays besides them, Cowrie makes
#   the home of a login that /etc/passwd does not name under it
TPLINK_ROOT_DIRS = ("bin", "dev", "etc", "lib", "mnt", "overlay", "proc", "rom", "root", "sbin", "sys", "tmp",
                    "usr", "var", "www")
ROOT_KEEP = ("home",)


# the table of the program folders of each family with the folders of its firmware: the Archer C7 per
#   release (releases), the NAS326 one table (programs) with what a release has else (releases)
FIRMWARE_TABLES = {"tplink": "archer-c7-v5.json", "zyxel": "nas326-v5.21.json"}


@functools.cache
def _tables(family):
    return json.loads((IMAGES / FIRMWARE_TABLES[family]).read_text(encoding="utf-8"))


def has_programs(persona):
    """Whether the persona has the program folders of its firmware image (programs)."""
    return persona.kind == "device" and persona.family in FIRMWARE_TABLES


def is_image(persona):
    """Whether the persona is its firmware image throughout (the Archer C7: the folders at the root of the
    image, the builtins and the PATH of its ash, its usage texts) rather than its program folders only."""
    return persona.kind == "device" and persona.family == "tplink"


def programs(persona, ident=None):
    """{path: link target or size} of the program folders of the persona's firmware (of the release of
    ident; without it the folders of every release), None where the persona keeps Cowrie's tree."""
    if not has_programs(persona):
        return None
    data = _tables(persona.family)
    merged = dict(data.get("programs", {}))
    for label, table in data["releases"].items():
        if ident is None or label == ident.release.label:
            merged.update(table)
    for rel in LEFT_OUT:
        merged.pop(rel, None)
    return merged


def root_dirs(persona):
    """The folders at the root of a firmware image (the others of Cowrie's tree go, but ROOT_KEEP); none
    where only the program folders are the image's (Cowrie's root stays)."""
    return TPLINK_ROOT_DIRS if is_image(persona) else ()


def login_env(persona):
    """{login_path, login_shell} of the persona's login ([shell] keys of the patched sessions), empty
    where upstream's PATH and SHELL stay: the PATH and shell of the Archer C7, the PATH of the NAS326
    (ZYXEL_PATH, so which and a bare name find the same program of its folders)."""
    if is_image(persona):
        return {"login_path": TPLINK_PATH, "login_shell": TPLINK_SHELL}
    if has_programs(persona) and persona.family == "zyxel":
        return {"login_path": ZYXEL_PATH}
    return {}


# the shell links of the BusyBox devices that keep Cowrie's merged tree, and the shells of Debian they have
#   not (with bash its bashbug): the DIR-859 1.05B03 image (sh -> busybox; no ash, bash or dash), the OpenWrt
#   images (sh and ash -> busybox, no bash) and the NAS326 V5.21(AAZF.14)C0 / (AAZF.18)C0 images (sh and
#   ash -> busybox in the initramfs, no bash in it or the system image); QTS has bash (QNAP's bash updates of
#   QTS 4.1, not read from a QTS 5.2 image); the camera sh -> busybox and no bash (the shell of a Hikvision
#   camera is the BusyBox sh); unverified for QTS and the camera (no dash; no image of the camera read)
_NO_BASH = ("bash", "rbash", "bashbug", "dash")
SHELL_LINKS = {"dlink": ({"sh": "busybox"}, _NO_BASH),
               "openwrt": ({"sh": "busybox", "ash": "busybox"}, _NO_BASH),
               "zyxel": ({"sh": "busybox", "ash": "busybox"}, _NO_BASH),
               "qnap": ({"sh": "busybox"}, ("dash",)), "ipcam": ({"sh": "busybox"}, _NO_BASH)}


# the names in /etc of Cowrie's Debian tree that a router's firmware has too: the rest of Cowrie's /etc goes
#   (render.adapt_system, validate._check_firmware_etc), the persona's own files (accounts, host name, release
#   files, /etc/config) are written after it
#   DIR-859 1.05B03 (its squashfs): /etc holds ath, config, defnodes, events, init.d, init0.d, l7-protocols,
#   scripts, services, templates, openssl.cnf, the stunnel files and the links TZ, hosts, iproute2, ppp and
#   resolv.conf into /var; no passwd, group, shadow, profile or shells (the persona keeps its accounts, the
#   logins of Cowrie need them)
#   Archer C7 v5: the accounts and /etc/profile of the images (TPLINK_PATH); unverified: hosts and resolv.conf
#   (the base-files of the OpenWrt it is built on have them; the /etc of the images was not listed)
#   init.d on both: an empty folder of Cowrie's tree, as both firmwares have an /etc/init.d (the DIR-859 squashfs;
#   on the C7 the init scripts of its daemons, devices.build_tplink); their scripts are not part of the persona
ETC_NAMES = {"dlink": ("hosts", "init.d", "resolv.conf"),
             "tplink": ("group", "hosts", "init.d", "passwd", "profile", "resolv.conf", "shadow")}


def etc_names(persona):
    """The names of Cowrie's /etc the persona's firmware has too (ETC_NAMES), None where Cowrie's /etc stays."""
    return ETC_NAMES.get(persona.family) if persona.kind == "device" else None


def shell_links(persona):
    """({name: target} in usr/bin, names of usr/bin to remove) of the persona's shells. With the program
    folders of its firmware the shells are its own (/bin/sh and /bin/ash of the NAS326's initramfs), only
    the names of the shells it has not stay (the commands of Cowrie that go)."""
    links, gone = SHELL_LINKS.get(persona.family, ({}, ()))
    return ({}, gone) if has_programs(persona) else (links, gone)


def usage_table(persona):
    """The file of the recorded usage of uname, head, grep and which of the persona's BusyBox
    (firmware_images/archer-c7-v5-usage.json), None where Cowrie's answers stay."""
    return IMAGES / "archer-c7-v5-usage.json" if is_image(persona) else None


def builtins(persona):
    """The builtins of the shell of such a firmware ([shell] builtins), None elsewhere."""
    return TPLINK_ASH_BUILTINS if is_image(persona) else None


# where BusyBox installs its applets (busybox --list-full of busybox:1.36.1); the TP-Link images and
#   the OpenWrt images have their applets in the same folders
BUSYBOX_DIRS = {
    "bin": (
        "arch ash base32 base64 cat chattr chgrp chmod chown conspy cp cpio cttyhack date dd df dmesg "
        "dnsdomainname dumpkmap echo ed egrep false fatattr fdflush fgrep fsync getopt grep gunzip gzip "
        "hostname hush ionice iostat ipcalc kbd_mode kill link linux32 linux64 ln login ls lsattr lzop "
        "makemime mkdir mknod mktemp more mount mountpoint mpstat mt mv netstat nice pidof ping ping6 "
        "pipe_progress printenv ps pwd reformime resume rev rm rmdir rpm run-parts scriptreplay sed setarch "
        "setpriv setserial sh sleep stat stty su sync tar touch true umount uname usleep vi watch zcat "
    ),
    "sbin": (
        "acpid adjtimex arp blkid blockdev bootchartd depmod devmem fbsplash fdisk findfs freeramdisk fsck "
        "fsck.minix fstrim getty halt hdparm hwclock ifconfig ifdown ifenslave ifup init inotifyd insmod ip "
        "ipaddr iplink ipneigh iproute iprule iptunnel klogd loadkmap logread losetup lsmod makedevs mdev "
        "mkdosfs mke2fs mkfs.ext2 mkfs.minix mkfs.vfat mkswap modinfo modprobe nameif pivot_root poweroff "
        "raidautorun reboot rmmod route run-init runlevel setconsole slattach start-stop-daemon sulogin "
        "swapoff swapon switch_root sysctl syslogd tc tunctl udhcpc uevent vconfig watchdog zcip "
    ),
    "usr/bin": (
        "[ [[ ar ascii awk basename bc beep blkdiscard bunzip2 bzcat bzip2 cal chpst chrt chvt cksum clear "
        "cmp comm crc32 crontab cryptpw cut dc deallocvt diff dirname dos2unix dpkg dpkg-deb du dumpleases "
        "eject env envdir envuidgid expand expr factor fallocate fgconsole find flock fold free ftpget ftpput "
        "fuser groups hd head hexdump hexedit hostid id install ipcrm ipcs killall last less logger logname "
        "lpq lpr lsof lspci lsscsi lsusb lzcat lzma man md5sum mesg microcom mkfifo mkpasswd nc nl nmeter "
        "nohup nproc nsenter nslookup od openvt passwd paste patch pgrep pkill pmap printf pscan pstree pwdx "
        "readlink realpath renice reset resize rpm2cpio runsv runsvdir rx script seq setfattr setkeycodes "
        "setsid setuidgid sha1sum sha256sum sha3sum sha512sum showkey shred shuf smemcap softlimit sort split "
        "ssl_client strings sum sv svc svok tac tail taskset tcpsvd tee telnet test tftp time timeout top tr "
        "traceroute traceroute6 tree truncate ts tsort tty ttysize udhcpc6 udpsvd unexpand uniq unix2dos "
        "unlink unlzma unshare unxz unzip uptime users uudecode uuencode vlock volname w wall wc wget which "
        "who whoami whois xargs xxd xz xzcat yes "
    ),
    "usr/sbin": (
        "add-shell addgroup adduser arping brctl chat chpasswd chroot crond delgroup deluser dhcprelay dnsd "
        "ether-wake fakeidentd fbset fdformat fsfreeze ftpd httpd i2cdetect i2cdump i2cget i2cset i2ctransfer "
        "ifplugd inetd killall5 loadfont lpd mim nanddump nandwrite nbd-client nologin ntpd partprobe "
        "popmaildir powertop rdate rdev readahead readprofile remove-shell rtcwake seedrng sendmail setfont "
        "setlogcons svlogd telnetd tftpd ubiattach ubidetach ubimkvol ubirename ubirmvol ubirsvol "
        "ubiupdatevol udhcpd "
    ),
}
# /bin and /sbin of Debian 9 (debian/eol:stretch): EdgeOS 2.0 has the folders of its userland
DEBIAN9_DIRS = {
    "bin": (
        "bash cat chgrp chmod chown cp dash date dd df dir dmesg dnsdomainname domainname echo egrep false "
        "fgrep findmnt grep gunzip gzexe gzip hostname ip ln login ls lsblk mkdir mknod mktemp more mount "
        "mountpoint mv nisdomainname pidof ping ping4 ping6 pwd rbash readlink rm rmdir run-parts sed sh "
        "sh.distrib sleep ss stty su sync tailf tar tempfile touch true umount uname uncompress vdir wdctl "
        "which ypdomainname zcat zcmp zdiff zegrep zfgrep zforce zgrep zless zmore znew "
    ),
    "sbin": (
        "agetty badblocks blkdiscard blkid blockdev bridge cfdisk chcpu ctrlaltdel debugfs devlink dumpe2fs "
        "e2fsck e2image e2label e2undo fdisk findfs fsck fsck.cramfs fsck.ext2 fsck.ext3 fsck.ext4 fsck.minix "
        "fsfreeze fstab-decode fstrim getty hwclock installkernel ip isosize killall5 ldconfig logsave "
        "losetup mke2fs mkfs mkfs.bfs mkfs.cramfs mkfs.ext2 mkfs.ext3 mkfs.ext4 mkfs.minix mkhomedir_helper "
        "mkswap pam_tally pam_tally2 pivot_root raw resize2fs rtacct rtmon runuser sfdisk shadowconfig "
        "start-stop-daemon sulogin swaplabel swapoff swapon switch_root tc tipc tune2fs unix_chkpwd "
        "unix_update wipefs zramctl "
    ),
}

def which_table(persona, applets):
    """The table of [shell] which_dirs (program_dirs) where Cowrie's tree keeps its merged folders; None
    with the program folders of a firmware (the NAS326), whose folders are its own: which looks through
    the login's PATH there, as a bare name does (which -a pgrep: /bin/pgrep and /usr/sbin/pgrep)."""
    return None if has_programs(persona) else program_dirs(persona, applets)


def program_dirs(persona, applets):
    """{program: (folder, ...)} of a device whose /bin, /sbin, /usr/bin and /usr/sbin are folders of their own
    while Cowrie's tree has them merged ([shell] which_dirs): the applets of its BusyBox where BusyBox
    installs them, EdgeOS the /bin and /sbin of Debian 9; with the program folders of its firmware
    (the NAS326) the applets where its firmware has their links (every applet in /bin or /sbin of its
    initramfs). None for a merged system or the Archer C7 (the folders of its image, no table)."""
    if persona.kind != "device" or is_image(persona) or persona.family == "synology":
        return None
    if has_programs(persona):
        where = {}
        for rel, target in sorted(programs(persona).items()):
            folder, name = rel.rsplit("/", 1)
            if folder in PROGRAM_FOLDERS and isinstance(target, str) and target.endswith("busybox"):
                where.setdefault(name, []).append(folder)
        return {name: tuple(where[name]) for name in applets if name in where}
    table = DEBIAN9_DIRS if persona.family == "ubiquiti" else BUSYBOX_DIRS
    where = {}
    for folder, names in table.items():
        for name in "".join(names).split():
            where.setdefault(name, []).append(folder)
    if persona.family == "ubiquiti":
        return {name: tuple(folders) for name, folders in where.items()}
    return {name: tuple(where[name]) for name in applets if name in where}
