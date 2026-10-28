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

from . import devices, firmware, kthreads, network, package_units, servers, system, tree
from .common import home_dir, is_login_shell, rng_for, top_keys
from .identity import DAY, drawn_at, to_json
from .ssh_profiles import PROFILES, ext_info_s, host_key_algorithms, kex_algorithms, server_sig_algs

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

# Login pairs that never log in, for every persona: [honeypot] auth_refused_pairs, which the AuthRandom
# of the patch script (patch_auth) reads. A pair matches exactly (login and password, case and bytes as
# sent); a refused attempt is logged as cowrie.login.failed and leaves AuthRandom's state as it is.
# AuthRandom decides every other pair, a near one too (admin with another password). Keep the list to
# exact pairs.
REFUSED_LOGINS = (
    ("admin", "definitely_not_valid_creds"),
    ("honeypot", "indexer"),
    ("honeypotter", "imaginegettingindexed"),
    ("xXhoneypotXx", "P@ssw0rd1337!"),
    ("youjustgotindexed", "getindexedretard"),
    ("notachancethisisreal", "nopasswordforme73baby"),
)


def refused_logins_value(pairs=REFUSED_LOGINS) -> str:
    """The value of [honeypot] auth_refused_pairs: one line of JSON, a list of [login, password]; a $ is
    written as $$ for Cowrie's ExtendedInterpolation."""
    return json.dumps([list(pair) for pair in pairs]).replace("$", "$$")


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
    "/bin/dnf",
    "/bin/dnf-3",
    "/bin/rpm",
    "/bin/yum",
    "/usr/bin/apt",
    "/usr/bin/apt-get",
    "/usr/bin/dnf",
    "/usr/bin/dnf-3",
    "/usr/bin/gcc",
    "/usr/bin/lspci",
    "/usr/bin/rpm",
    "/usr/bin/sudo",
    "/usr/bin/yum",
    "/usr/sbin/service",
    "chkconfig",
    "/usr/sbin/chkconfig",
    "/bin/opkg",
    "opkg",
    "/usr/bin/dpkg",
    "/bin/dpkg",
    "dpkg",
    "/usr/bin/dpkg-query",
    "/bin/dpkg-query",
    "dpkg-query",
    "/usr/bin/apt-cache",
    "/bin/apt-cache",
    "apt-cache",
    "apt",
    "apt-get",
    "dnf",
    "dnf-3",
    "gcc",
    "lspci",
    "rpm",
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
# Cowrie's ssh: not on a system without an SSH server (system.SSH_CLIENT_PATHS)
SSH_CLIENT_COMMANDS = ("ssh", "/bin/ssh", "/usr/bin/ssh")
# Cowrie's busybox runs the applets: only on a system with BusyBox (Content.busybox_help)
BUSYBOX_PYTHON_COMMANDS = ("/bin/busybox", "busybox")
KEY_FILES = {"ssh-rsa": "rsa", "ecdsa-sha2-nistp256": "ecdsa", "ssh-ed25519": "ed25519"}
BUSYBOX_HELP = "busybox-help.txt"
# the folder of each program of a device with folders of its own ([shell] which_dirs)
PROGRAM_DIRS = "program-dirs.txt"
# the file systems of the patched df ([shell] df_table, every persona)
DF_TABLE = "df.json"
# what ls -l shows for a program Cowrie runs (the pickle keeps the size of one it has):
# BusyBox of a firmware, any other program
#   (kmodloader of ubox: 16393 bytes in the aarch64 images of OpenWrt 21.02 / 22.03; unverified for MIPS)
PROGRAM_SIZES = {"busybox": (380_000, 1_100_000), "git": (2_500_000, 4_500_000), "python2.7": (7_000, 7_400),
                 "kmodloader": (15_000, 22_000)}
PROGRAM_SIZE = (18_000, 420_000)


def skip_python_commands(persona, ident, content):
    commands = set(COMMON_SKIP_PYTHON_COMMANDS) | set(system.python_commands(persona, ident))
    # a server runs Cowrie's sudo, service and its package manager (F7)
    for name in system.python_answers(persona):
        commands -= {name, f"/bin/{name}", f"/{system.command_path(name)}"}
    if persona.kind == "device":
        commands |= set(EMBEDDED_SKIP_PYTHON_COMMANDS) - ({"perl"} if system.has_perl(persona) else set()) \
            - set(system.python_answers(persona))
    if not system.has_systemd(persona):
        commands |= {"journalctl", "/bin/journalctl", "/usr/bin/journalctl"}
    # the tools of packages the server has not installed
    commands |= set(system.skipped_tools(persona, ident))
    # wc, sort and seq where the firmware's BusyBox has no such applet; rm, cp, mv, touch, rmdir and mkdir too
    present = system.text_programs(persona, ident)
    commands |= {spelling for name in system.TEXT_TOOLS if name not in present
                 for spelling in (name, f"/bin/{name}", f"/usr/bin/{name}")}
    commands |= {spelling for name in system.missing_fs_tools(persona, ident)
                 for spelling in (name, f"/bin/{name}", f"/usr/bin/{name}")}
    # Cowrie's commands and the persona's texts named like an applet the firmware's BusyBox has not (df,
    #   head, env and dmesg of the DIR-859)
    commands |= {spelling for name in system.missing_applet_commands(persona, ident)
                 + system.missing_applet_txtcmds(persona, ident)
                 for spelling in (name, f"/bin/{name}", f"/usr/bin/{name}", f"/sbin/{name}", f"/usr/sbin/{name}")}
    # the builtins of bash that msh has not (alias, history, type ... of the DIR-859: not found)
    commands |= set(system.missing_builtin_commands(persona, ident))
    # ip, ss, ifconfig and netstat where the firmware has no such program
    if persona.kind == "device":
        tools = network.tool_styles(persona, ident)
        commands |= {spelling for name in network.NET_TOOLS if name not in tools
                     for spelling in (name, f"/bin/{name}", f"/sbin/{name}", f"/usr/bin/{name}", f"/usr/sbin/{name}")}
    if not content.busybox_help:
        commands |= set(BUSYBOX_PYTHON_COMMANDS)
    own = firmware.programs(persona, ident)
    if own is not None:
        # the scp of dropbear in the image: Cowrie's scp answers (scp -t of an upload); help is a builtin
        #   of its ash, so Cowrie's help (the builtins of bash) gives way to the txtcmd of the builtin
        if "usr/bin/scp" in own:
            commands -= {"scp"}
        if "help" in (firmware.builtins(persona) or ()):
            commands |= {"help"}
    # the shells the firmware has not (Cowrie's bash answers bash and /bin/bash before the tree)
    for name in firmware.shell_links(persona)[1]:
        commands |= {name, f"/bin/{name}", f"/usr/bin/{name}"}
    # no ssh client where the system has no SSH server: the shell says it is not found
    if persona.ssh_profile is None:
        commands |= set(SSH_CLIENT_COMMANDS)
    if persona.family == "openwrt":
        commands -= {"/bin/opkg", "opkg"}
    if persona.family == "ubiquiti":
        commands -= {"/bin/apt-get", "/usr/bin/apt-get", "apt-get"}
    # the patched openssl answers where the system has an openssl program
    if system.openssl_build(persona, ident) is None:
        commands.add("openssl")
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
    if not target or target.startswith(("/", "../")):
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
    """Replace text in every name and link target (python3.11 -> python3.12); a link keeps the
    length of its target as its size."""
    for _, node in list(tree.iter_nodes(t)):
        for index in (tree.A_NAME, tree.A_TARGET):
            value = tree.node_get(node, index)
            if isinstance(value, str):
                for old, new in replacements:
                    value = value.replace(old, new)
                tree.node_set(node, index, value)
                if index == tree.A_TARGET and tree.node_get(node, tree.A_TYPE) == tree.T_LINK:
                    tree.node_set(node, tree.A_SIZE, len(value))


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
    # a router keeps only the names of Cowrie's /etc its firmware has too (firmware.etc_names)
    keep = firmware.etc_names(persona)
    etc = tree.find_dir(t, "etc") if keep is not None else None
    if etc is not None:
        tree.node_children(etc)[:] = [c for c in tree.node_children(etc) if tree.node_get(c, tree.A_NAME) in keep]
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


