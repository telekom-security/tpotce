"""Render a persona + identity into the files Cowrie reads."""
import copy
import fnmatch
import json
import os
import posixpath
import random
import re
import shutil
from pathlib import Path

from . import servers, system, tree
from .common import home_dir, rng_for
from .identity import DAY, to_json
from .ssh_profiles import PROFILES

COWRIE_ROOT = Path("/home/cowrie/cowrie")

# Upstream parts that name another system (or Cowrie's own "phil"); the persona
# writes its own release files back.
COMMON_REMOVE_PATHS = system.CONTAINER_PATHS + (
    "home/phil",
    "home/ubuntu",
    "etc/lsb-release",
    "etc/redhat-release",
    "etc/fedora-release",
    "etc/openwrt_release",
    "etc/openwrt_version",
    "etc.defaults/VERSION",
    "etc/config/uLinux.conf",
    "etc/default_config/uLinux.conf",
    "etc_ro/version",
    "etc_ro/product.ini",
    "etc/ubnt/version",
    "etc/zyxel/model",
    "firmware/mnt/info/fwversion",
)

# Only the package manager of the persona may exist (as a txtcmd).
PACKAGE_MANAGER_PATHS = (
    "bin/apt",
    "bin/apt-get",
    "bin/dnf",
    "bin/opkg",
    "bin/rpm",
    "bin/yum",
    "sbin/opkg",
    "sbin/qpkg_cli",
    "usr/bin/apt",
    "usr/bin/apt-get",
    "usr/bin/dnf",
    "usr/bin/dpkg",
    "usr/bin/opkg",
    "usr/bin/rpm",
    "usr/bin/yum",
    "usr/sbin/opkg",
    "usr/sbin/qpkg_cli",
    "usr/syno/bin/synopkg",
)

# Commands whose presence differs a lot between systems: removed, the persona adds its own
# (Perl and Python of Cowrie's tree: tpot_personas.system).
HIGH_VARIANCE_COMMAND_PATHS = (
    "bin/busybox",
    "usr/bin/gcc",
    "usr/bin/git",
    "usr/bin/lspci",
    "usr/bin/python",
    "usr/bin/sudo",
    "usr/sbin/service",
)

COMMON_DIRS = (
    "bin",
    "dev",
    "etc",
    "home",
    "lib",
    "proc",
    "root",
    "sbin",
    "tmp",
    "usr",
    "usr/bin",
    "usr/sbin",
    "var",
    "var/log",
)
DIR_MODES = {"proc": 0o555, "root": 0o700, "tmp": 0o1777}

COMMON_SKIP_PYTHON_COMMANDS = (
    "/bin/apt",
    "/bin/apt-get",
    "/bin/yum",
    "/usr/bin/apt",
    "/usr/bin/apt-get",
    "/usr/bin/gcc",
    "/usr/bin/lspci",
    "/usr/bin/sudo",
    "/usr/bin/yum",
    "/usr/sbin/service",
    "apt",
    "apt-get",
    "gcc",
    "lspci",
    "sudo",
    "yum",
    "service",
)
EMBEDDED_SKIP_PYTHON_COMMANDS = (
    "adduser",
    "finger",
    "git",
    "groups",
    "perl",
    "scp",
    "systemctl",
)
# Cowrie's busybox runs the applets: only on a system with BusyBox (Content.busybox_help)
BUSYBOX_PYTHON_COMMANDS = ("/bin/busybox", "busybox")
KEY_FILES = {"ssh-rsa": "rsa", "ecdsa-sha2-nistp256": "ecdsa", "ssh-ed25519": "ed25519"}
BUSYBOX_HELP = "busybox-help.txt"
# what ls -l shows for a program Cowrie runs (the pickle keeps the size of one it has):
# BusyBox of a firmware, any other program
PROGRAM_SIZES = {"busybox": (380_000, 1_100_000), "git": (2_500_000, 4_500_000), "python2.7": (7_000, 7_400)}
PROGRAM_SIZE = (18_000, 420_000)


def skip_python_commands(persona, ident, content):
    commands = set(COMMON_SKIP_PYTHON_COMMANDS) | set(system.python_commands(persona, ident))
    # a server runs Cowrie's sudo, service and its package manager (F7)
    for name in system.python_answers(persona):
        commands -= {name, f"/bin/{name}", f"/{system.command_path(name)}"}
    if persona.kind == "device":
        commands |= set(EMBEDDED_SKIP_PYTHON_COMMANDS) - ({"perl"} if system.has_perl(persona) else set())
    # the tools of packages the server has not installed
    commands |= set(system.skipped_tools(persona, ident))
    if not content.busybox_help:
        commands |= set(BUSYBOX_PYTHON_COMMANDS)
    return sorted(commands)


