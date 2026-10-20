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

from . import PersonaError, devices, firmware, kthreads, network, servers, system, tree
from .common import VM_PROVIDERS, home_dir, is_login_shell, ps_start, ps_user
from .identity import (DAY, drawn_at, host_patterns, hostname_regex, is_fresh, kernel_time, max_uptime, stale_at,
                       updated_at)
from .model import PROMPT_STYLES
from .render import (BUSYBOX_HELP, DF_TABLE, DIR_MODES, PACKAGE_MANAGER_PATHS, PROGRAM_DIRS, refused_logins_value,
                     runtime_json)
from .ssh_profiles import (COMPRESSIONS, HOST_KEY_ALGORITHMS, HOST_KEYS, PROFILES, TWISTED_CIPHERS, TWISTED_KEX,
                           TWISTED_MACS, host_key_algorithms, kex_algorithms, key_type, server_sig_algs,
                           server_version)

# a rendered device tree is about 1 MB (a server's more); far below it the render broke
MIN_PICKLE_SIZE = 800_000
PATH_DIRS = ("usr/local/sbin", "usr/local/bin", "usr/sbin", "usr/bin", "sbin", "bin")
PYTHON_PACKAGE_MANAGERS = ("apt", "apt-get", "dpkg", "yum", "dnf", "rpm", "opkg")
# a package manager of Cowrie's Python that is a program at one path only (opkg of OpenWrt)
PYTHON_PACKAGE_MANAGER_AT = {"opkg": "bin/opkg"}
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
        ("honeypot", "auth_refused_pairs"): refused_logins_value(),
        # only AuthRandom checks auth_refused_pairs (patch_auth)
        ("honeypot", "auth_class"): "AuthRandom",
        ("shell", "filesystem"): f"{runtime_dir}/fs.pickle",
        ("shell", "processes"): f"{runtime_dir}/cmdoutput.json",
        ("shell", "kernel_version"): r.kernel_version,
        ("shell", "kernel_build_string"): r.kernel_build,
        ("shell", "arch"): persona.arch,
        ("shell", "hardware_platform"): persona.hardware_platform,
        ("shell", "prompt_style"): persona.prompt_style,
        ("shell", "shell_name"): system.shell_style(persona)[0],
        ("shell", "not_found_style"): system.shell_style(persona)[1],
        ("shell", "history_size"): str(system.history_sizes(persona, r)[1]),
        ("shell", "history_size_root"): str(system.history_sizes(persona, r)[0]),
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
    _check_kex(profile, ident.release, cfg, out)
    banner = ident.release.ssh_banner
    ssh_version = cfg.get("shell", "ssh_version", fallback="")
    product = banner[len("SSH-2.0-"):].split(" ", 1)[0] if banner.startswith("SSH-2.0-") else ""
    if product.lower() == "dropbear":
        # OpenWrt and TP-Link build dropbear without the version in its banner: the version of the release
        if "dropbear" not in ssh_version.lower():
            out.append(f"ssh_version {ssh_version!r} does not name dropbear of the banner")
        elif ssh_version != ident.release.ssh_version:
            out.append(f"ssh_version {ssh_version!r} is not {ident.release.ssh_version!r} of the release")
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


def _check_kex(profile, release, cfg, out):
    """[ssh] kex_algorithms: Twisted's, with a curve25519 exchange (the clients of today offer no
    DH with SHA-1), those of the server of the banner in its order; [ssh] server_sig_algs as that
    server names them (ssh_profiles.kex_algorithms / server_sig_algs)."""
    names = tuple(e.strip() for e in cfg.get("ssh", "kex_algorithms", fallback="").split(",") if e.strip())
    out += [f"key exchange not supported: {name}" for name in names if name not in TWISTED_KEX]
    if not {"curve25519-sha256", "curve25519-sha256@libssh.org"} & set(names):
        out.append("key exchanges offer no curve25519: modern clients cannot connect")
    if names != kex_algorithms(profile, release):
        out.append(f"key exchanges {','.join(names)} differ from {profile.name} of {release.ssh_banner}")
    sigs = cfg.get("ssh", "server_sig_algs", fallback=None)
    if sigs is None or sigs.strip() != server_sig_algs(profile, release):
        out.append(f"server-sig-algs {sigs} differ from {profile.name} of {release.ssh_banner}")


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


