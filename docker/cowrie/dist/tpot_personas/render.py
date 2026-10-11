"""Render a persona + identity into the files Cowrie reads."""
import copy
import fnmatch
import json
import os
import posixpath
import random
import shutil
from pathlib import Path

from . import system, tree
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
        commands |= set(EMBEDDED_SKIP_PYTHON_COMMANDS)
    if not content.busybox_help:
        commands |= set(BUSYBOX_PYTHON_COMMANDS)
    return sorted(commands)


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
    no x86_64 names on another architecture."""
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
    if persona.hardware_platform != "x86_64":
        remove_matching(t, ("x86_64", "x86-64"))


def program_size(ident, rel):
    low, high = PROGRAM_SIZES.get(Path(rel).name, PROGRAM_SIZE)
    return rng_for(ident, f"size {rel}").randint(low, high)


def _mode(rel):
    if rel == "etc/shadow":
        return 0o640
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
    for rel in sorted(set(content.txtcmds) | set(content.programs) | set(system.programs(persona, ident))):
        tree.ensure_executable(t, rel, program_size(ident, rel), ctime=ident.installed_at)
    for rel, target in system.python_links(persona, ident):
        tree.ensure_link(t, rel, target, ctime=ident.installed_at)
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
    _write(out_dir / "cowrie.cfg", render_cfg(persona, ident, Path(runtime_dir), Path(keys_dir), cowrie_root,
                                              content))
    _write(out_dir / "identity.json", json.dumps(to_json(ident), indent=2, sort_keys=True, default=str) + "\n")