# links of Cowrie's pickle whose real target the rule of real_target does not give
REAL_TARGETS = {"etc/os-release": "../usr/lib/os-release", "etc/mtab": "../proc/self/mounts",
                "proc/mounts": "self/mounts", "proc/net": "self/net", "dev/fd": "/proc/self/fd"}


def systemd_prefix(persona, ident):
    """Where the units of the release are (/lib/systemd before the /usr merge of its units), "" without systemd."""
    if persona.kind == "server":
        return servers.unit_dir(persona, ident).rsplit("/system", 1)[0]
    return {"ubiquiti": "/lib/systemd", "synology": "/usr/lib/systemd"}.get(persona.family, "")


def real_target(link, target, systemd="", el=False):
    """The target a real system has for a link (link: its real path; target: from the root, as
    Cowrie's pickle stores it): relative within the directory of the link and in the run level
    directories, absolute elsewhere; units under the unit directory of the release."""
    if not target or target.startswith("/"):
        return target
    link = link.strip("/")
    if el and link == "etc/localtime":
        # timedatectl of Red Hat links the zone relatively
        return "../" + target
    if link in REAL_TARGETS:
        return REAL_TARGETS[link]
    directory = posixpath.dirname(link)
    if systemd and link.startswith("etc/systemd/") and target.startswith("usr/lib/systemd/"):
        return systemd + target[len("usr/lib/systemd"):]
    if link == "usr/lib64/ld-linux-x86-64.so.2" and target.startswith("usr/lib/"):
        return "/lib/" + target[len("usr/lib/"):]
    if not directory or target.startswith(directory + "/") or fnmatch.fnmatchcase(posixpath.basename(directory),
                                                                                  "rc?.d"):
        return posixpath.relpath("/" + target, "/" + directory)
    return "/" + target


def real_links(t, persona, ident):
    """Every link of Cowrie's pickle with the target of a real system (real_target)."""
    systemd = systemd_prefix(persona, ident)
    for path, node in tree.iter_nodes(t):
        if tree.node_get(node, tree.A_TYPE) == tree.T_LINK:
            target = tree.node_get(node, tree.A_TARGET) or ""
            if not target and re.fullmatch(r"/proc/\d+/(cwd|root)", path):
                # captured without a destination: init runs in / (the shell's: _login_links)
                target = "/"
            target = real_target(path, target, systemd, system.is_el(persona))
            tree.node_set(node, tree.A_TARGET, target)
            tree.node_set(node, tree.A_SIZE, len(target))


def link(t, persona, ident, rel, target, ctime):
    """A link of the persona; target from the root ("usr/bin/python3.12") or absolute."""
    real = tree.real_path(t, rel) or rel
    return tree.ensure_link(t, rel, real_target(real, target, systemd_prefix(persona, ident), system.is_el(persona)),
                            ctime=ctime)


def remove_paths(t, paths):
    """Remove the paths; a glob pattern in the last part removes every match in its directory."""
    for rel in paths:
        parent_rel, name = posixpath.split(rel)
        if not any(c in name for c in "*?["):
            tree.remove_node(t, rel)
            continue
        parent = tree.find_dir(t, parent_rel)
        if parent is not None:
            children = tree.node_children(parent)
            children[:] = [c for c in children if not fnmatch.fnmatchcase(tree.node_get(c, tree.A_NAME), name)]


def rename_parts(t, replacements):
    """Replace text in every name and link target (python3.11 -> python3.12)."""
    for _, node in list(tree.iter_nodes(t)):
        for index in (tree.A_NAME, tree.A_TARGET):
            value = tree.node_get(node, index)
            if isinstance(value, str):
                for old, new in replacements:
                    value = value.replace(old, new)
                tree.node_set(node, index, value)


def remove_matching(t, words):
    """Remove every node whose name or link target holds one of the words (x86_64 on ARM)."""
    def drop(node):
        values = (tree.node_get(node, tree.A_NAME), tree.node_get(node, tree.A_TARGET))
        return any(isinstance(v, str) and any(w in v for w in words) for v in values)

    stack = [t]
    while stack:
        node = stack.pop()
        children = tree.node_children(node)
        children[:] = [c for c in children if not drop(c)]
        stack += [c for c in children if tree.node_get(c, tree.A_TYPE) == tree.T_DIR]