def _check_top(persona, cfg, txtcmds, out, out_dir=None, runtime_dir=None):
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
    if not 10 <= threads <= 1500:
        out.append(f"top: {threads} kernel threads")
    if out_dir is None:
        return
    if cfg.get("shell", "kernel_thread_list", fallback="") != f"{runtime_dir}/{kthreads.KERNEL_THREADS}":
        out.append(f"top: cfg kernel_thread_list is not {runtime_dir}/{kthreads.KERNEL_THREADS}")
    try:
        count = len(json.loads((Path(out_dir) / kthreads.KERNEL_THREADS).read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        count = None
    if count != threads:
        out.append(f"top: {threads} kernel threads, ps lists {count}")
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
    has_lscpu, has_nproc = system.cpu_commands(persona, ident)
    if has_nproc and _text(txtcmds / "usr/bin/nproc").strip() != str(hw.cpus):
        out.append(f"nproc is not {hw.cpus}")
    if not has_nproc and (txtcmds / "usr/bin/nproc").exists():
        out.append("nproc: a txtcmd on a system without nproc")
    lscpu = _text(txtcmds / "usr/bin/lscpu")
    match = re.search(r"^CPU\(s\):\s+(\d+)", lscpu, re.M)
    if has_lscpu and (not match or int(match.group(1)) != hw.cpus):
        out.append(f"lscpu CPU(s) is not {hw.cpus}")
    if has_lscpu and hw.cpu_model not in lscpu:
        out.append(f"lscpu: no {hw.cpu_model!r}")
    if not has_lscpu and (txtcmds / "usr/bin/lscpu").exists():
        out.append("lscpu: a txtcmd on a system without lscpu")

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


def _check_accounts(persona, out_dir, out):
    """Every user of the ps list has its line in /etc/passwd (ps prints its uid, -u finds it)."""
    try:
        processes = json.loads(_text(Path(out_dir) / "cmdoutput.json"))["command"]["ps"]
    except (ValueError, KeyError, TypeError):
        return
    names = {line.split(":", 1)[0] for line in _text(Path(out_dir) / "honeyfs/etc/passwd").splitlines() if line}
    for entry in processes:
        name = entry.get("USERNAME") or entry.get("USER")
        if name not in names:
            out.append(f"accounts: {name} of {entry.get('COMMAND')!r} has no line in /etc/passwd")


def _check_program_owners(persona, ident, out_dir, fs, out):
    """Every program of the ps list of a server with a package manager is in the tree, and an installed package
    owns it as dpkg -S / rpm -qf name it (one below /usr/local, /opt or /home is nobody's); every program of a
    device's ps list and the telnetd of its sessions are in its tree (_check_device_programs)."""
    if persona.kind == "device":
        _check_device_programs(persona, ident, out_dir, fs, out)
        return
    if persona.kind != "server" or not (system.has_dpkg(persona) or system.is_el(persona)):
        return
    try:
        processes = json.loads(_text(Path(out_dir) / "cmdoutput.json"))["command"]["ps"]
    except (ValueError, KeyError, TypeError):
        return
    for entry in processes:
        command = str(entry.get("COMMAND") or "")
        for path in system.ps_programs(persona, ident, command):
            if path.startswith(system.UNPACKAGED):
                continue
            if fs is not None and tree.find_node(fs, path.lstrip("/")) is None and tree.resolve(fs, path.lstrip("/")) \
                    is None:
                out.append(f"owners: {path} of {command!r} is not in the tree")
            if system.program_owner(persona, ident, path) is None:
                out.append(f"owners: no package owns {path} of {command!r}")


def _check_device_programs(persona, ident, out_dir, fs, out):
    """The programs of a device's ps list (a bare name as its PATH finds it) and the telnetd of its telnet sessions
    ([shell] telnet_daemon) are in its tree."""
    if fs is None:
        return
    try:
        processes = json.loads(_text(Path(out_dir) / "cmdoutput.json"))["command"]["ps"]
    except (ValueError, KeyError, TypeError):
        return

    def present(path):
        return tree.resolve(fs, path.lstrip("/")) is not None

    folders = ("/sbin/", "/bin/", "/usr/sbin/", "/usr/bin/")
    for entry in processes:
        command = str(entry.get("COMMAND") or "")
        program = devices.ps_program(command)
        if program is None:
            continue
        spellings = [program] if program.startswith("/") else [folder + program for folder in folders]
        if not any(present(path) for path in spellings):
            out.append(f"programs: {program} of {command!r} is not in the tree")
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read(Path(out_dir) / "cowrie.cfg", encoding="utf-8")
    daemon = cfg.get("shell", "telnet_daemon", fallback="")
    if daemon != devices.telnet_daemon(persona, ident):
        out.append(f"cfg telnet_daemon: {daemon!r} is not the telnetd of {persona.id}")
    elif daemon and not present(daemon):
        out.append(f"cfg telnet_daemon: {daemon} is not in the tree")


# the values the patched commands take for the [shell] keys of round 4 (absent: the upstream behaviour)
TELNET_SERVERS = frozenset({"inetutils", "inetutils-2.6", "netkit"})
HISTORY_CONTROLS = frozenset({"ignorespace", "ignoredups", "ignoreboth", "erasedups"})
HISTORY_EXPORTS = frozenset({"HISTSIZE", "HISTFILESIZE", "HISTCONTROL", "HISTTIMEFORMAT"})


def _check_shell_keys(persona, ident, cfg, out_dir, runtime_dir, fs, out):
    """The [shell] keys of the telnet session, BusyBox's wget, dd, ls and usage texts, the program folders of
    which, the history of the login shell, command-not-found and the login's PATH and SHELL: allowed values,
    their files, and what the persona has."""
    out_dir, runtime_dir = Path(out_dir), Path(runtime_dir)

    def value(key):
        return cfg.get("shell", key, fallback=None)

    def bad(key, why):
        out.append(f"cfg {key}: {value(key)!r} {why}")

    def program(rel):
        node = tree.find_node(fs, rel.lstrip("/")) if fs is not None else None
        return node is not None and (tree.node_get(node, tree.A_TYPE) != tree.T_LINK or tree.resolve(fs, rel.lstrip("/")))

    style = value("telnet_server")
    expected = network.telnet_server(persona, ident) or None
    if style is not None and style not in TELNET_SERVERS:
        bad("telnet_server", f"is none of {', '.join(sorted(TELNET_SERVERS))}")
    elif style != expected:
        bad("telnet_server", f"is not the telnet server of {persona.id} ({expected!r})")
    elif style and not all(program(rel) for rel in (("usr/sbin/in.telnetd",) if style == "netkit"
                                                     else ("usr/sbin/telnetd", "usr/sbin/inetd"))):
        bad("telnet_server", "names a telnet daemon the tree has not")
    applets = system.busybox_applets(persona, ident) or ()
    if value("wget_style") not in (None, "busybox"):
        bad("wget_style", "is not busybox")
    elif value("wget_style") == "busybox" and ("wget" not in applets or not value("busybox_help")):
        bad("wget_style", "without the wget applet of a BusyBox")
    elif value("wget_style") is None and value("busybox_help") and "wget" in applets and persona.family != "ubiquiti":
        bad("wget_style", "is missing, the persona's wget is BusyBox's")
    for key in ("wget_timeout", "dd_status_line"):
        if value(key) not in (None, "no"):
            bad(key, "is not no")
    if value("wget_timeout") == "no" and value("wget_style") != "busybox":
        bad("wget_timeout", "without BusyBox's wget")
    ash = persona.family in system.ASH_FAMILIES
    if value("ls_style") not in (None, "busybox") or (value("ls_style") == "busybox") != ash:
        bad("ls_style", "does not fit the shell of the persona" if ash or value("ls_style") else "is not busybox")
    if value("ls_total") not in (None, "yes") or (value("ls_total") == "yes") != bool(system.busybox_desktop(persona)):
        bad("ls_total", "does not fit the BusyBox of the persona (CONFIG_DESKTOP)")
    usage = value("busybox_usage")
    want = firmware.usage_table(persona)
    if (usage is None) != (want is None) or (usage is not None and Path(usage) != Path(want)):
        bad("busybox_usage", f"is not the usage table of the persona ({want})")
    elif usage is not None:
        try:
            table = json.loads(Path(usage).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            table = None
        if not isinstance(table, dict) or not table:
            bad("busybox_usage", "is no table of usage texts")
    dirs = value("which_dirs")
    if dirs is not None:
        if dirs != f"{runtime_dir}/{PROGRAM_DIRS}" or not (out_dir / PROGRAM_DIRS).is_file():
            bad("which_dirs", f"is not {runtime_dir}/{PROGRAM_DIRS}")
        else:
            table = firmware.program_dirs(persona, applets) or {}
            rows = dict(line.partition(" ")[::2] for line in _text(out_dir / PROGRAM_DIRS).splitlines() if line)
            if rows != {name: ",".join(folders) for name, folders in table.items()}:
                bad("which_dirs", "is not the table of the persona's program folders")
            elif any(folder not in firmware.PROGRAM_FOLDERS for folders in rows.values() for folder in folders.split(",")):
                bad("which_dirs", f"names a folder other than {', '.join(firmware.PROGRAM_FOLDERS)}")
    for key, (root, user) in (("history_control", system.history_controls(persona)),):
        for name, want in ((key + "_root", root), (key, user)):
            given = value(name)
            if given is None:
                continue
            if any(part and part not in HISTORY_CONTROLS for part in given.split(":")):
                bad(name, f"is none of {', '.join(sorted(HISTORY_CONTROLS))}")
            elif given != want:
                bad(name, f"is not the HISTCONTROL of the persona ({want!r})")
    exports = value("history_export")
    if exports is not None:
        names = [name for name in exports.split(",") if name]
        if set(names) - HISTORY_EXPORTS or tuple(names) != tuple(system.history_exports(persona)):
            bad("history_export", f"is not what the login exports ({','.join(system.history_exports(persona))!r})")
    found = value("command_not_found")
    lines = system.command_not_found_lines(persona, ident)
    if (found is None) != (lines is None):
        bad("command_not_found", "does not fit the persona" if found else "is missing")
    elif found is not None and (found != f"{runtime_dir}/{system.COMMAND_NOT_FOUND}"
                                or not (out_dir / system.COMMAND_NOT_FOUND).is_file()):
        bad("command_not_found", f"is not {runtime_dir}/{system.COMMAND_NOT_FOUND}")
    sudo = value("command_not_found_sudo")
    if sudo is not None:
        group = _text(out_dir / "honeyfs/etc/group")
        users = {line.split(":", 1)[0] for line in _text(out_dir / "honeyfs/etc/passwd").splitlines() if line}
        if found is None or [name for name in sudo.split(",") if name] != system.sudo_users(group) \
                or set(name for name in sudo.split(",") if name) - users:
            bad("command_not_found_sudo", "is not the members of sudo and admin")
    login = firmware.login_env(persona)
    for key in ("login_path", "login_shell"):
        if value(key) != login.get(key):
            bad(key, f"is not the login of the persona ({login.get(key)!r})")
    if value("login_path") is not None and not all(fs is not None and tree.find_dir(fs, folder.strip("/")) is not None
                                                   for folder in value("login_path").split(":") if folder):
        bad("login_path", "names a folder the tree has not")
    if value("login_shell") is not None and not program(value("login_shell")):
        bad("login_shell", "is no program of the tree")


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
    # the history ends where the identity was drawn, the clock of the builders (now for a fresh one)
    first, last = _utc_date(ident.installed_at), _utc_date(drawn_at(ident))
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
    or rootfs on /."""
    if _text(honeyfs / "etc/fstab"):
        return
    if not (txtcmds / "bin/dmesg").exists():
        return  # a firmware without dmesg (the problems check names one that should have it)
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


def _top_style(cfg):
    return cfg.get("shell", "top_style", fallback="procps")


def _check_df(persona, ident, cfg, out_dir, runtime_dir, honeyfs, txtcmds, out):
    """df is the patched command over the persona's file system table (df.json, [shell]
    df_table): no txtcmd answers it, the table is the persona's, and every file system of it is
    in mount and /proc/mounts with its type; sizes fit (used and available within the size, the
    root of a server on the disk of its hardware) and awk is the persona's."""
    if cfg.get("shell", "df_table", fallback=None) != f"{runtime_dir}/{DF_TABLE}":
        out.append(f"df: cfg df_table is not {runtime_dir}/{DF_TABLE}")
    for key, value in system.awk_keys(persona, ident).items():
        if cfg.get("shell", key, fallback=None) != value:
            out.append(f"awk: cfg {key}: {cfg.get('shell', key, fallback=None)!r}, expected {value!r}")
    for directory in PATH_DIRS:
        if (txtcmds / directory / "df").is_file():
            out.append(f"df: a txtcmd /{directory}/df answers df, the table does not")
    try:
        found = json.loads(_text(out_dir / DF_TABLE))
    except ValueError:
        found = None
    rows = found.get("filesystems") if isinstance(found, dict) else None
    # the table of the persona's mount data (the file systems its builder mounts), not of its own rows;
    # an identity the builder cannot take (the uptime checks name it) keeps the check of the rows
    try:
        mounted_rows = persona.builder(persona, ident).filesystems
    except ValueError:
        mounted_rows = rows
    expected = system.df_table(persona, ident, mounted_rows)
    if not isinstance(rows, list) or found != json.loads(json.dumps(expected)):
        out.append("df: df.json is not the persona's")
        rows = rows if isinstance(rows, list) else []
    # df is a program of the coreutils or the BusyBox that wc, sort and seq are
    if (found or {}).get("style") != system.text_keys(persona, ident)["text_style"]:
        out.append(f"df: the {(found or {}).get('style')} df differs from the {system.text_keys(persona, ident)['text_style']} "
                   "wc, sort and seq")
    mounted = {(dev, mp, fs) for dev, mp, fs in _MOUNT.findall(_text(txtcmds / "bin/mount"))}
    proc = {tuple(line.split()[:3]) for line in _text(honeyfs / "proc/mounts").splitlines() if line.strip()}
    for row in rows:
        key = (row.get("source"), row.get("target"), row.get("fstype"))
        if key not in mounted:
            out.append(f"df: {key[0]} on {key[1]} ({key[2]}) is not in mount")
        if key not in proc:
            out.append(f"df: {key[0]} on {key[1]} ({key[2]}) is not in /proc/mounts")
        size, used, avail = row.get("size", 0), row.get("used", 0), row.get("avail", 0)
        if not (0 <= used <= size and 0 <= avail <= size - used):
            out.append(f"df: {key[1]} uses {used} and leaves {avail} of {size} KiB")
        if not 0 <= row.get("iused", 0) <= row.get("inodes", 0):
            out.append(f"df: {key[1]} uses {row.get('iused')} of {row.get('inodes')} inodes")
    roots = [row for row in rows if row.get("target") == "/" and row.get("size")]
    if persona.kind == "server" and [row["size"] for row in roots] != [ident.hardware.disk_kb]:
        out.append(f"df: the root of the server is not one disk of {ident.hardware.disk_kb} KiB")


def _check_dates(ident, honeyfs, out):
    first, last = _utc_date(ident.installed_at), _utc_date(drawn_at(ident))
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
        if len(fields) > 2 and fields[2].isdigit() and int(fields[2]) > drawn_at(ident) // DAY:
            out.append(f"date: shadow lastchange of {fields[0]} is after the draw")


def tree_texts(fs):
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
    paths = [out_dir / "cmdoutput.json", out_dir / kthreads.KERNEL_THREADS, out_dir / "cowrie.cfg", out_dir / BUSYBOX_HELP,
             out_dir / system.SERVICE_STATUS, out_dir / system.SYSTEMD_UNITS, out_dir / system.SUDO_LIST,
             out_dir / system.APT_PACKAGES, out_dir / system.PACKAGE_NAMES, out_dir / system.OPKG_HELP_FILE,
             out_dir / system.YUM_META, out_dir / system.RPM_OWNERS, out_dir / system.OPKG_META, out_dir / system.DNF_META,
             out_dir / system.RPM_MANUALS]
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
    for path, data in tree_texts(fs):
        for marker in found_in(data):
            hits.setdefault(marker, path)
    for marker, path in sorted(hits.items()):
        out.append(f"forbidden {marker} in fs.pickle ({path})")


def _skipped(cfg):
    return {e.strip() for e in cfg.get("shell", "skip_python_commands", fallback="").split(",") if e.strip()}


def _check_network(persona, ident, cfg, out_dir, runtime_dir, fs, out):
    """ip, ss, ifconfig and netstat read the persona's network ([shell] network): every program
    the network names answers and is on PATH, the others do neither; the interfaces have one MAC and
    one address each, the sockets belong to processes of ps, the AWS address is the host name's and
    QNAP's MAC ends in the host name."""
    if cfg.get("shell", "network", fallback=None) != f"{runtime_dir}/{network.NETWORK_FILE}":
        out.append(f"network: cfg network is not {runtime_dir}/{network.NETWORK_FILE}")
    try:
        data = json.loads(_text(out_dir / network.NETWORK_FILE))
        processes = json.loads(_text(out_dir / "cmdoutput.json"))["command"]["ps"]
    except (ValueError, KeyError, TypeError):
        out.append(f"network: {network.NETWORK_FILE} is no network")
        return
    tools = data.get("tools", {})
    for name in network.NET_TOOLS:
        if _answered(cfg, name) != (name in tools):
            out.append(f"network: {name} {'answers' if _answered(cfg, name) else 'does not answer'}, the network "
                       f"{'names' if name in tools else 'does not name'} it")
        if _answered(cfg, name) != _on_path(fs, name):
            out.append(f"network: {name} {'answers' if _answered(cfg, name) else 'does not answer'}, it is "
                       f"{'' if _on_path(fs, name) else 'not '}on PATH")
    macs = [i["mac"] for i in data.get("interfaces", [])]
    if len(set(macs)) != len(macs):
        out.append("network: two interfaces have one MAC")
    for iface in data.get("interfaces", []):
        if int(iface["mac"].split(":")[0], 16) & 1:
            out.append(f"network: the MAC of {iface['name']} is a multicast address")
        if iface.get("inet6") and iface["inet6"] != network.link_local(iface["mac"]):
            out.append(f"network: the link-local address of {iface['name']} is not that of its MAC")
    pids = {p["PID"] for p in processes}
    for sock in data.get("sockets", []):
        if sock["pid"] not in pids:
            out.append(f"network: the socket {sock['proto']} {sock['port']} belongs to no process of ps")
    main = data["interfaces"][0]
    if ident.hardware.provider == "aws" and main["inet"] != ident.hostname[3:].replace("-", "."):
        out.append(f"network: {main['inet']} is not the address of the host name {ident.hostname}")
    if persona.family == "qnap" and main["mac"].replace(":", "")[-6:] != ident.hostname[3:].lower():
        out.append(f"network: the MAC {main['mac']} does not end in the host name {ident.hostname}")


def _check_text_tools(persona, ident, cfg, fs, txtcmds, out):
    """wc, sort and seq are the patched commands of the persona's coreutils or BusyBox ([shell]
    text_*): they answer exactly where they are programs on PATH, no txtcmd answers them."""
    for key, value in system.text_keys(persona, ident).items():
        if cfg.get("shell", key, fallback=None) != value:
            out.append(f"text tools: cfg {key}: {cfg.get('shell', key, fallback=None)!r}, expected {value!r}")
    present = system.text_programs(persona, ident)
    for name in system.TEXT_TOOLS:
        if _answered(cfg, name) != _on_path(fs, name):
            out.append(f"text tools: {name} {'answers' if _answered(cfg, name) else 'does not answer'}, "
                       f"it is {'' if _on_path(fs, name) else 'not '}on PATH")
        if _answered(cfg, name) != (name in present):
            out.append(f"text tools: {name} {'answers' if _answered(cfg, name) else 'does not answer'}, the "
                       f"system {'has' if name in present else 'has no'} {name}")
        for directory in PATH_DIRS:
            if (txtcmds / directory / name).is_file():
                out.append(f"text tools: a txtcmd /{directory}/{name} answers {name}")


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
        name = Path(rel).name
        python = (name in PYTHON_PACKAGE_MANAGERS and _answered(cfg, name)
                  and PYTHON_PACKAGE_MANAGER_AT.get(name, rel) == rel)
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


def _check_programs(fs, txtcmds, out, builtins=frozenset()):
    """Every program in a bin / sbin directory has a size, every txtcmd is a program
    without contents (Cowrie's cat / dd serve the ELF header of [shell] arch); the txtcmd
    bin/<name> of a builtin of the firmware's shell ([shell] builtins) needs no program."""
    empty = [path for path, node in tree.iter_nodes(fs)
             if path.rsplit("/", 2)[-2] in ("bin", "sbin") and _is_program(node)
             and not tree.node_get(node, tree.A_SIZE)]
    if empty:
        out.append(f"program: size 0 for {', '.join(empty[:5])}")
    for path in sorted(txtcmds.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(txtcmds))
        if rel.startswith("bin/") and rel[4:] in builtins and tree.resolve(fs, rel) is None:
            continue
        # a txtcmd of a link (an applet of BusyBox) is the program it names
        node = tree.resolve(fs, rel)
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
    """Every link of the tree names a node (the links of its directories followed) and has the length
    of its target as its size: ls -l and cd find what a link points to."""
    dangling, sized = [], []
    for path, node in tree.iter_nodes(fs):
        if tree.node_get(node, tree.A_TYPE) != tree.T_LINK:
            continue
        target = tree.node_get(node, tree.A_TARGET) or ""
        resolved = tree.target_path(fs, path) if target else None
        if resolved is None or tree.find_node(fs, resolved) is None:
            dangling.append(f"{path} -> {target}")
        # ls -l shows the length of the target as the size of a link
        if tree.node_get(node, tree.A_SIZE) != len(target):
            sized.append(f"{path} -> {target} ({tree.node_get(node, tree.A_SIZE)})")
    if dangling:
        out.append(f"link: {len(dangling)} links name nothing: {', '.join(dangling[:4])}")
    if sized:
        out.append(f"link: {len(sized)} links show a size other than the length of their target: "
                   f"{', '.join(sized[:4])}")


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
            is_link = tree.node_get(node, tree.A_TYPE) == tree.T_LINK
            target = tree.target_path(fs, link) if is_link else None
            if is_link and (target is None or tree.find_node(fs, target) is None):
                # a link that names nothing: _check_links reports it
                found.add((level, sign, name))
                continue
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
    # the programs of a firmware image are its own (the adduser applet of the Archer C7's BusyBox)
    own |= {"/" + rel for rel in firmware.programs(persona, ident) or {}}
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
            if not unit.get("path"):
                continue  # system.slice: no unit file
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
    own = firmware.programs(persona)
    if (txtcmds / "bin/dmesg").exists():
        banner = re.search(r"\bLinux version (\S+)", _text(txtcmds / "bin/dmesg"))
        if not banner or banner.group(1) != r.kernel_version:
            out.append(f"kernel: dmesg does not name {r.kernel_version}")
    elif own is None or "bin/dmesg" in own:
        out.append("kernel: no dmesg")
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
    _check_top(persona, cfg, txtcmds, out, out_dir, runtime_dir)
    _check_boot(honeyfs, txtcmds, out)
    if persona.kind == "device":
        _check_device_boot(honeyfs, txtcmds, out)
    _check_df(persona, ident, cfg, out_dir, runtime_dir, honeyfs, txtcmds, out)
    _check_text_tools(persona, ident, cfg, fs, txtcmds, out)
    _check_network(persona, ident, cfg, out_dir, runtime_dir, fs, out)

    os_release = _text(honeyfs / "etc/os-release")
    fields = dict(line.split("=", 1) for line in os_release.splitlines() if "=" in line)
    # firmware of routers, cameras and NAS vendors has no os-release
    if (os_release or persona.kind != "device") and fields.get("ID", "").strip().strip('"') != persona.os_id:
        out.append(f"os-release ID is not {persona.os_id}")
    if "VERSION_ID" in fields and fields["VERSION_ID"].strip().strip('"') != r.os_version_id:
        out.append(f"os-release VERSION_ID is not {r.os_version_id}")

    _check_processes(persona, ident, out_dir / "cmdoutput.json", out)
    _check_accounts(persona, out_dir, out)
    _check_dates(ident, honeyfs, out)
    _check_logs(ident, honeyfs, fs, out)
    _check_forbidden(persona, out_dir, runtime_dir, keys_dir, fs, out)
    _check_busybox(ident, cfg, out_dir, runtime_dir, fs, out)
    if fs is not None:
        _check_tree(persona, ident, fs, out)
        _check_package_managers(persona, cfg, fs, honeyfs, txtcmds, out)
        _check_programs(fs, txtcmds, out, frozenset(
            name.strip() for name in cfg.get("shell", "builtins", fallback="").split(",") if name.strip()))
        _check_system(persona, ident, cfg, out_dir, runtime_dir, fs, out)
        _check_shell_keys(persona, ident, cfg, out_dir, runtime_dir, fs, out)
        _check_program_owners(persona, ident, out_dir, fs, out)
        _check_links(fs, out)
        _check_init_links(persona, fs, out)
    return out


def validate(persona, ident, out_dir, runtime_dir, keys_dir, min_pickle=MIN_PICKLE_SIZE):
    found = problems(persona, ident, out_dir, runtime_dir, keys_dir, min_pickle)
    if found:
        raise PersonaError(f"{persona.id}: " + "; ".join(found))
