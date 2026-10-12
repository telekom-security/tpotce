"""Consistency of a rendered persona: the parts of a system that describe the same thing must agree.

problems() returns one string per finding, each starting with the word of its
rule (kernel, hostname, nproc, START, forbidden, ...); validate() raises."""
import calendar
import configparser
import datetime
import fnmatch
import json
import posixpath
import re
import time
from pathlib import Path

from . import PersonaError, servers, system, tree
from .common import VM_PROVIDERS, home_dir, is_login_shell, ps_start, ps_user
from .identity import (DAY, drawn_at, host_patterns, hostname_regex, is_fresh, kernel_time, max_uptime, stale_at,
                       updated_at)
from .model import PROMPT_STYLES
from .render import BUSYBOX_HELP, DIR_MODES, PACKAGE_MANAGER_PATHS, runtime_json
from .ssh_profiles import (COMPRESSIONS, HOST_KEY_ALGORITHMS, HOST_KEYS, PROFILES, TWISTED_CIPHERS, TWISTED_MACS,
                           host_key_algorithms, key_type, server_version)

# a rendered device tree is about 1 MB (a server's more); far below it the render broke
MIN_PICKLE_SIZE = 800_000
PATH_DIRS = ("usr/local/sbin", "usr/local/bin", "usr/sbin", "usr/bin", "sbin", "bin")
PYTHON_PACKAGE_MANAGERS = ("apt", "apt-get", "yum", "dnf", "rpm")
FORBIDDEN_MARKERS = ("phil", "2.6.26-2-686", "2.6.26-19lenny", "com/ubuntu/upstart", "dannf@debian.org",
                     "svr04", "Wed Nov 4 20:45:37")
UBUNTU_MARKERS = ("ubuntu 22.04", "ubuntu 24.04", "id=ubuntu", "jammy jellyfish", "noble numbat")
REQUIRED = ("fs.pickle", "honeyfs", "txtcmds", "cmdoutput.json", "cowrie.cfg", "identity.json")
# the keys Cowrie's free formats (src/cowrie/commands/free.py, FREE_OUTPUT)
MEMINFO_KEYS = ("MemTotal", "MemFree", "MemAvailable", "Buffers", "Cached", "Shmem", "SwapTotal", "SwapFree")
PS_KEYS = ("USER", "PID", "CPU", "MEM", "VSZ", "RSS", "TTY", "STAT", "START", "TIME", "COMMAND")
_ISO_TIME = re.compile(r"(\d{4})-(\d{2})-(\d{2})[ T]+(\d{2}):(\d{2}):(\d{2})")
# yum.log: "Jun 04 10:22:33 Updated: openssh-server-7.4p1-23.el7_9.x86_64", no year
_YUM_LINE = re.compile(r"^([A-Z][a-z]{2}) (\d{2}) (\d{2}):(\d{2}):(\d{2}) (\w+): (\S+)$", re.M)