def adapt_system(t, persona, ident):
    """Cowrie's Debian 12 x86_64 container tree as the persona's system (tpot_personas.system):
    foreign paths removed, the multiarch directory and Python named as on that system,
    no x86_64 names on another architecture and no names of Debian's own packages without dpkg."""
    remove_paths(t, system.foreign_paths(persona))
    triplet = system.triplet(persona)
    replacements = [(system.MULTIARCH, triplet)] if triplet and triplet != system.MULTIARCH else []
    python = system.tools(persona, ident).get("python")
    name = system.python3_name(python) if python else None
    if name:
        version = name[len("python"):]
        replacements += [(f"{tool}3.11", f"{tool}{version}") for tool in ("python", "pydoc", "pdb", "pygettext")]
        replacements += [("cpython-311", f"cpython-{version.replace('.', '')}"), ("config-3.11-", f"config-{version}-")]
    if replacements:
        rename_parts(t, replacements)
    words = system.foreign_words(persona)
    if words:
        remove_matching(t, words)


def program_size(ident, rel, sizes=PROGRAM_SIZES):
    low, high = sizes.get(Path(rel).name, PROGRAM_SIZE)
    return rng_for(ident, f"size {rel}").randint(low, high)


def _unit_files(t, persona, ident, processes):
    """A unit file for every unit systemctl shows (Cowrie's tree has those of its container), the link
    of the target that wants an enabled one."""
    for unit in servers.systemd_units(persona, ident, processes)["units"]:
        path = unit["path"]
        if not path.startswith(("/usr/lib/systemd/", "/lib/systemd/")):
            continue
        rel = "usr" + path if path.startswith("/lib/") else path.lstrip("/")
        if tree.find_node(t, rel) is None:
            tree.ensure_file(t, rel, servers.unit_file(persona, ident, unit).encode(), ctime=ident.installed_at)
        # the programs the unit runs
        text = tree.node_get(tree.find_node(t, rel), tree.A_CONTENTS)
        for line in (text.decode("utf-8", "replace").splitlines() if isinstance(text, bytes) else ()):
            program = system.exec_program(line) if line.startswith("ExecStart=") else ""
            if program and system.program_node(t, program) is None:
                rel_program = program.lstrip("/") if program.startswith("/") else f"usr/bin/{program}"
                tree.ensure_executable(t, rel_program, program_size(ident, rel_program), ctime=ident.installed_at)
        if unit["state"] == "enabled":
            tree.ensure_link(t, f"etc/systemd/system/{servers.wants_target(unit['name'])}.wants/{unit['name']}", path,
                             ctime=ident.installed_at)


def _login_links(t, persona, ident, content_group=""):
    """The standard streams and /proc/self of the login shell: fd 0-2 on its terminal, exe its
    program (Cowrie's tree has the pipes and the Python of the container there)."""
    own = tree.real_path(t, "proc/self/fd")
    if tree.find_dir(t, "proc/self") is None or own is None:
        return
    fd = tree.find_dir(t, own)
    mode = tree.node_get(fd, tree.A_MODE) if fd is not None else None
    # devpts gives the terminal to the tty group where the system has one (OpenWrt has none: root, 0600)
    tty = "\ntty:x:5:" in "\n" + content_group
    tree.ensure_device(t, system.TERMINAL, persona.uid, 5 if tty else 0, 0o620 if tty else 0o600, ctime=ident.now)
    for number in range(3):
        link(t, persona, ident, f"{own}/{number}", system.TERMINAL, ident.now)
    if mode is not None:
        tree.node_set(tree.find_dir(t, own), tree.A_MODE, mode)
    for rel, target in system.STREAM_LINKS:
        link(t, persona, ident, rel, target, ident.installed_at)
    process = own.rsplit("/", 1)[0]
    link(t, persona, ident, f"{process}/exe", system.login_program(persona, ident), ident.now)
    # the shell runs in the home directory of the user, its root is /
    link(t, persona, ident, f"{process}/cwd", home_dir(persona), ident.now)
    link(t, persona, ident, f"{process}/root", "/", ident.now)
    # /proc/thread-self: the one thread of the shell
    tree.ensure_dir(t, f"{process}/task/{process.rsplit('/', 1)[-1]}", mode=0o555, ctime=ident.now)


