"""Consistency of a rendered persona: what an attacker can cross-check must agree.

problems() returns one string per finding, each starting with the word of its
rule (kernel, hostname, nproc, START, forbidden, ...); validate() raises."""
import calendar
import configparser
import datetime
import json
import re
import time
from pathlib import Path

from . import PersonaError, tree
from .common import home_dir, is_login_shell, ps_user
from .identity import DAY, host_patterns, hostname_regex, kernel_time, stale_at, updated_at
from .render import DIR_MODES, PACKAGE_MANAGER_PATHS
from .ssh_profiles import COMPRESSIONS, HOST_KEYS, PROFILES, TWISTED_CIPHERS, TWISTED_MACS

MIN_PICKLE_SIZE = 1_000_000
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
        ("ssh", "version"): r.ssh_banner,
    }
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

    banner = ident.release.ssh_banner
    ssh_version = cfg.get("shell", "ssh_version", fallback="")
    product = banner[len("SSH-2.0-"):].split(" ", 1)[0] if banner.startswith("SSH-2.0-") else ""
    if product.lower().startswith("dropbear_"):
        version = product.split("_", 1)[1]
        if "dropbear" not in ssh_version.lower() or not re.search(rf"(^|\s){re.escape(version)}(\s|,|$)", ssh_version):
            out.append(f"ssh_version {ssh_version!r} does not name {product} of the banner")
    elif product.startswith("OpenSSH_"):
        # RHEL / CentOS send OpenSSH_8.7 while ssh -V says OpenSSH_8.7p1
        if not re.match(rf"{re.escape(product)}(p\d+)?(,|\s|$)", ssh_version):
            out.append(f"ssh_version {ssh_version!r} does not name {product} of the banner")
    else:
        out.append(f"ssh_version: banner {banner!r} names no known product")