def _without_text(node):
    """A missing node, or a file without contents (as Cowrie's pickle has its files)."""
    return node is None or (tree.node_get(node, tree.A_TYPE) == tree.T_FILE
                            and not isinstance(tree.node_get(node, tree.A_CONTENTS), bytes))


def _unit_files(t, persona, ident, processes):
    """A unit file for every unit systemctl shows (Cowrie's tree has those of its container), the link
    of the target that wants an enabled one."""
    units = servers.systemd_units(persona, ident, processes) if persona.kind == "server" else \
        devices.device_units(persona, ident, processes)
    directory = units["unit_dir"].lstrip("/")
    directory = "usr/" + directory if directory.startswith("lib/") else directory
    for name, target in servers.unit_links(persona, ident).items():
        # the links the packages ship between their unit files (openbsd-inetd.service -> inetd.service)
        if any(u["name"] == target for u in units["units"]):
            tree.ensure_link(t, f"{directory}/{name}", target, ctime=ident.installed_at)
    for unit in units["units"]:
        path = unit["path"]
        if not path.startswith(("/usr/lib/systemd/", "/lib/systemd/")):
            continue
        rel = "usr" + path if path.startswith("/lib/") else path.lstrip("/")
        # a unit of Cowrie's tree has its size and no contents: systemctl cat prints its text
        if _without_text(tree.find_node(t, rel)):
            tree.ensure_file(t, rel, servers.unit_file(persona, ident, unit).encode(), ctime=ident.installed_at)
        # the programs the unit runs
        text = tree.node_get(tree.find_node(t, rel), tree.A_CONTENTS)
        for line in (text.decode("utf-8", "replace").splitlines() if isinstance(text, bytes) else ()):
            program = system.exec_program(line) if line.startswith("ExecStart=") else ""
            node = system.program_node(t, program) if program else None
            rel_program = program.lstrip("/") if program.startswith("/") else f"usr/bin/{program}"
            if program in servers.SCRIPT_PROGRAMS and _without_text(node):
                tree.ensure_file(t, rel_program, servers.SCRIPT_PROGRAMS[program].encode(), mode=0o755,
                                 ctime=ident.installed_at)
            elif program and node is None:
                tree.ensure_executable(t, rel_program, program_size(ident, rel_program), ctime=ident.installed_at)
        if unit["state"] == "enabled":
            for wants in unit.get("wants") or [f"{servers.wants_target(unit['name'])}.wants"]:
                tree.ensure_link(t, f"etc/systemd/system/{wants}/{unit['name']}", path, ctime=ident.installed_at)
            for alias in unit.get("alias", ()):
                if not unit["name"].startswith("ssh"):
                    tree.ensure_link(t, f"etc/systemd/system/{alias}", path, ctime=ident.installed_at)


def _shell_proc(t, processes):
    """/proc/<PID> of the login shell, the PID ps shows and $$ expands to: Cowrie's tree has the
    process of the container's Python there (/proc/self, /proc/thread-self, /proc/net, /proc/mounts)."""
    shells = [p["PID"] for p in processes if is_login_shell(p) and isinstance(p.get("PID"), int)]
    own = tree.node_get(tree.find_node(t, "proc/self"), tree.A_TARGET) if tree.find_node(t, "proc/self") else None
    if not shells or not own:
        return
    old = own.rstrip("/").rsplit("/", 1)[-1]
    new = str(shells[-1])
    node = tree.find_dir(t, f"proc/{old}")
    if node is None or old == new or tree.find_node(t, f"proc/{new}") is not None:
        return
    node[tree.A_NAME] = new
    task = tree.find_dir(t, f"proc/{new}/task/{old}")
    if task is not None:
        task[tree.A_NAME] = new
    def retarget(link, target):
        # a link's size is the length of its target
        link[tree.A_TARGET] = target
        tree.node_set(link, tree.A_SIZE, len(target))

    for _, child in tree.iter_nodes(tree.find_dir(t, "proc")):
        if child[tree.A_TYPE] == tree.T_LINK and isinstance(child[tree.A_TARGET], str):
            target = re.sub(rf"(^|/)proc/{old}(?=/|$)", rf"\g<1>proc/{new}",
                            re.sub(rf"/task/{old}(?=/|$)", f"/task/{new}", child[tree.A_TARGET]))
            if target != child[tree.A_TARGET]:
                retarget(child, target)
    for rel in ("proc/self", "proc/thread-self", "proc/net", "proc/mounts"):
        link = tree.find_node(t, rel)
        if link is not None and link[tree.A_TYPE] == tree.T_LINK and isinstance(link[tree.A_TARGET], str):
            target = re.sub(rf"(^|/){old}(?=/|$)", rf"\g<1>{new}", link[tree.A_TARGET])
            if target != link[tree.A_TARGET]:
                retarget(link, target)


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


# /proc/<PID> of every process ps shows (_process_proc): cmdline, comm, stat and status as on Linux (recorded: the
#   7.0 kernel of the Docker VM, tests/fixtures/readlink.json "proc"); a folder holds only these, no environ or maps
PROC_STATES = {"R": "R (running)", "S": "S (sleeping)", "D": "D (disk sleep)", "T": "T (stopped)",
               "t": "t (tracing stop)", "Z": "Z (zombie)", "X": "X (dead)", "I": "I (idle)"}