def _mode(rel):
    if rel == "etc/shadow":
        return 0o640
    if rel == "etc/sudoers" or rel.startswith("etc/sudoers.d/"):
        return 0o440
    if rel.startswith(("etc/rc.d/init.d/", "etc/init.d/")) and rel.rsplit("/", 1)[-1] not in ("README", "functions"):
        return 0o755
    return 0o755 if rel.startswith(("bin/", "sbin/", "usr/bin/", "usr/sbin/")) else 0o644


def _set_dirs(t, persona, ctime):
    """Owner and mode of the directories that are not 0:0 0755. Runs after the
    files: tree.ensure_file resets every parent directory to 0:0 0755."""
    for rel in COMMON_DIRS:
        tree.ensure_dir(t, rel, 0, 0, DIR_MODES.get(rel, 0o755), ctime=ctime)
    home = home_dir(persona)
    node = tree.ensure_dir(t, home, persona.uid, persona.gid, 0o700 if home == "root" else 0o755, ctime=ctime)
    for _, child in tree.iter_nodes(node):
        tree.node_set(child, tree.A_UID, persona.uid)
        tree.node_set(child, tree.A_GID, persona.gid)


def apply_to_tree(base_tree, persona, ident, content):
    rng = random.Random(ident.seed ^ 0x5EED)
    t = copy.deepcopy(base_tree)
    real_links(t, persona, ident)
    for rel in COMMON_REMOVE_PATHS + persona.remove_paths + PACKAGE_MANAGER_PATHS + HIGH_VARIANCE_COMMAND_PATHS:
        tree.remove_node(t, rel)
    adapt_system(t, persona, ident)
    # the system was installed at installed_at: upstream ctimes (2013 ...) move there
    tree.node_set(t, tree.A_CTIME, ident.installed_at - rng.randint(0, 30 * DAY))
    for _, node in tree.iter_nodes(t):
        tree.node_set(node, tree.A_CTIME, ident.installed_at - rng.randint(0, 30 * DAY))
    for rel, text in content.files.items():
        ctime = rng.randint(ident.installed_at, ident.booted_at)
        # logs and /etc/shadow were last written at their newest entry
        ctime = content.ctimes.get(rel, ctime)
        node = tree.ensure_file(t, rel, text.encode("utf-8"), 0, 0, _mode(rel), ctime=ctime)
        # ensure_file keeps the ctime of a node Cowrie's pickle already has (/etc/shadow, logs)
        tree.node_set(node, tree.A_CTIME, ctime)
    # txtcmds and the commands of Cowrie's Python: programs with a size and no contents
    # (cat / dd give the ELF header of [shell] arch)
    sizes = {**PROGRAM_SIZES, **system.program_sizes(persona)}
    tools, tool_links = system.tool_programs(persona, ident)
    for rel in sorted(set(content.txtcmds) | set(content.programs) | set(system.programs(persona, ident)) | set(tools)):
        tree.ensure_executable(t, rel, program_size(ident, rel, sizes), ctime=ident.installed_at)
    for rel in system.directories(persona):
        tree.ensure_dir(t, rel, ctime=ident.installed_at)
    if persona.kind == "server":
        # Red Hat's sudo package keeps sudoers.d closed
        tree.ensure_dir(t, "etc/sudoers.d", mode=0o750 if system.is_el(persona) else 0o755, ctime=ident.installed_at)
    removed, ssh_links = system.ssh_units(persona, ident)
    for rel in removed:
        tree.remove_node(t, rel)
    for rel, target in system.python_links(persona, ident) + system.links(persona) + ssh_links + tool_links:
        link(t, persona, ident, rel, target, ident.installed_at)
    if persona.kind == "server":
        _unit_files(t, persona, ident, content.processes)
        # the daemons of ps are programs of the tree
        for process in content.processes:
            program = process["COMMAND"].split()[0]
            if program.startswith("/") and tree.find_node(t, program) is None:
                tree.ensure_executable(t, program.lstrip("/"), program_size(ident, program), ctime=ident.installed_at)
    _login_links(t, persona, ident, content.files.get("etc/group", ""))
    _set_dirs(t, persona, ident.installed_at)
    return t