def _check_hardware(persona, ident, honeyfs, txtcmds, out):
    hw = ident.hardware
    cpuinfo = _text(honeyfs / "proc/cpuinfo")
    if len(re.findall(r"^processor\s*:", cpuinfo, re.M)) != hw.cpus:
        out.append(f"cpuinfo: processor count is not {hw.cpus}")
    # what an attacker counts: lines with the lowercase word, the model name included
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
    top = _text(txtcmds / "usr/bin/top")
    procps = re.search(r"MiB Mem :\s+([\d.]+) total", top)
    busybox = re.search(r"Mem: (\d+)K used, (\d+)K free", top)
    if not procps and not busybox:
        out.append("top shows no memory")
    if procps and procps.group(1) != f"{hw.mem_kb / 1024:.1f}":
        out.append(f"top MiB Mem {procps.group(1)} is not {hw.mem_kb / 1024:.1f}")
    if busybox and int(busybox.group(1)) + int(busybox.group(2)) != hw.mem_kb:
        out.append(f"top Mem used + free is not {hw.mem_kb}K")


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
    boot = time.strftime("%b%d", time.gmtime(ident.booted_at))
    since_boot = {time.strftime("%b%d", time.gmtime(ts))
                  for ts in range(ident.booted_at, ident.now, DAY)}
    since_boot.add(time.strftime("%b%d", time.gmtime(ident.now)))
    for p in processes:
        if is_login_shell(p):
            if p["START"] not in since_boot:
                out.append(f"START {p['START']!r} of {p['COMMAND']!r} is not between boot and now")
        elif p["START"] != boot:
            out.append(f"START {p['START']!r} of {p['COMMAND']!r} is not the boot day {boot}")
    pids = [p["PID"] for p in processes]
    if pids[0] != 1:
        out.append(f"PID of {processes[0]['COMMAND']!r} is {pids[0]}, not 1")
    if len(set(pids)) != len(pids):
        out.append("PID used twice in ps")


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
    from the rotation name yum.log-<year>0101, else that of now)."""
    events = []
    for path in sorted((honeyfs / "var/log").rglob("*")):
        if not path.is_file():
            continue
        rel, text = str(path.relative_to(honeyfs)), _text(path)
        if path.name.startswith("yum.log"):
            rotated = re.search(r"-(\d{4})0101$", path.name)
            year = int(rotated.group(1)) - 1 if rotated else time.gmtime(ident.now).tm_year
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


def _check_forbidden(persona, out_dir, runtime_dir, keys_dir, out):
    markers = FORBIDDEN_MARKERS + (UBUNTU_MARKERS if persona.os_id != "ubuntu" else ())
    markers = tuple(m.lower().encode() for m in markers)
    paths = [out_dir / "fs.pickle", out_dir / "cmdoutput.json", out_dir / "cowrie.cfg"]
    for root in (out_dir / "honeyfs", out_dir / "txtcmds"):
        for path in sorted(root.rglob("*")):
            if any(m in path.name.lower().encode() for m in markers):
                out.append(f"forbidden name {path.relative_to(out_dir)}")
            if path.is_file():
                paths.append(path)
    # the directories are the operator's (or a test's random temp dir), not persona content
    own_paths = sorted({str(runtime_dir).lower().encode(), str(keys_dir).lower().encode()}, key=len, reverse=True)
    for path in paths:
        data = path.read_bytes().lower() if path.is_file() else b""
        for own in own_paths:
            data = data.replace(own, b"")
        found = [m.decode() for m in markers if m in data]
        if found:
            out.append(f"forbidden {', '.join(found)} in {path.relative_to(out_dir)}")


def _check_package_managers(persona, fs, honeyfs, txtcmds, out):
    names = set()
    for rel in PACKAGE_MANAGER_PATHS:
        command = (txtcmds / rel).is_file()
        in_pickle = tree.find_node(fs, rel) is not None
        if command != in_pickle:
            out.append(f"package manager {rel}: txtcmd {command}, fs.pickle {in_pickle}")
        if (honeyfs / rel).exists() and not command:
            out.append(f"package manager {rel} in honeyfs without txtcmd")
        if command:
            names.add(Path(rel).name)
    if names != set(persona.package_managers):
        out.append(f"package manager set {sorted(names)} is not {sorted(persona.package_managers)}")


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

    patterns = host_patterns(persona, ident.hardware)
    if not any(re.fullmatch(hostname_regex(p, ident.hardware), ident.hostname) for p in patterns):
        out.append(f"hostname: {ident.hostname} fits no host name of the {ident.hardware.provider or 'any'} hardware")
    if _text(honeyfs / "etc/hostname") != ident.hostname + "\n":
        out.append(f"hostname: etc/hostname is not {ident.hostname}")
    if ident.hostname not in _text(honeyfs / "etc/hosts").split():
        out.append(f"hostname: etc/hosts does not name {ident.hostname}")

    _check_hardware(persona, ident, honeyfs, txtcmds, out)
    _check_boot(honeyfs, txtcmds, out)

    os_release = _text(honeyfs / "etc/os-release")
    fields = dict(line.split("=", 1) for line in os_release.splitlines() if "=" in line)
    if fields.get("ID", "").strip().strip('"') != persona.os_id:
        out.append(f"os-release ID is not {persona.os_id}")
    if "VERSION_ID" in fields and fields["VERSION_ID"].strip().strip('"') != r.os_version_id:
        out.append(f"os-release VERSION_ID is not {r.os_version_id}")

    _check_processes(persona, ident, out_dir / "cmdoutput.json", out)
    _check_dates(ident, honeyfs, out)
    _check_logs(ident, honeyfs, fs, out)
    _check_forbidden(persona, out_dir, runtime_dir, keys_dir, out)
    if fs is not None:
        if ident.hostname.encode() not in (out_dir / "fs.pickle").read_bytes():
            out.append("hostname not in fs.pickle")
        _check_tree(persona, ident, fs, out)
        _check_package_managers(persona, fs, honeyfs, txtcmds, out)
    return out


def validate(persona, ident, out_dir, runtime_dir, keys_dir, min_pickle=MIN_PICKLE_SIZE):
    found = problems(persona, ident, out_dir, runtime_dir, keys_dir, min_pickle)
    if found:
        raise PersonaError(f"{persona.id}: " + "; ".join(found))
