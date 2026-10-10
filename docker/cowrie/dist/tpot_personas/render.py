"""Render a persona + identity into the files Cowrie reads."""
import copy
import json
import os
import random
import shutil
from pathlib import Path

from . import tree
from .common import home_dir
from .identity import DAY, to_json
from .ssh_profiles import PROFILES

COWRIE_ROOT = Path("/home/cowrie/cowrie")

# Upstream parts that name another system (or Cowrie's own "phil"); the persona
# writes its own release files back.
COMMON_REMOVE_PATHS = (
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

# Commands whose presence differs a lot between systems: removed, the persona adds its own.
HIGH_VARIANCE_COMMAND_PATHS = (
    "bin/busybox",
    "usr/bin/gcc",
    "usr/bin/git",
    "usr/bin/lspci",
    "usr/bin/perl",
    "usr/bin/python",
    "usr/bin/python3",
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
    "/bin/busybox",
    "/bin/yum",
    "/usr/bin/apt",
    "/usr/bin/apt-get",
    "/usr/bin/busybox",
    "/usr/bin/gcc",
    "/usr/bin/lspci",
    "/usr/bin/sudo",
    "/usr/bin/yum",
    "/usr/sbin/service",
    "apt",
    "apt-get",
    "busybox",
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
    "python",
    "python3",
    "scp",
    "systemctl",
)
KEY_FILES = {"ssh-rsa": "rsa", "ecdsa-sha2-nistp256": "ecdsa", "ssh-ed25519": "ed25519"}


def skip_python_commands(persona):
    commands = set(COMMON_SKIP_PYTHON_COMMANDS)
    if persona.kind == "device":
        commands |= set(EMBEDDED_SKIP_PYTHON_COMMANDS)
    return sorted(commands)


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
    for rel in content.txtcmds:
        tree.ensure_file(t, rel, b"", 0, 0, 0o755, ctime=ident.installed_at)
    _set_dirs(t, persona, ident.installed_at)
    return t


def render_cfg(persona, ident, runtime_dir, keys_dir, cowrie_root=COWRIE_ROOT):
    r = ident.release
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
skip_python_commands = {",".join(skip_python_commands(persona))}

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
    _write(out_dir / "cowrie.cfg", render_cfg(persona, ident, Path(runtime_dir), Path(keys_dir), cowrie_root))
    _write(out_dir / "identity.json", json.dumps(to_json(ident), indent=2, sort_keys=True, default=str) + "\n")