def render_cfg(persona, ident, runtime_dir, keys_dir, cowrie_root=COWRIE_ROOT, content=None):
    r = ident.release
    content = content if content is not None else persona.builder(persona, ident)
    busybox = f"busybox_help = {runtime_dir}/{BUSYBOX_HELP}\n" if content.busybox_help else ""
    tools = "".join(f"{key} = {value}\n"
                    for key, value in system.shell_keys(persona, ident, content.files, runtime_dir).items())
    profile = PROFILES[persona.ssh_profile]
    keys = "\n".join(
        f"{KEY_FILES[k]}_public_key = {keys_dir}/ssh_host_{KEY_FILES[k]}_key.pub\n"
        f"{KEY_FILES[k]}_private_key = {keys_dir}/ssh_host_{KEY_FILES[k]}_key"
        for k in profile.host_keys)
    return f"""[honeypot]
hostname = {ident.hostname}
log_path = {cowrie_root}/log
logtype = plain
download_path = {cowrie_root}/dl
state_path = /tmp/cowrie/data
etc_path = {keys_dir}
contents_path = {runtime_dir}/honeyfs
txtcmds_path = {runtime_dir}/txtcmds
ttylog = true
ttylog_path = {cowrie_root}/log/tty
interactive_timeout = 180
idle_timeout = 180
authentication_timeout = 120
boot_offset = {ident.boot_offset}
backend = shell
timezone = UTC
auth_class = AuthRandom
auth_class_parameters = 2, 5, 10

[shell]
filesystem = {runtime_dir}/fs.pickle
processes = {runtime_dir}/cmdoutput.json
arch = {persona.arch}
kernel_version = {r.kernel_version}
kernel_build_string = {r.kernel_build}
hardware_platform = {persona.hardware_platform}
operating_system = {persona.operating_system}
prompt_style = {persona.prompt_style}
shell_name = {system.shell_style(persona)[0]}
not_found_style = {system.shell_style(persona)[1]}
ssh_version = {r.ssh_version}
skip_python_commands = {",".join(skip_python_commands(persona, ident, content))}
{busybox}{tools}
[ssh]
enabled = true
{keys}
public_key_auth = {",".join(profile.host_keys)}
version = {r.ssh_banner}
ciphers = {",".join(profile.ciphers)}
macs = {",".join(profile.macs)}
compression = {",".join(profile.compression)}
listen_endpoints = tcp:22:interface=0.0.0.0
sftp_enabled = true
forwarding = false
forward_redirect = false
forward_tunnel = false
auth_none_enabled = false
auth_keyboard_interactive_enabled = true
auth_publickey_allow_any = true

[telnet]
enabled = true
listen_endpoints = tcp:23:interface=0.0.0.0
reported_port = 23
cve_2026_24061_vulnerable = true

[output_jsonlog]
enabled = true
logfile = {cowrie_root}/log/cowrie.json
epoch_timestamp = false

[output_textlog]
enabled = false
logfile = {cowrie_root}/log/cowrie-textlog.log
format = text
"""


def runtime_json(persona, ident, processes):
    """The JSON files of a server's runtime folder: the units of systemctl, the rules of sudo -l,
    the packages of apt (Debian)."""
    files = {system.SYSTEMD_UNITS: servers.systemd_units(persona, ident, processes),
             system.SUDO_LIST: system.sudo_list(persona)}
    if system.has_dpkg(persona):
        files[system.APT_PACKAGES] = system.apt_packages(persona, ident)
    return files


def _write(path, text, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.chmod(path, mode)


def render(persona, ident, base_tree, out_dir, runtime_dir, keys_dir, cowrie_root=COWRIE_ROOT):
    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    content = persona.builder(persona, ident)
    tree.dump_pickle(apply_to_tree(base_tree, persona, ident, content), out_dir / "fs.pickle")
    for rel, text in content.files.items():
        _write(out_dir / "honeyfs" / rel, text, _mode(rel))
    (out_dir / "honeyfs").mkdir(exist_ok=True)
    for rel, text in content.txtcmds.items():
        _write(out_dir / "txtcmds" / rel, text)
    (out_dir / "txtcmds").mkdir(exist_ok=True)
    _write(out_dir / "cmdoutput.json",
           json.dumps({"command": {"ps": content.processes}}, indent=2, sort_keys=True) + "\n")
    if content.busybox_help:
        _write(out_dir / BUSYBOX_HELP, content.busybox_help)
    if persona.kind == "server":
        _write(out_dir / system.SERVICE_STATUS, system.service_status(persona))
        for name, data in runtime_json(persona, ident, content.processes).items():
            _write(out_dir / name, json.dumps(data, indent=1, sort_keys=True) + "\n")
    _write(out_dir / "cowrie.cfg", render_cfg(persona, ident, Path(runtime_dir), Path(keys_dir), cowrie_root,
                                              content))
    _write(out_dir / "identity.json", json.dumps(to_json(ident), indent=2, sort_keys=True, default=str) + "\n")