def _text(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _utc_date(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).date()


def _check_cfg(persona, ident, cfg, runtime_dir, keys_dir, out):
    r = ident.release
    expected = {
        ("honeypot", "hostname"): ident.hostname,
        ("honeypot", "boot_offset"): str(ident.boot_offset),
        ("honeypot", "contents_path"): f"{runtime_dir}/honeyfs",
        ("honeypot", "txtcmds_path"): f"{runtime_dir}/txtcmds",
        ("honeypot", "etc_path"): str(keys_dir),
        ("shell", "filesystem"): f"{runtime_dir}/fs.pickle",
        ("shell", "processes"): f"{runtime_dir}/cmdoutput.json",
        ("shell", "kernel_version"): r.kernel_version,
        ("shell", "kernel_build_string"): r.kernel_build,
        ("shell", "arch"): persona.arch,
        ("shell", "hardware_platform"): persona.hardware_platform,
        ("shell", "prompt_style"): persona.prompt_style,
        ("shell", "shell_name"): system.shell_style(persona)[0],
        ("shell", "not_found_style"): system.shell_style(persona)[1],
        ("ssh", "version"): r.ssh_banner,
    }
    if persona.prompt_style not in PROMPT_STYLES:
        out.append(f"prompt_style {persona.prompt_style!r} is not one of {', '.join(PROMPT_STYLES)}")
    for (section, key), value in expected.items():
        got = cfg.get(section, key, fallback=None)
        if got != value:
            out.append(f"cfg {key}: {got!r}, expected {value!r}")
    if cfg.has_section("ssh"):
        for key, value in cfg.items("ssh"):
            if key.endswith(("_public_key", "_private_key")):
                if not value.startswith(f"{keys_dir}/"):
                    out.append(f"cfg {key}: {value!r} is not under {keys_dir}")
                if key.startswith("dsa_"):
                    out.append(f"host key not supported: {key}")


def _check_ssh(persona, ident, cfg, out):
    profile = PROFILES[persona.ssh_profile]
    lists = (("cipher", "ciphers", TWISTED_CIPHERS, profile.ciphers),
             ("mac", "macs", TWISTED_MACS, profile.macs),
             ("compression", "compression", COMPRESSIONS, profile.compression),
             ("host key", "public_key_auth", HOST_KEYS, profile.host_keys))
    for word, key, allowed, wanted in lists:
        entries = tuple(e.strip() for e in cfg.get("ssh", key, fallback="").split(",") if e.strip())
        for entry in entries:
            if entry not in allowed:
                out.append(f"{word} not supported: {entry}")
        if entries != tuple(wanted):
            out.append(f"{word} list {','.join(entries)} differs from profile {profile.name}")

    _check_host_key_algorithms(profile, ident.release, cfg, out)
    banner = ident.release.ssh_banner
    ssh_version = cfg.get("shell", "ssh_version", fallback="")
    product = banner[len("SSH-2.0-"):].split(" ", 1)[0] if banner.startswith("SSH-2.0-") else ""
    if product.lower() == "dropbear":
        # OpenWrt builds dropbear without the version in its banner
        if "dropbear" not in ssh_version.lower():
            out.append(f"ssh_version {ssh_version!r} does not name dropbear of the banner")
    elif product.lower().startswith("dropbear_"):
        version = product.split("_", 1)[1]
        if "dropbear" not in ssh_version.lower() or not re.search(rf"(^|\s){re.escape(version)}(\s|,|$)", ssh_version):
            out.append(f"ssh_version {ssh_version!r} does not name {product} of the banner")
    elif product.startswith("OpenSSH_"):
        # RHEL / CentOS send OpenSSH_8.7 while ssh -V says OpenSSH_8.7p1
        if not re.match(rf"{re.escape(product)}(p\d+)?(,|\s|$)", ssh_version):
            out.append(f"ssh_version {ssh_version!r} does not name {product} of the banner")
    else:
        out.append(f"ssh_version: banner {banner!r} names no known product")


def _check_host_key_algorithms(profile, release, cfg, out):
    """[ssh] host_key_algorithms: what the server of the banner offers for the loaded keys. Each one
    signs with a key of public_key_auth, every key is offered; OpenSSH 8.8 and later offer no
    ssh-rsa, OpenSSH before 7.2 and dropbear before 2020.79 no rsa-sha2-*, dropbear no
    rsa-sha2-512, OpenSSH 7.2 and later both rsa-sha2-512 and rsa-sha2-256 (unless its policy has
    no SHA-1, also ssh-rsa)."""
    names = tuple(e.strip() for e in cfg.get("ssh", "host_key_algorithms", fallback="").split(",") if e.strip())
    keys = {e.strip() for e in cfg.get("ssh", "public_key_auth", fallback="").split(",") if e.strip()}
    for name in names:
        if name not in HOST_KEY_ALGORITHMS:
            out.append(f"host key algorithm not supported: {name}")
        elif key_type(name) not in keys:
            out.append(f"host key algorithm {name} has no {key_type(name)} key")
    for key in sorted(keys - {key_type(name) for name in names}):
        out.append(f"host key {key} is not offered in host_key_algorithms")
    try:
        product, version = server_version(release)
    except ValueError:
        return
    rsa = [name for name in names if key_type(name) == "ssh-rsa"]
    sha2 = {"rsa-sha2-512", "rsa-sha2-256"}
    if product == "openssh" and version >= (8, 8) and "ssh-rsa" in rsa:
        out.append(f"host key algorithms: OpenSSH {version[0]}.{version[1]} offers no ssh-rsa")
    if ((product == "openssh" and version < (7, 2)) or (product == "dropbear" and version < (2020, 79))) \
            and sha2 & set(rsa):
        out.append(f"host key algorithms: {product} {version[0]}.{version[1]} has no rsa-sha2")
    if product == "dropbear" and "rsa-sha2-512" in rsa:
        out.append("host key algorithms: dropbear has no rsa-sha2-512")
    if product == "openssh" and version >= (7, 2) and rsa and not sha2 <= set(rsa):
        out.append(f"host key algorithms: OpenSSH {version[0]}.{version[1]} offers rsa-sha2-512 and rsa-sha2-256")
    if product == "openssh" and version < (8, 8) and rsa and "ssh-rsa" not in rsa \
            and release.extra.get("ssh_rsa_sha1", True):
        out.append(f"host key algorithms: OpenSSH {version[0]}.{version[1]} still offers ssh-rsa")
    if names != host_key_algorithms(profile, release):
        out.append(f"host key algorithms {','.join(names)} differ from {profile.name} of {release.ssh_banner}")


def _check_top(persona, cfg, txtcmds, out):
    """The patched top answers (it reads /proc/meminfo, the ps list and these keys): no static
    top, a style the persona's system has, procps-ng's version, a plausible count of kernel
    threads and base load."""
    if (txtcmds / "usr/bin/top").exists():
        out.append("top: a static txtcmd usr/bin/top shadows the live top")
    style = cfg.get("shell", "top_style", fallback="")
    if style not in ("procps", "busybox") or (style == "busybox" and persona.kind != "device"):
        out.append(f"top_style {style!r} does not fit {persona.kind} {persona.id}")
    version = cfg.get("shell", "procps_version", fallback="")
    if style == "procps" and not re.fullmatch(r"\d+\.\d+\.\d+", version):
        out.append(f"procps_version {version!r} is no procps-ng version")
    try:
        threads = int(cfg.get("shell", "kernel_threads", fallback=""))
        base = float(cfg.get("shell", "load_base", fallback=""))
    except ValueError:
        out.append("top: kernel_threads or load_base is no number")
        return
    if not 20 <= threads <= 600:
        out.append(f"top: {threads} kernel threads")
    if not 0.0 <= base <= 1.0:
        out.append(f"top: load_base {base} of an idle system")


def _check_hardware(persona, ident, honeyfs, txtcmds, out):
    hw = ident.hardware
    cpuinfo = _text(honeyfs / "proc/cpuinfo")
    if len(re.findall(r"^processor\s*:", cpuinfo, re.M)) != hw.cpus:
        out.append(f"cpuinfo: processor count is not {hw.cpus}")
    # what a count with grep sees: lines with the lowercase word, the model name included
    counted = sum("processor" in line for line in cpuinfo.splitlines())
    if counted != hw.cpus:
        out.append(f"cpuinfo: grep -c processor gives {counted}, not {hw.cpus}")
    if persona.hardware_platform == "aarch64":
        # arm64 cpuinfo has no model name; the Pi firmware names the board
        if not re.search(rf"^Model\s*: {re.escape(hw.board)}$", cpuinfo, re.M):
            out.append(f"cpuinfo: no Model {hw.board!r}")
    elif hw.cpu_model not in cpuinfo:
        out.append(f"cpuinfo: no {hw.cpu_model!r}")
    if _text(txtcmds / "usr/bin/nproc").strip() != str(hw.cpus):
        out.append(f"nproc is not {hw.cpus}")
    lscpu = _text(txtcmds / "usr/bin/lscpu")
    match = re.search(r"^CPU\(s\):\s+(\d+)", lscpu, re.M)
    if not match or int(match.group(1)) != hw.cpus:
        out.append(f"lscpu CPU(s) is not {hw.cpus}")
    if hw.cpu_model not in lscpu:
        out.append(f"lscpu: no {hw.cpu_model!r}")

    meminfo = _text(honeyfs / "proc/meminfo")
    lacking = [key for key in MEMINFO_KEYS if not re.search(rf"^{key}:\s+\d+ kB$", meminfo, re.M)]
    if lacking:
        out.append(f"meminfo lacks {' '.join(lacking)}")
    match = re.search(r"^MemTotal:\s+(\d+) kB", meminfo, re.M)
    if not match or int(match.group(1)) != hw.mem_kb:
        out.append(f"meminfo MemTotal is not {hw.mem_kb} kB")

    flags = re.findall(r"^flags\s*: (.*)$", cpuinfo, re.M)
    if any("hypervisor" in line.split() for line in flags) and hw.provider not in VM_PROVIDERS:
        out.append(f"cpuinfo: hypervisor flag on bare metal ({hw.provider or 'device'})")
    # the delay loop of dmesg is what cpuinfo shows per CPU (MIPS / ARM; x86 shows 2x MHz)
    calibrated = re.search(r"([\d.]+) BogoMIPS \(lpj=", _text(txtcmds / "bin/dmesg"))
    shown = set(re.findall(r"^(?:BogoMIPS|bogomips)\s*: ([\d.]+)$", cpuinfo, re.M))
    if calibrated and shown and shown != {calibrated.group(1)}:
        out.append(f"BogoMIPS {calibrated.group(1)} of dmesg is not the {' '.join(sorted(shown))} of cpuinfo")


def _check_processes(persona, ident, path, out):
    try:
        processes = json.loads(_text(path))["command"]["ps"]
    except (ValueError, KeyError, TypeError):
        out.append("processes: cmdoutput.json is no ps list")
        return
    if not processes:
        out.append("processes: ps list is empty")
        return
    if any(set(PS_KEYS) - set(p) for p in processes):
        out.append("processes: an entry lacks one of " + " ".join(PS_KEYS))
        return
    if ps_user(persona.user) not in {p["USER"] for p in processes}:
        out.append(f"processes: none of {persona.user}")
    boot = ps_start(ident.booted_at, ident.now)
    for p in processes:
        if is_login_shell(p):
            if not _start_between(p["START"], ident.booted_at, ident.now):
                out.append(f"START {p['START']!r} of {p['COMMAND']!r} is not between boot and now")
        elif p["START"] != boot:
            out.append(f"START {p['START']!r} of {p['COMMAND']!r} is not the boot {boot}")
    pids = [p["PID"] for p in processes]
    if pids[0] != 1:
        out.append(f"PID of {processes[0]['COMMAND']!r} is {pids[0]}, not 1")
    if len(set(pids)) != len(pids):
        out.append("PID used twice in ps")


def _check_clock(persona, ident, out):
    """Installed before the boot, booted before now; a fresh draw boots one day
    to max_uptime ago, a restored identity (COWRIE_PERSONA_ROTATION) keeps its
    boot, so its uptime may have grown beyond that."""
    if ident.booted_at > ident.now:
        out.append("uptime: booted after now")
    if ident.installed_at > ident.booted_at:
        out.append("uptime: installed after the boot")
    if is_fresh(ident) and not DAY <= ident.boot_offset <= max_uptime(persona):
        out.append(f"uptime: {ident.boot_offset // DAY} days, a fresh draw has 1 to {max_uptime(persona) // DAY}")


def _start_between(start, low, now):
    """A procps START (HH:MM, MonDD or the year) of a process started between low and now."""
    if re.fullmatch(r"\d{2}:\d{2}", start):
        today = now // DAY * DAY
        minute = today + int(start[:2]) * 3600 + int(start[3:]) * 60
        return max(low, today) - 59 <= minute <= now
    days = range(low // DAY * DAY, now + 1, DAY)
    return any(ps_start(max(low, day), now) == start for day in days)


def _check_tree(persona, ident, fs, out):
    low, high = ident.installed_at - 31 * DAY, ident.now
    nodes = [("/", fs)] + list(tree.iter_nodes(fs))
    late = [path for path, node in nodes if not low <= (tree.node_get(node, tree.A_CTIME) or 0) <= high]
    if late:
        out.append(f"ctime outside installation..now: {', '.join(late[:5])}")
    node = tree.find_node(fs, "etc/hostname")
    if node is None or tree.node_get(node, tree.A_CONTENTS) != f"{ident.hostname}\n".encode():
        out.append("hostname: etc/hostname of fs.pickle differs")
    wanted = {rel: (0, 0, mode) for rel, mode in DIR_MODES.items()}
    home = home_dir(persona)
    wanted[home] = (persona.uid, persona.gid, 0o700 if home == "root" else None)
    for rel, (uid, gid, mode) in wanted.items():
        node = tree.find_node(fs, rel)
        if node is None:
            out.append(f"owner: /{rel} missing in fs.pickle")
            continue
        got = (tree.node_get(node, tree.A_UID), tree.node_get(node, tree.A_GID),
               (tree.node_get(node, tree.A_MODE) or 0) & 0o7777)
        if got[:2] != (uid, gid) or (mode is not None and got[2] != mode):
            out.append(f"owner of /{rel} is {got[0]}:{got[1]} {got[2]:o}, expected {uid}:{gid}"
                       + (f" {mode:o}" if mode is not None else ""))


def _iso_ts(match):
    return calendar.timegm(tuple(int(g) for g in match.groups()))


def _log_events(ident, honeyfs):
    """(path, time, text) of every dated entry of the logs under var/log: apt
    history blocks (by End-Date), dnf.log lines and yum.log lines (the year
    from the rotation name yum.log-<year>0101, else that of the draw: the
    history of a kept identity ends there)."""
    events = []
    for path in sorted((honeyfs / "var/log").rglob("*")):
        if not path.is_file():
            continue
        rel, text = str(path.relative_to(honeyfs)), _text(path)
        if path.name.startswith("yum.log"):
            rotated = re.search(r"-(\d{4})0101$", path.name)
            year = int(rotated.group(1)) - 1 if rotated else time.gmtime(drawn_at(ident)).tm_year
            for mon, day, hh, mm, ss, verb, name in _YUM_LINE.findall(text):
                stamp = calendar.timegm(time.strptime(f"{year} {mon} {day} {hh}:{mm}:{ss}", "%Y %b %d %H:%M:%S"))
                events.append((rel, stamp, f"{verb}: {name}"))
        elif path.name == "history.log":
            for block in text.split("\n\n"):
                stamps = [_iso_ts(m) for m in _ISO_TIME.finditer(block)]
                if stamps:
                    events.append((rel, max(stamps), block))
        else:
            for line in text.splitlines():
                match = _ISO_TIME.search(line)
                if match:
                    events.append((rel, _iso_ts(match), line))
    return events


def _restarts_sshd(text):
    return "openssh-server" in text and re.search(r"(^|\n|\s)(Upgrade|Upgraded|Updated):", text) is not None


def _check_logs(ident, honeyfs, fs, out):
    events = _log_events(ident, honeyfs)
    first, last = _utc_date(ident.installed_at), _utc_date(ident.now)
    updated, stale = updated_at(ident.release), stale_at(ident.release)
    newest = {}
    for rel, stamp, text in events:
        newest[rel] = max(newest.get(rel, stamp), stamp)
        # the logged versions are those of the release: current from its update until they went stale
        when = time.strftime("%Y-%m-%d", time.gmtime(stamp))
        if stamp < updated:
            out.append(f"date: {rel} logs versions on {when}, before the update of the release")
        if stale and stamp > stale:
            out.append(f"date: {rel} logs versions on {when}, after they went stale")
        if rel.rsplit("/", 1)[-1].startswith("yum.log") and not first <= _utc_date(stamp) <= last:
            out.append(f"date {_utc_date(stamp)} in {rel} is not between {first} and {last}")
        # a real upgrade restarts sshd, but ps shows the sshd of the boot
        if _restarts_sshd(text) and stamp > ident.booted_at:
            out.append(f"sshd: openssh upgrade in {rel} at {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(stamp))} "
                       "is after the boot")
    if fs is None:
        return
    shadow_days = [int(f[2]) for f in (line.split(":") for line in _text(honeyfs / "etc/shadow").splitlines())
                   if len(f) > 2 and f[2].isdigit()]
    if shadow_days:
        newest["etc/shadow"] = max(shadow_days) * DAY
    for rel, stamp in sorted(newest.items()):
        node = tree.find_node(fs, rel)
        if node is not None and (tree.node_get(node, tree.A_CTIME) or 0) < stamp:
            out.append(f"ctime of /{rel} is before its newest entry")


def _root_mount(text):
    match = re.search(r"^(\S+) on / type ", text, re.M)
    return match.group(1) if match else None


def _check_boot(honeyfs, txtcmds, out):
    """The root= of the kernel command line in dmesg names the mounted root: by
    device, by the / entry of fstab, or (no initramfs, /dev/root) by PARTUUID.
    On a Pi the kernel gets cmdline.txt. Personas without fstab are not checked."""
    fstab = _text(honeyfs / "etc/fstab")
    if not fstab:
        return
    match = re.search(r"Command line: (.*)$", _text(txtcmds / "bin/dmesg"), re.M)
    cmdline = match.group(1).strip() if match else ""
    pi = _text(honeyfs / "boot/firmware/cmdline.txt").strip()
    if pi and cmdline != pi:
        out.append("boot: the kernel command line of dmesg is not boot/firmware/cmdline.txt")
    root = next((t[len("root="):] for t in cmdline.split() if t.startswith("root=")), None)
    spec = next((f[0] for f in (line.split() for line in fstab.splitlines() if not line.startswith("#"))
                 if len(f) > 1 and f[1] == "/"), None)
    mounted = _root_mount(_text(txtcmds / "bin/mount"))
    if root is None:
        out.append("boot: the kernel command line of dmesg has no root=")
    elif root.startswith("/dev/"):
        if root != mounted:
            out.append(f"boot: root={root} of dmesg, but / is mounted from {mounted}")
    elif mounted == "/dev/root":
        if not root.startswith("PARTUUID="):
            out.append(f"boot: / is /dev/root (no initramfs), but dmesg has root={root}")
    elif root != spec:
        out.append(f"boot: root={root} of dmesg is not the / entry {spec} of fstab")


_MOUNT = re.compile(r"^(\S+) on (\S+) type (\S+) ", re.M)
_VFS = re.compile(r"VFS: Mounted root \((\S+) filesystem\)(?: readonly)? on device (\d+):(\d+)\.")


def _check_device_boot(honeyfs, txtcmds, out):
    """Devices (no fstab): the kernel mounted its root as dmesg says, and mount shows
    it: the file system type of "VFS: Mounted root" on /dev/root or the root= device
    (/, /rom under an overlay or a mount point of the vendor), an mtdblock root on
    MTD major 31 and its minor; an initramfs (no VFS line) has no root= and a tmpfs
    or rootfs on /. Every file system of df is mounted."""
    if _text(honeyfs / "etc/fstab"):
        return
    dmesg = _text(txtcmds / "bin/dmesg")
    match = re.search(r"(?:Kernel command line|Command line): (.*)$", dmesg, re.M)
    if not match:
        out.append("boot: dmesg has no kernel command line")
        return
    root = next((t[len("root="):] for t in match.group(1).split() if t.startswith("root=")), None)
    mounts = _MOUNT.findall(_text(txtcmds / "bin/mount"))
    vfs = _VFS.search(dmesg)
    if vfs is None:
        if root is not None:
            out.append(f"boot: root={root} of dmesg, but no root was mounted from it")
        if not any(mp == "/" and fs in ("tmpfs", "rootfs") for _, mp, fs in mounts):
            out.append("boot: an initramfs boot without a tmpfs or rootfs on /")
    else:
        fstype, major, minor = vfs.group(1), int(vfs.group(2)), int(vfs.group(3))
        devices = {"/dev/root", root} if root else {"/dev/root"}
        if not any(dev in devices and fs == fstype for dev, _, fs in mounts):
            out.append(f"boot: dmesg mounted a {fstype} root, mount shows none on {' or '.join(sorted(devices))}")
        mtd = re.fullmatch(r"/dev/mtdblock(\d+)", root or "")
        if mtd and (major, minor) != (31, int(mtd.group(1))):
            out.append(f"boot: root={root}, but dmesg mounted device {major}:{minor}")
    mounted = {(dev, mp) for dev, mp, _ in mounts}
    for line in _text(txtcmds / "bin/df").splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 6 and (fields[0], fields[-1]) not in mounted:
            out.append(f"df: {fields[0]} on {fields[-1]} is not in mount")


def _check_dates(ident, honeyfs, out):
    first, last = _utc_date(ident.installed_at), _utc_date(ident.now)
    for path in sorted((honeyfs / "var/log").rglob("*")):
        if not path.is_file():
            continue
        for y, m, d in re.findall(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)", _text(path)):
            try:
                day = datetime.date(int(y), int(m), int(d))
            except ValueError:
                out.append(f"date {y}-{m}-{d} in {path.relative_to(honeyfs)} is no date")
                continue
            if not first <= day <= last:
                out.append(f"date {day} in {path.relative_to(honeyfs)} is not between {first} and {last}")
    for line in _text(honeyfs / "etc/shadow").splitlines():
        fields = line.split(":")
        if len(fields) > 2 and fields[2].isdigit() and int(fields[2]) > ident.now // DAY:
            out.append(f"date: shadow lastchange of {fields[0]} is in the future")


def _tree_texts(fs):
    """(path, bytes) of what the shell shows of fs.pickle: the path of every node, the
    contents of files and the targets of links. The raw pickle is no text: its
    ctimes are 4-byte integers (0x6c696870 is b"phil", August 2027)."""
    for path, node in tree.iter_nodes(fs):
        yield path, path.encode("utf-8", "replace")
        contents = tree.node_get(node, tree.A_CONTENTS)
        if tree.node_get(node, tree.A_TYPE) != tree.T_DIR and isinstance(contents, (bytes, str)):
            yield path, contents.encode("utf-8", "replace") if isinstance(contents, str) else contents
        target = tree.node_get(node, tree.A_TARGET)
        if isinstance(target, str):
            yield path, target.encode("utf-8", "replace")


def _check_forbidden(persona, out_dir, runtime_dir, keys_dir, fs, out):
    markers = FORBIDDEN_MARKERS + (UBUNTU_MARKERS if persona.os_id != "ubuntu" else ())
    markers = tuple(m.lower().encode() for m in markers)
    paths = [out_dir / "cmdoutput.json", out_dir / "cowrie.cfg", out_dir / BUSYBOX_HELP,
             out_dir / system.SERVICE_STATUS, out_dir / system.SYSTEMD_UNITS, out_dir / system.SUDO_LIST,
             out_dir / system.APT_PACKAGES]
    for root in (out_dir / "honeyfs", out_dir / "txtcmds"):
        for path in sorted(root.rglob("*")):
            if any(m in path.name.lower().encode() for m in markers):
                out.append(f"forbidden name {path.relative_to(out_dir)}")
            if path.is_file():
                paths.append(path)
    # the directories are the operator's (or a test's random temp dir), not persona content
    own_paths = sorted({str(runtime_dir).lower().encode(), str(keys_dir).lower().encode()}, key=len, reverse=True)

    def found_in(data):
        data = data.lower()
        for own in own_paths:
            data = data.replace(own, b"")
        return [m.decode() for m in markers if m in data]

    for path in paths:
        found = found_in(path.read_bytes() if path.is_file() else b"")
        if found:
            out.append(f"forbidden {', '.join(found)} in {path.relative_to(out_dir)}")
    if fs is None:
        return
    hits = {}
    for path, data in _tree_texts(fs):
        for marker in found_in(data):
            hits.setdefault(marker, path)
    for marker, path in sorted(hits.items()):
        out.append(f"forbidden {marker} in fs.pickle ({path})")


def _skipped(cfg):
    return {e.strip() for e in cfg.get("shell", "skip_python_commands", fallback="").split(",") if e.strip()}


def _answered(cfg, name):
    """Cowrie's Python answers the command (it is not in skip_python_commands)."""
    skipped = _skipped(cfg)
    return name not in skipped and not any(f"/{d}/{name}" in skipped for d in PATH_DIRS)


def _check_package_managers(persona, cfg, fs, honeyfs, txtcmds, out):
    """The package managers of the persona are programs answered by a txtcmd or by Cowrie's
    Python (apt / yum of a server), the others are neither."""
    names = set()
    # bin/apt and usr/bin/apt are one node where bin is a link to usr/bin
    commands = {}
    for rel in PACKAGE_MANAGER_PATHS:
        real = tree.real_path(fs, rel) or rel
        python = Path(rel).name in PYTHON_PACKAGE_MANAGERS and _answered(cfg, Path(rel).name)
        commands[real] = commands.get(real, False) or (txtcmds / rel).is_file() or python
    for rel in PACKAGE_MANAGER_PATHS:
        command = commands[tree.real_path(fs, rel) or rel]
        in_pickle = tree.find_node(fs, rel) is not None
        if command != in_pickle:
            out.append(f"package manager {rel}: command {command}, fs.pickle {in_pickle}")
        if (honeyfs / rel).exists() and not command:
            out.append(f"package manager {rel} in honeyfs without txtcmd")
        if command:
            names.add(Path(rel).name)
    if names != set(persona.package_managers):
        out.append(f"package manager set {sorted(names)} is not {sorted(persona.package_managers)}")


def _is_file(node):
    return node is not None and tree.node_get(node, tree.A_TYPE) == tree.T_FILE


def _is_program(node):
    return (node is not None and tree.node_get(node, tree.A_TYPE) == tree.T_FILE
            and (tree.node_get(node, tree.A_MODE) or 0) & 0o111 != 0)


def _check_programs(fs, txtcmds, out):
    """Every program in a bin / sbin directory has a size, every txtcmd is a program
    without contents (Cowrie's cat / dd serve the ELF header of [shell] arch)."""
    empty = [path for path, node in tree.iter_nodes(fs)
             if path.rsplit("/", 2)[-2] in ("bin", "sbin") and _is_program(node)
             and not tree.node_get(node, tree.A_SIZE)]
    if empty:
        out.append(f"program: size 0 for {', '.join(empty[:5])}")
    for path in sorted(txtcmds.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(txtcmds))
        node = tree.find_node(fs, rel)
        if not _is_program(node):
            out.append(f"program: txtcmd {rel} is no executable of fs.pickle")
        elif isinstance(tree.node_get(node, tree.A_CONTENTS), (bytes, str)):
            out.append(f"program: txtcmd {rel} has contents in fs.pickle, cat shows no ELF header")


def _check_busybox(ident, cfg, out_dir, runtime_dir, fs, out):
    """BusyBox of the firmware: Cowrie's busybox runs (not skipped), prints the help
    of that version and /bin/busybox is a program. Without BusyBox it is skipped."""
    banner = ident.release.extra.get("busybox")
    skipped = {e.strip() for e in cfg.get("shell", "skip_python_commands", fallback="").split(",")}
    key = cfg.get("shell", "busybox_help", fallback=None)
    if not banner:
        if "busybox" not in skipped or key is not None:
            out.append("busybox: Cowrie's busybox answers on a system without BusyBox")
        return
    if "busybox" in skipped or "/bin/busybox" in skipped:
        out.append("busybox: Cowrie's busybox is skipped, the applets do not run")
    if key != f"{runtime_dir}/{BUSYBOX_HELP}":
        out.append(f"busybox: cfg busybox_help {key!r} is not {runtime_dir}/{BUSYBOX_HELP}")
    if not _text(out_dir / BUSYBOX_HELP).startswith(f"{banner} multi-call binary.\n"):
        out.append(f"busybox: the help does not start with {banner}")
    if fs is not None and not _is_program(tree.find_node(fs, "bin/busybox")):
        out.append("busybox: /bin/busybox is no program of fs.pickle")


# the commands of Cowrie's Python whose program differs per system (which and the command agree)
COMMANDS = ("perl", "git", "python", "python3", "sudo", "service", "systemctl", "apt", "apt-get", "yum",
            "dnf", "rpm")


def _matches(fs, rel):
    """The paths of the tree a path (a glob pattern in its last part) names."""
    parent_rel, name = posixpath.split(rel)
    if not any(c in name for c in "*?["):
        return [rel] if tree.find_node(fs, rel) is not None else []
    parent = tree.find_dir(fs, parent_rel)
    return [f"{parent_rel}/{tree.node_get(c, tree.A_NAME)}" for c in tree.node_children(parent or [])
            if fnmatch.fnmatchcase(tree.node_get(c, tree.A_NAME), name)] if parent else []


def _on_path(fs, name):
    for directory in PATH_DIRS:
        path = f"{directory}/{name}"
        node = tree.find_node(fs, path)
        for _ in range(5):
            if node is None or tree.node_get(node, tree.A_TYPE) != tree.T_LINK:
                break
            path = tree.target_path(fs, path)
            node = tree.find_node(fs, path) if path is not None else None
        if _is_program(node):
            return True
    return False


def _check_links(fs, out):
    """Every link of the tree names a node (the links of its directories followed): ls -l and cd
    find what a link points to."""
    dangling = []
    for path, node in tree.iter_nodes(fs):
        if tree.node_get(node, tree.A_TYPE) != tree.T_LINK:
            continue
        target = tree.node_get(node, tree.A_TARGET) or ""
        resolved = tree.target_path(fs, path) if target else None
        if resolved is None or tree.find_node(fs, resolved) is None:
            dangling.append(f"{path} -> {target}")
    if dangling:
        out.append(f"link: {len(dangling)} links name nothing: {', '.join(dangling[:4])}")


_LSB_LEVELS = re.compile(r"^# Default-(Start|Stop):[ \t]*(.*)$", re.M)
_CHKCONFIG = re.compile(r"^# chkconfig:[ \t]+(-|\d+)[ \t]", re.M)
_RC_LINK = re.compile(r"([SK])\d\d(.+)")


def _run_levels(name, text):
    """(start levels, stop levels) of an init script: chkconfig's line (Red Hat), the LSB header,
    else those of Cowrie's own scripts; None if it names none."""
    text = text.decode("utf-8", "replace") if isinstance(text, bytes) else ""
    match = _CHKCONFIG.search(text)
    if match:
        start = "" if match.group(1) == "-" else match.group(1)
        return set(start), set("0123456") - set(start)
    header = dict(_LSB_LEVELS.findall(text))
    if header:
        return set(header.get("Start", "").split()), set(header.get("Stop", "").split())
    levels = system.TREE_INIT_LEVELS.get(name)
    return None if levels is None else (set(levels[0].split()), set(levels[1].split()))


def _check_init_links(persona, fs, out):
    """The run level links of a server are those of its init scripts: S<nn><name> in every level of
    Default-Start (chkconfig's levels on Red Hat), K<nn><name> in every level of Default-Stop, each
    naming its script (update-rc.d / chkconfig)."""
    init = tree.find_dir(fs, "etc/init.d")
    if persona.kind != "server" or init is None:
        return
    expected = {}
    for node in tree.node_children(init):
        name = tree.node_get(node, tree.A_NAME)
        if name in ("README", "functions") or not _is_file(node):
            continue
        levels = _run_levels(name, tree.node_get(node, tree.A_CONTENTS))
        if levels is None:
            out.append(f"init: /etc/init.d/{name} names no run levels")
            continue
        expected.update({(level, sign, name): None for sign, part in zip("SK", levels) for level in part})
    found = set()
    for level in "0123456S":
        directory = tree.find_dir(fs, f"etc/rc{level}.d")
        for node in tree.node_children(directory) if directory is not None else ():
            match = _RC_LINK.fullmatch(tree.node_get(node, tree.A_NAME))
            if match is None:
                continue
            link = f"etc/rc{level}.d/{tree.node_get(node, tree.A_NAME)}"
            sign, name = match.groups()
            target = tree.target_path(fs, link) if tree.node_get(node, tree.A_TYPE) == tree.T_LINK else None
            if target is None or target != tree.real_path(fs, f"etc/init.d/{name}"):
                out.append(f"init: /{link} names {'/' + target if target else 'nothing'}, not /etc/init.d/{name}")
            elif (level, sign, name) not in expected:
                out.append(f"init: /{link}, /etc/init.d/{name} does not {'start' if sign == 'S' else 'stop'} "
                           f"in run level {level}")
            found.add((level, sign, name))
    for level, sign, name in sorted(set(expected) - found):
        out.append(f"init: /etc/init.d/{name} {'starts' if sign == 'S' else 'stops'} in run level {level}, "
                   f"/etc/rc{level}.d has no {sign} link to it")


_DNF_LOG_LINE = re.compile(r"SUBDEBUG (Installed|Upgrade): (\S+)$", re.M)


def _upstream(evr):
    return evr.split(":", 1)[-1].split("-", 1)[0]


def _check_dnf(persona, ident, out_dir, fs, out):
    """dnf / rpm of RHEL 9 (dnf-packages.json) and the system agree: a program on PATH is from an installed
    package, a tool runs exactly where its package is installed, the packages of the persona have the
    versions the system shows (uname, ssh -V, sudo -V, perl, python3, systemctl, cloud-init's sudoers rule),
    dnf.log installed what is installed, redhat.repo has the repositories dnf lists."""
    try:
        data = json.loads(_text(out_dir / system.DNF_PACKAGES))
        installed, available = data["installed"], data["available"]
    except (ValueError, KeyError, TypeError):
        out.append(f"dnf: {system.DNF_PACKAGES} is no package list")
        return
    for program, package in sorted(system.RPM_PROGRAMS.items()):
        if _on_path(fs, program) and package not in installed:
            out.append(f"dnf: {program} is on PATH, rpm does not have {package} installed")
        elif package in installed and not _on_path(fs, program):
            out.append(f"dnf: {package} is installed, its program {program} is not on PATH")
    tools = system.installed_tools(persona, ident)
    for tool, package in sorted(system.RPM_TOOLS.items()):
        if (tool in tools) != (package in installed):
            out.append(f"dnf: {tool} is {'' if tool in tools else 'not '}installed, rpm has "
                       f"{'' if package in installed else 'no '}{package}")
    for name, entry in sorted(available.items()):
        for path in entry.get("programs", []):
            if tree.find_node(fs, path.lstrip("/")) is not None:
                out.append(f"dnf: {path} is in fs.pickle, rpm does not have {name} installed")
    for name in sorted(set(installed) & set(available)):
        out.append(f"dnf: {name} is installed and available")
    for name, entry in sorted(available.items()):
        for dep in entry.get("requires", []):
            if dep not in available and dep not in installed:
                out.append(f"dnf: {name} requires {dep}, which dnf does not know")
    r = ident.release
    found = system.tools(persona, ident)
    expected = {"kernel": r.kernel_version.rsplit(".", 1)[0], "openssh-server": r.packages["openssh-server"],
                "sudo": system.sudo_version(persona, ident), "python3": found["python"],
                "perl-interpreter": found["perl"][0], "dnf": r.packages["dnf"], "rpm": r.packages["rpm"],
                "systemd": str(servers.systemd_version(persona, ident)[0]),
                "openssl": r.ssh_version.split("OpenSSL ", 1)[-1].split()[0],
                "cloud-init": system.cloud_init_version(persona, ident)[0],
                "redhat-release": r.os_version_id}
    for name, version in expected.items():
        evr = installed.get(name, {}).get("evr", "")
        shown = evr.split(":", 1)[-1] if name in ("kernel", "openssh-server", "cloud-init") else _upstream(evr)
        if shown != version:
            out.append(f"dnf: {name} is {evr or 'not installed'}, the system shows {version}")
    if (data.get("version"), data.get("rpm_version")) != (r.packages["dnf"], r.packages["rpm"]):
        out.append("dnf: the versions of dnf and rpm are not the persona's")
    for kind, nevra in _DNF_LOG_LINE.findall(_text(out_dir / "honeyfs/var/log/dnf.log")):
        if not any(nevra == f"{name}-{entry['evr']}.{entry['arch']}" for name, entry in installed.items()):
            out.append(f"dnf: dnf.log names {kind} {nevra}, rpm does not have it installed")
    text = _text(out_dir / "honeyfs/etc/yum.repos.d/redhat.repo")
    enabled = re.findall(r"^\[([^\]]+)\]\n(?:[^\[]*?\n)?enabled = 1$", text, re.M)
    if sorted(enabled) != sorted(repo["id"] for repo in data.get("repos", []) if repo.get("enabled")):
        out.append(f"dnf: redhat.repo enables {sorted(enabled)}, dnf lists others")
    owners = data.get("owners", {})
    for path, package in sorted(owners.items()):
        if package not in installed:
            out.append(f"dnf: rpm names {package} the owner of {path}, rpm does not have it installed")
    # every program on PATH is from a package rpm names
    for directory in ("usr/bin", "usr/sbin"):
        for node in tree.node_children(tree.find_dir(fs, directory) or []):
            name = tree.node_get(node, tree.A_NAME)
            if (tree.node_get(node, tree.A_TYPE) == tree.T_LINK or _is_program(node)) \
                    and f"/{directory}/{name}" not in owners:
                out.append(f"dnf: /{directory}/{name} is on PATH, rpm names no owner of it")


def _check_system(persona, ident, cfg, out_dir, runtime_dir, fs, out):
    """Cowrie's Debian 12 x86_64 container tree is the persona's system (tpot_personas.system):
    no foreign paths, no x86_64 names on another architecture and no names of Debian's own
    packages without dpkg, /proc/modules of the persona, the commands of Cowrie's Python
    (interpreters, sudo, service, apt / yum) on PATH exactly where Cowrie or a txtcmd answers
    them, with their versions."""
    honeyfs, txtcmds = out_dir / "honeyfs", out_dir / "txtcmds"
    own = {"/" + rel for rel, _ in system.links(persona)}
    found = [hit for rel in system.CONTAINER_PATHS + system.foreign_paths(persona) for hit in _matches(fs, rel)
             if "/" + hit not in own]
    if found:
        out.append(f"foreign: {', '.join('/' + rel for rel in found[:6])} on {persona.id}")
    for word in system.foreign_words(persona):
        named = [path for path, node in tree.iter_nodes(fs) if path not in own
                 and any(isinstance(v, str) and word in v
                         for v in (tree.node_get(node, tree.A_NAME), tree.node_get(node, tree.A_TARGET)))]
        if named:
            out.append(f"foreign: {word} names on {persona.id}: {', '.join(named[:4])}")
    triplet = system.triplet(persona)
    if triplet and tree.find_dir(fs, f"usr/lib/{triplet}") is None:
        out.append(f"foreign: no multiarch directory /usr/lib/{triplet}")
    node = tree.find_node(fs, system.PROC_MODULES)
    modules = _text(honeyfs / system.PROC_MODULES)
    if not modules or tree.node_get(node or [], tree.A_CONTENTS) != modules.encode() \
            or modules != system.proc_modules(persona, ident):
        out.append("modules: /proc/modules is not the persona's")
    out += system.modules_problems(modules, system.address_width(persona))
    for name in COMMANDS:
        answered = _answered(cfg, name)
        txtcmd = any((txtcmds / d / name).is_file() for d in PATH_DIRS)
        if answered and txtcmd:
            out.append(f"which: a txtcmd of {name} answers /usr/bin/{name}, Cowrie's {name} answers {name}")
        if _on_path(fs, name) != (answered or txtcmd):
            out.append(f"which: {name} is {'not ' if answered or txtcmd else ''}on PATH, "
                       f"{'Cowrie answers it' if answered else 'a txtcmd answers it' if txtcmd else 'nothing answers it'}")
    for name in system.python_answers(persona):
        if not _answered(cfg, name):
            out.append(f"which: Cowrie's {name} is skipped on a server")
    files = {str(path.relative_to(honeyfs)): _text(path) for path in (honeyfs / "etc/apt").rglob("*")
             if path.is_file()}
    for key, value in system.shell_keys(persona, ident, files, runtime_dir).items():
        if cfg.get("shell", key, fallback=None) != value:
            out.append(f"cfg {key}: {cfg.get('shell', key, fallback=None)!r}, expected {value!r}")
    if persona.kind == "server" and _text(out_dir / system.SERVICE_STATUS) != system.service_status(persona):
        out.append("service: service-status.txt is not the persona's")
    if persona.kind == "server":
        try:
            processes = json.loads(_text(out_dir / "cmdoutput.json"))["command"]["ps"]
            expected = runtime_json(persona, ident, processes)
        except (ValueError, KeyError, TypeError):
            expected = {}
        for name, data in expected.items():
            try:
                found = json.loads(_text(out_dir / name))
            except ValueError:
                found = None
            if found != json.loads(json.dumps(data)):
                out.append(f"runtime: {name} is not the persona's")
        for rel, text in system.sudoers_files(persona, ident).items():
            node = tree.find_node(fs, rel)
            if _text(honeyfs / rel) != text or node is None or tree.node_get(node, tree.A_CONTENTS) != text.encode() \
                    or (tree.node_get(node, tree.A_MODE) or 0) & 0o7777 != 0o440:
                out.append(f"sudoers: /{rel} is not the persona's")
        if system.has_dpkg(persona):
            try:
                installed = json.loads(_text(out_dir / system.APT_PACKAGES)).get("installed", {})
            except (ValueError, AttributeError):
                installed = {}
            for package, program in sorted(system.PACKAGE_PROGRAMS.items()):
                if _on_path(fs, program) and package not in installed:
                    out.append(f"apt: {program} is on PATH, apt does not have it installed")
                elif package in installed and not _on_path(fs, program):
                    out.append(f"apt: {package} is installed, its program {program} is not on PATH")
        if persona.family == "rhel":
            _check_dnf(persona, ident, out_dir, fs, out)
        tools = system.installed_tools(persona, ident)
        for tool, programs in sorted(system.TOOL_PROGRAMS.items()):
            for name in programs:
                answers = (name in system.COWRIE_TOOL_NAMES and _answered(cfg, name)) or _on_path(fs, name)
                if answers and system.has_dpkg(persona) and tool not in installed:
                    out.append(f"tools: {name} of {tool} answers, apt does not have {tool} installed")
                elif answers != (tool in tools):
                    out.append(f"tools: {name} of {tool} {'answers' if answers else 'does not answer'}, "
                               f"{tool} is {'' if tool in tools else 'not '}installed")
                if name in system.COWRIE_TOOL_NAMES and _answered(cfg, name) != _on_path(fs, name):
                    out.append(f"which: {name} is {'not ' if _answered(cfg, name) else ''}on PATH, "
                               f"{'Cowrie answers it' if _answered(cfg, name) else 'nothing answers it'}")
        units = {u["name"]: u for u in expected.get(system.SYSTEMD_UNITS, {}).get("units", [])}
        for name, unit in sorted(units.items()):
            node = tree.find_node(fs, unit["path"])
            if node is None:
                out.append(f"systemctl: {unit['path']} of {name} is not in fs.pickle")
                continue
            text = tree.node_get(node, tree.A_CONTENTS)
            if unit["path"].startswith("/etc/"):
                continue
            if not isinstance(text, bytes):
                out.append(f"systemctl: {unit['path']} of {name} has no text, systemctl cat prints nothing of it")
                continue
            for line in text.decode("utf-8", "replace").splitlines():
                program = system.exec_program(line) if line.startswith("ExecStart=") else ""
                node = system.program_node(fs, program) if program else None
                if program and node is None:
                    out.append(f"systemctl: ExecStart of {name} names {program}, which is not in fs.pickle")
                elif program in servers.SCRIPT_PROGRAMS and not isinstance(tree.node_get(node, tree.A_CONTENTS), bytes):
                    out.append(f"systemctl: {program} of {name} is a script, fs.pickle has no text of it")
        for line in _text(out_dir / system.SERVICE_STATUS).splitlines():
            if line.startswith(" [ ") and not _is_file(tree.find_node(fs, f"etc/init.d/{line.split()[-1]}")):
                out.append(f"service: --status-all names {line.split()[-1]}, /etc/init.d/{line.split()[-1]} is missing")
            script = line.split()[-1] if line.startswith(" [ + ]") else None
            if script and not any(name.startswith(script + ".") or name in system.INIT_UNITS.get(script, ())
                                  for name in units):
                out.append(f"service: {script} runs, systemctl has no unit of it")


def problems(persona, ident, out_dir, runtime_dir, keys_dir, min_pickle=MIN_PICKLE_SIZE):
    out_dir, runtime_dir, keys_dir = Path(out_dir), Path(runtime_dir), Path(keys_dir)
    out = [f"missing {name}" for name in REQUIRED if not (out_dir / name).exists()]
    if out:
        return out
    honeyfs, txtcmds = out_dir / "honeyfs", out_dir / "txtcmds"
    r = ident.release

    if (out_dir / "fs.pickle").stat().st_size < min_pickle:
        out.append(f"pickle too small: {(out_dir / 'fs.pickle').stat().st_size} < {min_pickle} bytes")
    try:
        fs = tree.load_pickle(out_dir / "fs.pickle")
    except Exception as exc:  # noqa: BLE001 - any unpickling error is a finding
        out.append(f"pickle unreadable: {exc}")
        fs = None

    cfg = configparser.ConfigParser(interpolation=None)
    try:
        cfg.read(out_dir / "cowrie.cfg", encoding="utf-8")
    except configparser.Error as exc:
        out.append(f"cfg unreadable: {exc}")
    _check_cfg(persona, ident, cfg, runtime_dir, keys_dir, out)
    _check_ssh(persona, ident, cfg, out)

    try:
        data = json.loads(_text(out_dir / "identity.json"))
    except ValueError:
        out.append("identity.json is no JSON")
    else:
        if not isinstance(data, dict):
            out.append("identity.json is no JSON object")
        elif (data.get("persona"), data.get("hostname"), data.get("boot_offset")) != \
                (persona.id, ident.hostname, ident.boot_offset):
            out.append("identity.json does not describe this identity")

    version = _text(honeyfs / "proc/version")
    if r.kernel_version not in version or r.kernel_build not in version:
        out.append(f"kernel: proc/version does not name {r.kernel_version} {r.kernel_build}")
    banner = re.search(r"\bLinux version (\S+)", _text(txtcmds / "bin/dmesg"))
    if not banner or banner.group(1) != r.kernel_version:
        out.append(f"kernel: dmesg does not name {r.kernel_version}")
    built = kernel_time(r)
    if built is not None and built > ident.booted_at:
        out.append(f"kernel: built {time.strftime('%Y-%m-%d', time.gmtime(built))}, after the boot")
    _check_clock(persona, ident, out)

    patterns = host_patterns(persona, ident.hardware)
    if not any(re.fullmatch(hostname_regex(p, ident.hardware), ident.hostname) for p in patterns):
        out.append(f"hostname: {ident.hostname} fits no host name of the {ident.hardware.provider or 'any'} hardware")
    if _text(honeyfs / "etc/hostname") != ident.hostname + "\n":
        out.append(f"hostname: etc/hostname is not {ident.hostname}")
    if ident.hostname not in _text(honeyfs / "etc/hosts").split():
        out.append(f"hostname: etc/hosts does not name {ident.hostname}")

    _check_hardware(persona, ident, honeyfs, txtcmds, out)
    _check_top(persona, cfg, txtcmds, out)
    _check_boot(honeyfs, txtcmds, out)
    if persona.kind == "device":
        _check_device_boot(honeyfs, txtcmds, out)

    os_release = _text(honeyfs / "etc/os-release")
    fields = dict(line.split("=", 1) for line in os_release.splitlines() if "=" in line)
    # firmware of routers, cameras and NAS vendors has no os-release
    if (os_release or persona.kind != "device") and fields.get("ID", "").strip().strip('"') != persona.os_id:
        out.append(f"os-release ID is not {persona.os_id}")
    if "VERSION_ID" in fields and fields["VERSION_ID"].strip().strip('"') != r.os_version_id:
        out.append(f"os-release VERSION_ID is not {r.os_version_id}")

    _check_processes(persona, ident, out_dir / "cmdoutput.json", out)
    _check_dates(ident, honeyfs, out)
    _check_logs(ident, honeyfs, fs, out)
    _check_forbidden(persona, out_dir, runtime_dir, keys_dir, fs, out)
    _check_busybox(ident, cfg, out_dir, runtime_dir, fs, out)
    if fs is not None:
        _check_tree(persona, ident, fs, out)
        _check_package_managers(persona, cfg, fs, honeyfs, txtcmds, out)
        _check_programs(fs, txtcmds, out)
        _check_system(persona, ident, cfg, out_dir, runtime_dir, fs, out)
        _check_links(fs, out)
        _check_init_links(persona, fs, out)
    return out


def validate(persona, ident, out_dir, runtime_dir, keys_dir, min_pickle=MIN_PICKLE_SIZE):
    found = problems(persona, ident, out_dir, runtime_dir, keys_dir, min_pickle)
    if found:
        raise PersonaError(f"{persona.id}: " + "; ".join(found))