# the major of the pseudo terminals (/dev/pts/N: 136), of the virtual consoles and serial lines (tty1, ttyS0: 4)
PROC_TTY_MAJOR = {"pts/": (136, 0), "ttyS": (4, 64), "tty": (4, 0)}
CLOCK_TICKS = 100


def _proc_tty(tty):
    """tty_nr of /proc/<PID>/stat: major << 8 | minor of the terminal, 0 without one."""
    for prefix, (major, base) in PROC_TTY_MAJOR.items():
        if tty.startswith(prefix) and tty[len(prefix):].isdigit():
            return major << 8 | (base + int(tty[len(prefix):]))
    return 0


# the name of a task (TASK_COMM_LEN less its NUL), the buffer /proc gives a longer one in (fs/proc/array.c)
TASK_COMM = 15
PROC_COMM = 63


def proc_comm(command, kernel=False, ident=None):
    """The name of a process (comm, 15 characters), the one ps and top show: the program of its first word, of a
    login shell without its dash, of a script run by its interpreter the script's (tpot_top_comm of the patch). A
    kernel thread's as fs/proc/array.c proc_task_name gives it: from Linux 4.18 a workqueue worker with its
    description after the 15 characters (wq_worker_comm, kworker/0:0H-events_highpri), from 5.17 any kernel thread
    its full name (get_kthread_comm), at most 63 characters; before, and without ident (the release), the 15
    characters of the task."""
    if kernel:
        name = command.strip("[]")
        version = kthreads.kernel_version(ident) if ident is not None else (0, 0, 0)
        full = (version >= (4, 18) and name.startswith("kworker/") and "-" in name) or version >= (5, 17)
        return name[:PROC_COMM] if full else name[:TASK_COMM]
    words = command.split()
    if not words:
        return ""
    name = words[0].rstrip(":").rsplit("/", 1)[-1].lstrip("-")
    if re.fullmatch(r"(python|perl|ruby|bash|sh)[0-9.]*", name) and len(words) > 1 and words[1].startswith("/"):
        name = words[1].rsplit("/", 1)[-1]
    return name[:TASK_COMM]


def proc_cmdline(command, kernel=False):
    """/proc/<PID>/cmdline: its words, each ending in a NUL; empty for a kernel thread. A title a daemon set for
    itself ("sshd: /usr/sbin/sshd -D [listener] ...", "nginx: master process ...") is one string (unverified: the
    padding a daemon leaves after its title)."""
    if kernel:
        return b""
    words = command.split()
    if words and (words[0].endswith(":") or words[0].startswith("@")):
        return command.encode("utf-8", "surrogateescape") + b"\0"
    return b"".join(word.encode("utf-8", "surrogateescape") + b"\0" for word in words)


def _real_program(t, path, folders=True):
    """The path of a program with every link of it resolved (/sbin/init -> /usr/lib/systemd/systemd), as the kernel
    names it in /proc/<PID>/exe; None where the tree has no such file. folders False keeps the folders as the path
    names them (a device: Cowrie's tree links /sbin to /usr/sbin, its firmware has folders of its own)."""
    path = "/" + path.lstrip("/")
    for _ in range(tree.MAX_LINKS):
        real = tree.real_path(t, path)
        node = tree.find_node(t, real) if real is not None else None
        if node is None:
            return None
        here = "/" + real if folders else path
        if tree.node_get(node, tree.A_TYPE) != tree.T_LINK:
            return here if tree.node_get(node, tree.A_TYPE) == tree.T_FILE else None
        target = tree.node_get(node, tree.A_TARGET) or ""
        path = posixpath.normpath(target if target.startswith("/") else posixpath.join(posixpath.dirname(here),
                                                                                       target))
    return None


def proc_program(t, persona, ident, command):
    """The program of a process of ps (its first word, the program of a title, a bare name in the folders of the
    PATH), resolved as /proc/<PID>/exe shows it; a login shell's is the program of the login shell
    (system.login_program); None for a kernel thread and a program the tree has not."""
    words = str(command or "").split()
    if not words or words[0].startswith("["):
        return None
    first = words[0]
    if first.startswith("-"):
        program = "/" + system.login_program(persona, ident)
    elif first in system.TITLE_PROGRAMS:
        program = system.TITLE_PROGRAMS[first]
    elif first.endswith(":"):
        program = devices.ps_program(command) or ""
    else:
        program = first
    if not program:
        return None
    folders = persona.kind == "server"
    if program.startswith("/"):
        return _real_program(t, program, folders)
    for folder in ("usr/sbin", "usr/bin", "sbin", "bin") if folders else ("sbin", "usr/sbin", "bin", "usr/bin"):
        found = _real_program(t, f"{folder}/{program}", folders)
        if found:
            return found
    return None


def _kernel_bits(ident):
    v = kthreads.kernel_version(ident)
    # the capabilities of the kernel (CAP_LAST_CAP + 1): 41 from 5.9, 40 in 5.8, 38 from 3.16, 37 before
    caps = 41 if v >= (5, 9) else 40 if v >= (5, 8) else 38 if v >= (3, 16) else 37
    return v, f"{(1 << caps) - 1:016x}"


def proc_status(persona, ident, process, uid, gid, threads):
    """/proc/<PID>/status of a process (process: pid, ppid, comm, state, vsz, rss, kernel, tty) in the layout of the
    kernel of the release: the fields of fs/proc/array.c in their order, those a kernel has not yet left out
    (unverified: the kernel versions the fields came with; the values a running process would have are drawn)."""
    v, full = _kernel_bits(ident)
    rng = rng_for(ident, f"proc status {process['pid']}")
    kernel = process["kernel"]
    pid, ppid = process["pid"], process["ppid"]
    groups = " ".join(str(g) for g in process.get("groups", ())) + (" " if process.get("groups") else "")
    lines = [("Name", process["comm"])]
    if v >= (4, 7):
        lines.append(("Umask", "0000" if kernel else "0022"))
    lines += [("State", PROC_STATES.get(process["state"][:1], "S (sleeping)")), ("Tgid", pid)]
    if v >= (3, 13):
        lines.append(("Ngid", 0))
    lines += [("Pid", pid), ("PPid", ppid), ("TracerPid", 0), ("Uid", "\t".join([str(uid)] * 4)),
              ("Gid", "\t".join([str(gid)] * 4)), ("FDSize", 64 if kernel else rng.choice((64, 64, 128, 256))),
              ("Groups", groups)]
    if v >= (4, 1):
        lines += [("NStgid", pid), ("NSpid", pid), ("NSpgid", process["pgrp"]), ("NSsid", process["sid"])]
    if v >= (6, 9):
        lines.append(("Kthread", 1 if kernel else 0))
    if not kernel:
        vsz, rss = max(process["vsz"], 4), max(process["rss"], 0)
        anon = min(rss, max(4, rss * rng.randint(10, 60) // 100))
        shmem = 0
        memory = [("VmPeak", vsz + rng.randint(0, 2048)), ("VmSize", vsz), ("VmLck", 0), ("VmPin", 0),
                  ("VmHWM", rss + rng.randint(0, 512)), ("VmRSS", rss)]
        if v >= (4, 5):
            memory += [("RssAnon", anon), ("RssFile", rss - anon - shmem), ("RssShmem", shmem)]
        memory += [("VmData", max(4, vsz // rng.randint(4, 12))), ("VmStk", 132), ("VmExe", rng.randint(8, 900)),
                   ("VmLib", max(4, vsz // rng.randint(3, 8))), ("VmPTE", rng.randint(11, 30) * 4), ("VmSwap", 0)]
        if v >= (4, 4):
            memory.append(("HugetlbPages", 0))
        lines += [(key, f"{value:8d} kB") for key, value in memory]
    if v >= (4, 15):
        lines.append(("CoreDumping", 0))
    if v >= (5, 0):
        lines.append(("THP_enabled", 1))
    if v >= (6, 4):
        lines.append(("untag_mask", "0xffffffffffffffff" if persona.hardware_platform == "x86_64" else
                      "0xffffffffffffff"))
    cpus = max(1, ident.hardware.cpus)
    # RLIMIT_SIGPENDING: half of threads-max, which the kernel sets from the memory
    lines += [("Threads", threads), ("SigQ", f"0/{max(1024, ident.hardware.mem_kb // 256)}"),
              ("SigPnd", "0" * 16), ("ShdPnd", "0" * 16), ("SigBlk", "0" * 16),
              ("SigIgn", "0" * 16 if kernel else process.get("sigign", "0000000000001000")),
              ("SigCgt", "0" * 16 if kernel else process.get("sigcgt", "0000000180004002")),
              ("CapInh", "0" * 16), ("CapPrm", full if uid == 0 else "0" * 16),
              ("CapEff", full if uid == 0 else "0" * 16), ("CapBnd", full)]
    if v >= (4, 3):
        lines.append(("CapAmb", "0" * 16))
    if v >= (4, 10):
        lines.append(("NoNewPrivs", 0))
    lines.append(("Seccomp", 0))
    if v >= (5, 9):
        lines.append(("Seccomp_filters", 0))
    if v >= (4, 17):
        lines.append(("Speculation_Store_Bypass", "thread vulnerable" if persona.hardware_platform == "x86_64"
                      else "not vulnerable"))
    if v >= (5, 1):
        lines.append(("SpeculationIndirectBranch", "conditional enabled" if persona.hardware_platform == "x86_64"
                      else "unknown"))
    mask = f"{(1 << cpus) - 1:x}"
    lines += [("Cpus_allowed", mask), ("Cpus_allowed_list", "0" if cpus == 1 else f"0-{cpus - 1}"),
              ("Mems_allowed", ",".join(["00000000"] * 31 + ["00000001"]) if persona.hardware_platform == "x86_64"
               else "1"), ("Mems_allowed_list", 0),
              ("voluntary_ctxt_switches", rng.randint(2, 40000)),
              ("nonvoluntary_ctxt_switches", rng.randint(0, 900))]
    if v >= (6, 6) and persona.hardware_platform == "x86_64":
        # the user shadow stack of x86 (arch_proc_pid_thread_features), none enabled
        lines += [("x86_Thread_features", ""), ("x86_Thread_features_locked", "")]
    return "".join(f"{key}:\t{value}\n" for key, value in lines).encode("utf-8", "surrogateescape")


def proc_stat(ident, process, threads, start_ticks):
    """/proc/<PID>/stat: the 52 fields of fs/proc/array.c do_task_stat; the CPU times stay 0 here (the patched
    Cowrie writes those of ps when the file is read: tpot_proc_stat), the start in clock ticks after the boot."""
    rng = rng_for(ident, f"proc stat {process['pid']}")
    kernel = process["kernel"]
    state = process["state"][:1] or "S"
    nice = -20 if "<" in process["state"] else 19 if "N" in process["state"] else 0
    vsize = 0 if kernel else process["vsz"] * 1024
    rss = 0 if kernel else process["rss"] // 4
    code = 0 if kernel else 0x555555554000 + rng.randrange(0, 1 << 30, 4096)
    stack = 0 if kernel else 0x7ffc00000000 + rng.randrange(0, 1 << 32, 16)
    fields = [process["pid"], f"({process['comm']})", state, process["ppid"], process["pgrp"], process["sid"],
              process["tty_nr"], process["tpgid"], 2129984 if kernel else 4194560 if process["uid"] == 0 else 4194304,
              0 if kernel else rng.randint(80, 9000), 0, 0 if kernel else rng.randint(0, 60), 0,
              0, 0, 0, 0, 20 + nice, nice, threads, 0, start_ticks, vsize, rss, 18446744073709551615,
              1 if kernel else code, 1 if kernel else code + rng.randrange(4096, 1 << 20, 4096), 0 if kernel else stack,
              0, 0, 0, 0, 0 if kernel else 4096, 0 if kernel else 81920, 0, 0, 0, 17, 0, 0, 0, 0, 0, 0]
    data = 0 if kernel else code + (1 << 21)
    fields += [data, data + 4096 if data else 0, data + (1 << 22) if data else 0, stack + 2000 if data else 0,
               stack + 2040 if data else 0, stack + 2040 if data else 0, stack + 4000 if data else 0, 0]
    return (" ".join(str(f) for f in fields) + "\n").encode("utf-8", "surrogateescape")


def proc_processes(persona, ident, content):
    """Every process of the persona's ps list and its kernel threads, with what its /proc/<PID> folder needs:
    pid, ppid, process group, session, terminal, comm, command line, state, sizes, user and kernel thread or not."""
    accounts = {}
    for line in content.files.get("etc/passwd", "").splitlines():
        parts = line.split(":")
        if len(parts) > 3 and parts[2].isdigit() and parts[3].isdigit():
            accounts.setdefault(parts[0], (int(parts[2]), int(parts[3])))
    members = {}
    for line in content.files.get("etc/group", "").splitlines():
        parts = line.split(":")
        if len(parts) > 3 and parts[2].isdigit():
            for member in filter(None, parts[3].split(",")):
                members.setdefault(member, set()).add(int(parts[2]))
    out = []
    listed = [p for p in content.processes if isinstance(p.get("PID"), int)]
    shells = [p["PID"] for p in listed if is_login_shell(p)]
    threads = kthreads.kernel_threads(persona, ident, content.processes, content.filesystems)
    for entry in listed:
        pid, command, stat = entry["PID"], str(entry.get("COMMAND") or ""), str(entry.get("STAT") or "S")
        login = bool(shells) and pid == shells[-1]
        name = persona.user if login else str(entry.get("USERNAME") or entry.get("USER") or "root")
        uid, gid = accounts.get(name, (persona.uid, persona.gid) if login else (0, 0))
        ppid = 0 if pid == 1 else (pid - 1 if login else entry.get("PPID") if isinstance(entry.get("PPID"), int)
                                   else 1)
        leader = "s" in stat or login
        tty = str(entry.get("TTY") or "?")
        out.append({"pid": pid, "ppid": ppid, "pgrp": pid if leader else ppid, "sid": pid if leader else ppid,
                    "tty_nr": _proc_tty(tty), "tpgid": pid if login else -1 if tty == "?" else ppid,
                    "comm": proc_comm(command), "command": command, "state": stat, "vsz": int(entry.get("VSZ") or 0),
                    "rss": int(entry.get("RSS") or 0), "uid": uid, "gid": gid, "kernel": False, "login": login,
                    # the groups of a login (initgroups: the primary and every group that names the user)
                    "groups": sorted({gid} | members.get(name, set())) if login else ()})
    for entry in threads:
        pid, command = entry["PID"], str(entry.get("COMMAND") or "")
        out.append({"pid": pid, "ppid": int(entry.get("PPID", 2)), "pgrp": 0, "sid": 0, "tty_nr": 0, "tpgid": -1,
                    "comm": proc_comm(command, True, ident), "command": command,
                    "state": str(entry.get("STAT") or "S"), "vsz": 0, "rss": 0, "uid": 0, "gid": 0, "kernel": True,
                    "login": False})
    return out


def _process_proc(t, persona, ident, content):
    """/proc/<PID> of every process ps shows: the persona's list and its kernel threads (the session's own, its
    sshd, in.telnetd or login and the PID of its shell, are the patched Cowrie's: tpot_proc_session). Each folder
    has cmdline, comm, stat and status, the links exe (not for a kernel thread, nor where the tree has not the
    program), cwd and root, and an empty fd (the login shell's has its terminal: _login_links). A number of
    Cowrie's tree that is no such process goes, so ls /proc lists what ps lists."""
    processes = proc_processes(persona, ident, content)
    wanted = {str(p["pid"]) for p in processes}
    proc = tree.find_dir(t, "proc")
    if proc is not None:
        tree.node_children(proc)[:] = [c for c in tree.node_children(proc)
                                       if not str(tree.node_get(c, tree.A_NAME)).isdigit()
                                       or tree.node_get(c, tree.A_NAME) in wanted]
    for process in processes:
        pid, uid, gid = process["pid"], process["uid"], process["gid"]
        rel = f"proc/{pid}"
        rng = rng_for(ident, f"proc {pid}")
        threads = rng.randint(2, 8) if "l" in process["state"] else 1
        start = 0 if pid == 1 else 1 + pid * 3 % 2400 if not process["login"] else \
            int(max(0, drawn_at(ident) - ident.booted_at)) * CLOCK_TICKS
        files = {"cmdline": proc_cmdline(process["command"], process["kernel"]),
                 "comm": (process["comm"] + "\n").encode("utf-8", "surrogateescape"),
                 "stat": proc_stat(ident, process, threads, start),
                 "status": proc_status(persona, ident, process, uid, gid, threads)}
        when = ident.now if process["login"] else ident.booted_at
        for name, data in files.items():
            node = tree.ensure_file(t, f"{rel}/{name}", data, uid, gid, 0o644 if name == "comm" else 0o444,
                                    ctime=when)
            tree.node_set(node, tree.A_SIZE, 0)
            tree.node_set(node, tree.A_CTIME, when)
        if not process["login"]:
            program = None if process["kernel"] else proc_program(t, persona, ident, process["command"])
            if program:
                tree.ensure_link(t, f"{rel}/exe", program, ctime=when)
            else:
                tree.remove_node(t, f"{rel}/exe")
            for name in ("cwd", "root"):
                tree.ensure_link(t, f"{rel}/{name}", "/", ctime=when)
            tree.ensure_dir(t, f"{rel}/fd", uid, gid, 0o500, ctime=when)
        tree.ensure_dir(t, rel, uid, gid, 0o555, ctime=when)
        tree.node_set(tree.find_dir(t, rel), tree.A_CTIME, when)


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
    # the link nodes of Cowrie's tree (the nodes themselves, held until the cleanup below): one whose program the
    #   persona has not goes with it; ensure_link makes a new node, so a link of the persona is never among them
    upstream_links = [node for _, node in tree.iter_nodes(t) if tree.node_get(node, tree.A_TYPE) == tree.T_LINK]
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
    openssl = system.openssl_build(persona, ident)
    if openssl:
        # /usr/bin/openssl of the build
        sizes["openssl"] = (system.openssl_size(openssl),) * 2
    tools, tool_links = system.tool_programs(persona, ident)
    for rel in sorted(set(content.txtcmds) | set(content.programs) | set(system.programs(persona, ident)) | set(tools)):
        tree.ensure_executable(t, rel, program_size(ident, rel, sizes), ctime=ident.installed_at)
    for rel in system.directories(persona):
        tree.ensure_dir(t, rel, ctime=ident.installed_at)
    # the applets of the firmware's BusyBox are links to it (ls -l /bin/ls of OpenWrt: `-> busybox`), Cowrie's
    #   Debian program of that name goes; the tools placed below (wc, ip, lsmod, ...) win
    links = system.applet_links(persona, ident)
    if links:
        for rel in links:
            for folder in firmware.PROGRAM_FOLDERS:
                tree.remove_node(t, f"{folder}/{rel.rsplit('/', 1)[1]}")
        for rel, target in links.items():
            tree.ensure_link(t, rel, target, ctime=ident.installed_at)
        # a file command the firmware's BusyBox has not (the rmdir of the DIR-859) is no program either, nor
        #   Cowrie's Debian program of another command its BusyBox has no applet for (df, head of the DIR-859)
        for name in set(system.missing_fs_tools(persona, ident)) | set(system.missing_applet_commands(persona, ident)) \
                | set(system.missing_applet_txtcmds(persona, ident)):
            for folder in firmware.PROGRAM_FOLDERS:
                tree.remove_node(t, f"{folder}/{name}")
    # wc, sort and seq as the system has them: coreutils (Cowrie's tree has those of Debian) or the
    # applets of the firmware's BusyBox
    if persona.kind == "device":
        for name in system.TEXT_TOOLS:
            for folder in ("bin", "sbin", "usr/bin", "usr/sbin"):
                tree.remove_node(t, f"{folder}/{name}")
    if persona.kind == "device":
        # lscpu and nproc only where the firmware has them (system.cpu_commands; their txtcmds there)
        has_lscpu, has_nproc = system.cpu_commands(persona, ident)
        for name, has in (("lscpu", has_lscpu), ("nproc", has_nproc)):
            if not has:
                for folder in ("bin", "sbin", "usr/bin", "usr/sbin"):
                    tree.remove_node(t, f"{folder}/{name}")
    if persona.kind == "device":
        # ip, ss, ifconfig and netstat as the firmware has them
        for name in network.NET_TOOLS:
            for folder in ("bin", "sbin", "usr/bin", "usr/sbin"):
                tree.remove_node(t, f"{folder}/{name}")
        for rel, target in network.tool_paths(persona, ident).values():
            if target:
                tree.ensure_link(t, rel, target, ctime=ident.installed_at)
            else:
                tree.ensure_executable(t, rel, program_size(ident, rel, sizes), ctime=ident.installed_at)
        if persona.family == "ubiquiti":
            tree.ensure_link(t, "bin/ip", "/sbin/ip", ctime=ident.installed_at)
    for rel, target in system.text_programs(persona, ident).values():
        if target:
            tree.ensure_link(t, rel, target, ctime=ident.installed_at)
        else:
            tree.ensure_executable(t, rel, program_size(ident, rel, sizes), ctime=ident.installed_at)
    if persona.kind == "server":
        # Red Hat's sudo package keeps sudoers.d closed
        tree.ensure_dir(t, "etc/sudoers.d", mode=0o750 if system.is_el(persona) else 0o755, ctime=ident.installed_at)
    removed, ssh_links = system.ssh_units(persona, ident)
    for rel in removed:
        tree.remove_node(t, rel)
    for rel, target in (system.python_links(persona, ident) + system.links(persona) + ssh_links + tool_links
                        + system.init_links(persona)):
        link(t, persona, ident, rel, target, ident.installed_at)
    if persona.kind != "server" and persona.family in system.SYSTEMD_DEVICES:
        _unit_files(t, persona, ident, content.processes)
    if persona.kind == "server":
        _unit_files(t, persona, ident, content.processes)
        # /sbin/init of systemd-sysv is a link to systemd, which runs as PID 1 (/proc/1/exe; unverified: the
        #   spelling of the target, absolute before the /usr merge of the packages, as /lib/systemd/systemd)
        prefix = systemd_prefix(persona, ident)
        if prefix and tree.find_node(t, "usr/lib/systemd/systemd") is not None and \
                any(p["COMMAND"].split()[:1] == ["/sbin/init"] for p in content.processes):
            tree.remove_node(t, "usr/sbin/init")
            tree.ensure_link(t, "usr/sbin/init", "/lib/systemd/systemd" if prefix == "/lib/systemd"
                             else "../lib/systemd/systemd", ctime=ident.installed_at)
        # the daemons of ps are programs of the tree
        for process in content.processes:
            program = process["COMMAND"].split()[0]
            if program.startswith("/") and tree.find_node(t, program) is None:
                tree.ensure_executable(t, program.lstrip("/"), program_size(ident, program), ctime=ident.installed_at)
    for files in system.rpm_libraries(persona, ident).values():
        for rel, value in files.items():
            tree.remove_node(t, rel)
            if isinstance(value, str):
                tree.ensure_link(t, rel, value, ctime=ident.installed_at)
            else:
                tree.ensure_executable(t, rel, value, ctime=ident.installed_at)
    _firmware_programs(t, persona, ident, content)
    # the daemons of a device's ps list and the telnetd of its telnet sessions, where the tree has them not
    for path, target in devices.daemon_programs(persona, ident, content.processes).items():
        if tree.resolve(t, path.lstrip("/")) is not None:
            continue
        if target:
            tree.ensure_link(t, path.lstrip("/"), target, ctime=ident.installed_at)
        else:
            tree.ensure_executable(t, path.lstrip("/"), program_size(ident, path), ctime=ident.installed_at)
    links, gone = firmware.shell_links(persona)
    for name in gone:
        tree.remove_node(t, f"usr/bin/{name}")
    for name, target in links.items():
        tree.remove_node(t, f"usr/bin/{name}")
        tree.ensure_link(t, f"usr/bin/{name}", target, ctime=ident.installed_at)
    if links and (tree.find_node(t, "proc/1/exe") or [None] * 10)[tree.A_TARGET] == "/usr/bin/dash":
        # PID 1 is BusyBox init (procd on OpenWrt) where Cowrie's tree had its container's dash
        tree.remove_node(t, "proc/1/exe")
        init = "/sbin/procd" if persona.family == "openwrt" and tree.find_node(t, "sbin/procd") else "/bin/busybox"
        tree.ensure_link(t, "proc/1/exe", init, ctime=ident.booted_at)
    if persona.family == "openwrt":
        # lsmod and the other module tools of OpenWrt are kmodloader of ubox
        tree.ensure_executable(t, "usr/sbin/kmodloader", program_size(ident, "/sbin/kmodloader"),
                               ctime=ident.installed_at)
        for name in system.KMODLOADER_LINKS:
            tree.remove_node(t, f"usr/sbin/{name}")
            tree.ensure_link(t, f"usr/sbin/{name}", "/sbin/kmodloader", ctime=ident.installed_at)
    if persona.family == "ubiquiti":
        # EdgeOS boots with systemd: no links of Cowrie's tree in the rc directories
        for rel, node in list(tree.iter_nodes(t)):
            if re.fullmatch(r"/etc/rc[0-6S]\.d/[SK]\d\d.*", rel) and node[tree.A_TYPE] == tree.T_LINK:
                tree.remove_node(t, rel.lstrip("/"))
    _shell_proc(t, content.processes)
    _login_links(t, persona, ident, content.files.get("etc/group", ""))
    # a folder of its own for every process of ps (after the shell's: its exe, cwd and terminal stay)
    _process_proc(t, persona, ident, content)
    # a link of Cowrie's tree whose target the persona has not (lastb -> last, slogin -> ssh, pidof -> killall5 of a
    #   firmware without them) goes with it, once the login shell has its /proc (/proc/thread-self names its task);
    #   the links the persona makes are the validator's (_check_links)
    tree.remove_dangling_links(t, upstream_links)
    _set_dirs(t, persona, ident.installed_at)
    return t


def _firmware_programs(t, persona, ident, content):
    """The program folders of a firmware image (firmware.programs) in place of Cowrie's: folders of
    their own (no /usr merge), the BusyBox applets as links, the vendor's programs of its release, the
    folders at the root of the image (firmware.root_dirs; the others of Cowrie's tree go, but /home).
    ulimit is a builtin of the Archer C7's ash (its txtcmd answers without a program); a persona with only
    the program folders of its firmware (the NAS326) keeps Cowrie's root and its txtcmds as programs where
    the table has none (/bin/ulimit: no table of the builtins of its ash was recorded)."""
    table = firmware.programs(persona, ident)
    if table is None:
        return
    for folder in firmware.PROGRAM_FOLDERS:
        tree.remove_node(t, folder)
    for folder in firmware.PROGRAM_FOLDERS:
        tree.ensure_dir(t, folder, ctime=ident.installed_at)
    for rel, value in sorted(table.items()):
        if isinstance(value, str):
            tree.ensure_link(t, rel, value, ctime=ident.installed_at)
        else:
            tree.ensure_executable(t, rel, value, ctime=ident.installed_at)
    if not firmware.is_image(persona):
        for rel in sorted(set(content.txtcmds) | set(content.programs)):
            if tree.resolve(t, rel) is None:
                tree.ensure_executable(t, rel, program_size(ident, rel), ctime=ident.installed_at)
    root = firmware.root_dirs(persona)
    for rel in root:
        tree.ensure_dir(t, rel, ctime=ident.installed_at)
    for child in list(tree.node_children(t)) if root else ():
        name = child[tree.A_NAME]
        if name not in root and name not in firmware.ROOT_KEEP:
            tree.remove_node(t, name)
    # /var/run and /var/lock of Cowrie's tree are links into /run: folders of their own (in the images
    #   /var is a link to /tmp, which holds them; unverified: their contents)
    if root:
        for rel in ("var/run", "var/lock"):
            node = tree.find_node(t, rel)
            if node is not None and node[tree.A_TYPE] == tree.T_LINK:
                tree.remove_node(t, rel)
            tree.ensure_dir(t, rel, ctime=ident.booted_at)
    # the links of Cowrie's tree into the old folders (Debian's alternatives) go, init is BusyBox
    tree.remove_node(t, "etc/alternatives")
    if tree.find_node(t, "proc/1/exe") is not None:
        tree.remove_node(t, "proc/1/exe")
        tree.ensure_link(t, "proc/1/exe", "/bin/busybox", ctime=ident.booted_at)
    folders = tuple(f"{folder}/" for folder in firmware.PROGRAM_FOLDERS)
    for rel, node in list(tree.iter_nodes(t)):
        if node[tree.A_TYPE] != tree.T_LINK or not node[tree.A_TARGET]:
            continue
        target = posixpath.normpath(posixpath.join(posixpath.dirname(rel), node[tree.A_TARGET])).lstrip("/") \
            if not node[tree.A_TARGET].startswith("/") else node[tree.A_TARGET].lstrip("/")
        if target.startswith(folders) and tree.resolve(t, rel) is None:
            tree.remove_node(t, rel.lstrip("/"))


def _host_key_lines(names, keys_dir):
    return "".join(
        f"{KEY_FILES[k]}_public_key = {keys_dir}/ssh_host_{KEY_FILES[k]}_key.pub\n"
        f"{KEY_FILES[k]}_private_key = {keys_dir}/ssh_host_{KEY_FILES[k]}_key\n"
        for k in names)


# the options of an SSH session ([ssh] after listen_endpoints): only for a persona with an SSH server
SSH_SESSION_OPTIONS = """sftp_enabled = true
forwarding = false
forward_redirect = false
forward_tunnel = false
auth_none_enabled = false
auth_keyboard_interactive_enabled = true
auth_publickey_allow_any = true
"""


def ssh_server_options(persona, ident, keys_dir):
    """The options of [ssh] before listen_endpoints: the host keys and algorithms of the persona's SSH
    server. Without one (ssh_profile None) Cowrie still listens on 22 and closes each connection
    before its banner (drop_connections, patch_ssh_drop); its factory needs one host key to start,
    an ed25519 key that is never offered."""
    if persona.ssh_profile is None:
        return "drop_connections = true\n" + _host_key_lines(("ssh-ed25519",), keys_dir) + \
            "public_key_auth = ssh-ed25519\n"
    r = ident.release
    profile = PROFILES[persona.ssh_profile]
    return f"""{_host_key_lines(profile.host_keys, keys_dir)}public_key_auth = {",".join(profile.host_keys)}
host_key_algorithms = {",".join(host_key_algorithms(profile, r))}
kex_algorithms = {",".join(kex_algorithms(profile, r))}
server_sig_algs = {server_sig_algs(profile, r)}
ext_info_s = {"true" if ext_info_s(profile, r) else "false"}
version = {r.ssh_banner}
ciphers = {",".join(profile.ciphers)}
macs = {",".join(profile.macs)}
compression = {",".join(profile.compression)}
"""


def render_cfg(persona, ident, runtime_dir, keys_dir, cowrie_root=COWRIE_ROOT, content=None):
    r = ident.release
    content = content if content is not None else persona.builder(persona, ident)
    busybox = f"busybox_help = {runtime_dir}/{BUSYBOX_HELP}\n" if content.busybox_help else ""
    names = firmware.builtins(persona)
    builtins = f"builtins = {','.join(names)}\n" if names is not None else ""
    if firmware.which_table(persona, system.busybox_applets(persona, ident)) is not None:
        builtins += f"which_dirs = {runtime_dir}/{PROGRAM_DIRS}\n"
    # wget of the firmware's BusyBox (the patched wget.py writes its texts)
    if content.busybox_help and persona.family != "ubiquiti" and "wget" in system.busybox_applets(persona, ident):
        builtins += "wget_style = busybox\n"
        if persona.family == "tplink":
            # its BusyBox is built without FEATURE_WGET_TIMEOUT (wget of the image: no -T in its usage, no --timeout)
            builtins += "wget_timeout = no\n"
    if persona.family == "tplink":
        # and without FEATURE_DD_THIRD_STATUS_LINE (dd of the image: the records only)
        builtins += "dd_status_line = no\n"
    builtins += "".join(f"{key} = {value}\n" for key, value in firmware.login_env(persona).items())
    # HISTTIMEFORMAT as a JSON string: the format ends in a space, which the config file would drop
    stamp = system.history_time_format(persona)
    history_time_format = f"history_time_format = {json.dumps(stamp)}\n" if stamp else ""
    untimed = system.history_untimed(persona)
    history_time_format += f"history_untimed = {untimed}\n" if untimed else ""
    # bash --version of the persona's bash (QNAP), a JSON string as it has two lines
    bash_version = f"bash_version = {json.dumps(content.bash_version)}\n" if content.bash_version else ""
    if persona.family in system.BUSYBOX_FAMILIES:
        # ls -l of BusyBox, the total line of a build with CONFIG_DESKTOP
        builtins += "ls_style = busybox\n" + ("ls_total = yes\n" if system.busybox_desktop(persona) else "")
    usage = firmware.usage_table(persona)
    if usage is not None:
        # the recorded usage of uname, head, grep and which of its BusyBox
        builtins += f"busybox_usage = {usage}\n"
    if system.command_not_found_lines(persona, ident) is not None:
        busybox += (f"command_not_found = {runtime_dir}/{system.COMMAND_NOT_FOUND}\n"
                    f"command_not_found_sudo = {','.join(system.sudo_users(content.files.get('etc/group', '')))}\n")
    tools = "".join(f"{key} = {value}\n"
                    for key, value in system.shell_keys(persona, ident, content.files, runtime_dir).items())
    top = "".join(f"{key} = {value}\n" for key, value in top_keys(persona, ident, content).items())
    top += f"df_table = {runtime_dir}/{DF_TABLE}\n"
    top += f"kernel_thread_list = {runtime_dir}/{kthreads.KERNEL_THREADS}\n"
    top += "".join(f"{key} = {value}\n" for key, value in system.awk_keys(persona, ident).items())
    top += "".join(f"{key} = {value}\n" for key, value in system.text_keys(persona, ident).items())
    top += "".join(f"{key} = {value}\n" for key, value in system.fs_keys(persona, ident).items())
    top += f"network = {runtime_dir}/{network.NETWORK_FILE}\n"
    if system.openssl_build(persona, ident):
        top += f"openssl = {runtime_dir}/{system.OPENSSL_DATA}\n"
    if network.telnet_server(persona, ident):
        top += f"telnet_server = {network.telnet_server(persona, ident)}\n"
    if devices.telnet_daemon(persona, ident):
        top += f"telnet_daemon = {devices.telnet_daemon(persona, ident)}\n"
    ssh = ssh_server_options(persona, ident, keys_dir)
    ssh_sessions = SSH_SESSION_OPTIONS
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
auth_refused_pairs = {refused_logins_value()}

[shell]
filesystem = {runtime_dir}/fs.pickle
processes = {runtime_dir}/cmdoutput.json
arch = {persona.arch}
kernel_version = {r.kernel_version}
kernel_build_string = {r.kernel_build}
hardware_platform = {persona.hardware_platform}
operating_system = {persona.operating_system}
prompt_style = {persona.prompt_style}
{builtins}shell_name = {system.shell_style(persona)[0]}
not_found_style = {system.shell_style(persona)[1]}
history_size = {system.history_sizes(persona, r)[1]}
history_size_root = {system.history_sizes(persona, r)[0]}
history_control = {system.history_controls(persona)[1]}
history_control_root = {system.history_controls(persona)[0]}
history_file_size = {system.history_file_sizes(persona, r)[1]}
history_file_size_root = {system.history_file_sizes(persona, r)[0]}
history_export = {",".join(system.history_exports(persona))}
{history_time_format}{bash_version}ssh_version = {r.ssh_version}
skip_python_commands = {",".join(skip_python_commands(persona, ident, content))}
{top}{busybox}{tools}
[ssh]
enabled = true
{ssh}listen_endpoints = tcp:22:interface=0.0.0.0
{ssh_sessions if persona.ssh_profile is not None else ""}
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
    the packages of apt (Debian) and of dnf / rpm (RHEL 9)."""
    if persona.kind == "device":
        # EdgeOS and DSM have systemd, EdgeOS sudo
        files = {system.SYSTEMD_UNITS: devices.device_units(persona, ident, processes)}
        if "sudo" in system.python_answers(persona):
            files[system.SUDO_LIST] = system.sudo_list(persona)
        return files
    files = {system.SYSTEMD_UNITS: servers.systemd_units(persona, ident, processes),
             system.SUDO_LIST: system.sudo_list(persona)}
    files[package_units.PACKAGE_UNITS] = package_units.package_units(persona, ident, files[system.SYSTEMD_UNITS])
    if system.has_dpkg(persona):
        files[system.APT_PACKAGES] = system.apt_packages(persona, ident)
    if persona.family == "rhel":
        files[system.DNF_PACKAGES] = servers.dnf_packages(persona, ident)
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
    dirs = firmware.which_table(persona, system.busybox_applets(persona, ident))
    if dirs is not None:
        _write(out_dir / PROGRAM_DIRS, "".join(f"{name} {','.join(folders)}\n" for name, folders in sorted(dirs.items())))
    cnf = system.command_not_found_lines(persona, ident)
    if cnf is not None:
        _write(out_dir / system.COMMAND_NOT_FOUND, "".join(f"{line}\n" for line in cnf))
    _write(out_dir / network.NETWORK_FILE,
           json.dumps(network.network(persona, ident, content.processes), indent=1, sort_keys=True) + "\n")
    _write(out_dir / kthreads.KERNEL_THREADS,
           json.dumps(kthreads.kernel_threads(persona, ident, content.processes, content.filesystems), indent=1) + "\n")
    _write(out_dir / DF_TABLE, json.dumps(system.df_table(persona, ident, content.filesystems), indent=1, sort_keys=True) + "\n")
    if persona.family == "openwrt":
        _write(out_dir / system.OPKG_HELP_FILE, system.OPKG_HELP)
    openssl = system.openssl_data(persona, ident)
    if openssl is not None:
        _write(out_dir / system.OPENSSL_DATA, json.dumps(openssl, indent=1, sort_keys=True) + "\n")
    names = system.package_names(persona, ident)
    if names is not None:
        _write(out_dir / system.PACKAGE_NAMES, "".join(f"{line}\n" for line in names))
    for name, lines in system.package_meta_files(persona, ident).items():
        _write(out_dir / name, "".join(f"{line}\n" for line in lines))
    if persona.kind == "server":
        _write(out_dir / system.SERVICE_STATUS, system.service_status(persona))
    if persona.kind == "server" or persona.family in system.SYSTEMD_DEVICES:
        for name, data in runtime_json(persona, ident, content.processes).items():
            _write(out_dir / name, json.dumps(data, indent=1, sort_keys=True) + "\n")
    _write(out_dir / "cowrie.cfg", render_cfg(persona, ident, Path(runtime_dir), Path(keys_dir), cowrie_root,
                                              content))
    _write(out_dir / "identity.json", json.dumps(to_json(ident), indent=2, sort_keys=True, default=str) + "\n")
