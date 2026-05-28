#!/usr/bin/env python3

import argparse
import os
import pickle
import shutil
import stat
import time
from pathlib import Path


HOSTNAME = "srv01"
LOGIN_USER = "ubuntu"
LOGIN_UID = 1000
LOGIN_GID = 1000
MIN_PICKLE_SIZE = 1_000_000

A_NAME = 0
A_TYPE = 1
A_UID = 2
A_GID = 3
A_SIZE = 4
A_MODE = 5
A_CTIME = 6
A_CONTENTS = 7
A_TARGET = 8
A_REALFILE = 9

T_LINK = 0
T_DIR = 1
T_FILE = 2

TEXT_FILES = {
    "etc/passwd": """root:x:0:0:root:/root:/bin/bash
daemon:x:1:1:daemon:/usr/sbin:/usr/sbin/nologin
bin:x:2:2:bin:/bin:/usr/sbin/nologin
sys:x:3:3:sys:/dev:/usr/sbin/nologin
sync:x:4:65534:sync:/bin:/bin/sync
games:x:5:60:games:/usr/games:/usr/sbin/nologin
man:x:6:12:man:/var/cache/man:/usr/sbin/nologin
lp:x:7:7:lp:/var/spool/lpd:/usr/sbin/nologin
mail:x:8:8:mail:/var/mail:/usr/sbin/nologin
news:x:9:9:news:/var/spool/news:/usr/sbin/nologin
uucp:x:10:10:uucp:/var/spool/uucp:/usr/sbin/nologin
proxy:x:13:13:proxy:/bin:/usr/sbin/nologin
www-data:x:33:33:www-data:/var/www:/usr/sbin/nologin
backup:x:34:34:backup:/var/backups:/usr/sbin/nologin
list:x:38:38:Mailing List Manager:/var/list:/usr/sbin/nologin
irc:x:39:39:ircd:/run/ircd:/usr/sbin/nologin
gnats:x:41:41:Gnats Bug-Reporting System:/var/lib/gnats:/usr/sbin/nologin
nobody:x:65534:65534:nobody:/nonexistent:/usr/sbin/nologin
systemd-network:x:998:998:systemd Network Management:/:/usr/sbin/nologin
systemd-resolve:x:997:997:systemd Resolver:/:/usr/sbin/nologin
messagebus:x:100:102::/nonexistent:/usr/sbin/nologin
sshd:x:101:65534::/run/sshd:/usr/sbin/nologin
ubuntu:x:1000:1000:Ubuntu:/home/ubuntu:/bin/bash
""",
    "etc/group": """root:x:0:
daemon:x:1:
bin:x:2:
sys:x:3:
adm:x:4:syslog,ubuntu
tty:x:5:
disk:x:6:
lp:x:7:
mail:x:8:
news:x:9:
uucp:x:10:
man:x:12:
proxy:x:13:
kmem:x:15:
dialout:x:20:ubuntu
fax:x:21:
voice:x:22:
cdrom:x:24:ubuntu
floppy:x:25:ubuntu
tape:x:26:
sudo:x:27:ubuntu
audio:x:29:ubuntu
dip:x:30:ubuntu
www-data:x:33:
backup:x:34:
operator:x:37:
list:x:38:
irc:x:39:
src:x:40:
gnats:x:41:
shadow:x:42:
utmp:x:43:
video:x:44:ubuntu
sasl:x:45:
plugdev:x:46:ubuntu
staff:x:50:
games:x:60:
users:x:100:
nogroup:x:65534:
systemd-network:x:998:
systemd-resolve:x:997:
messagebus:x:102:
ssh:x:103:
ubuntu:x:1000:
""",
    "etc/shadow": """root:*:19276:0:99999:7:::
daemon:*:19276:0:99999:7:::
bin:*:19276:0:99999:7:::
sys:*:19276:0:99999:7:::
sync:*:19276:0:99999:7:::
games:*:19276:0:99999:7:::
man:*:19276:0:99999:7:::
lp:*:19276:0:99999:7:::
mail:*:19276:0:99999:7:::
news:*:19276:0:99999:7:::
uucp:*:19276:0:99999:7:::
proxy:*:19276:0:99999:7:::
www-data:*:19276:0:99999:7:::
backup:*:19276:0:99999:7:::
list:*:19276:0:99999:7:::
irc:*:19276:0:99999:7:::
gnats:*:19276:0:99999:7:::
nobody:*:19276:0:99999:7:::
systemd-network:*:19276:0:99999:7:::
systemd-resolve:*:19276:0:99999:7:::
messagebus:*:19276:0:99999:7:::
sshd:*:19276:0:99999:7:::
ubuntu:*:19276:0:99999:7:::
""",
    "etc/hostname": f"{HOSTNAME}\n",
    "etc/hosts": f"""127.0.0.1 localhost
127.0.1.1 {HOSTNAME}

# The following lines are desirable for IPv6 capable hosts
::1     ip6-localhost ip6-loopback
fe00::0 ip6-localnet
ff00::0 ip6-mcastprefix
ff02::1 ip6-allnodes
ff02::2 ip6-allrouters
""",
    "etc/os-release": """PRETTY_NAME="Ubuntu 22.04.4 LTS"
NAME="Ubuntu"
VERSION_ID="22.04"
VERSION="22.04.4 LTS (Jammy Jellyfish)"
VERSION_CODENAME=jammy
ID=ubuntu
ID_LIKE=debian
HOME_URL="https://www.ubuntu.com/"
SUPPORT_URL="https://help.ubuntu.com/"
BUG_REPORT_URL="https://bugs.launchpad.net/ubuntu/"
PRIVACY_POLICY_URL="https://www.ubuntu.com/legal/terms-and-policies/privacy-policy"
UBUNTU_CODENAME=jammy
""",
    "etc/issue": "Ubuntu 22.04.4 LTS \\n \\l\n",
    "etc/issue.net": "Ubuntu 22.04.4 LTS\n",
    "etc/motd": """Welcome to Ubuntu 22.04.4 LTS (GNU/Linux 5.15.0-23-generic x86_64)

 * Documentation:  https://help.ubuntu.com
 * Management:     https://landscape.canonical.com
 * Support:        https://ubuntu.com/advantage

0 updates can be applied immediately.
""",
    "proc/version": "Linux version 5.15.0-23-generic (buildd@lcy02-amd64-058) (gcc (Ubuntu 11.2.0-19ubuntu1) 11.2.0, GNU ld (GNU Binutils for Ubuntu) 2.38) #25~22.04-Ubuntu SMP\n",
}

HOME_FILES = {
    ".bash_logout": """# ~/.bash_logout: executed by bash(1) when login shell exits.
if [ "$SHLVL" = 1 ]; then
    [ -x /usr/bin/clear_console ] && /usr/bin/clear_console -q
fi
""",
    ".bashrc": """# ~/.bashrc: executed by bash(1) for non-login shells.
case $- in
    *i*) ;;
      *) return;;
esac

HISTCONTROL=ignoreboth
shopt -s histappend
HISTSIZE=1000
HISTFILESIZE=2000

if [ -f /etc/bash_completion ]; then
    . /etc/bash_completion
fi
""",
    ".profile": """# ~/.profile: executed by the command interpreter for login shells.
if [ "$BASH" ]; then
    if [ -f ~/.bashrc ]; then
        . ~/.bashrc
    fi
fi

mesg n 2> /dev/null || true
""",
    ".sudo_as_admin_successful": "",
}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Derive a custom Cowrie fs.pickle and honeyfs overlay from Cowrie defaults."
    )
    parser.add_argument("--cowrie-root", required=True, type=Path)
    parser.add_argument("--work-dir", default=Path("/tmp/cowrie-custom-fs"), type=Path)
    return parser.parse_args()


def node_get(node, index, default=None):
    return node[index] if len(node) > index else default


def node_set(node, index, value):
    while len(node) <= index:
        node.append(None)
    node[index] = value


def node_children(node):
    return node_get(node, A_CONTENTS, [])


def walk(node, path=""):
    name = node_get(node, A_NAME, "")
    current = "/" if name == "/" else f"{path.rstrip('/')}/{name}"
    yield current, node
    if node_get(node, A_TYPE) == T_DIR:
        for child in node_children(node):
            yield from walk(child, current)


def find_node(root, relative_path):
    if relative_path in ("", "."):
        return root
    current = root
    for part in Path(relative_path).parts:
        if part in ("", "/"):
            continue
        current = next(
            (child for child in node_children(current) if node_get(child, A_NAME) == part),
            None,
        )
        if current is None:
            return None
    return current


def ensure_dir(root, relative_path, uid=0, gid=0, mode=0o755):
    current = root
    now = int(time.time())
    for part in Path(relative_path).parts:
        if part in ("", "/"):
            continue
        match = next(
            (child for child in node_children(current) if node_get(child, A_NAME) == part),
            None,
        )
        if match is None:
            match = [part, T_DIR, uid, gid, 4096, stat.S_IFDIR | mode, now, [], None, None]
            node_children(current).append(match)
        current = match
    return current


def ensure_file(root, relative_path, content, uid=0, gid=0, mode=0o644):
    parent = ensure_dir(root, str(Path(relative_path).parent), uid=0, gid=0)
    name = Path(relative_path).name
    node = next(
        (child for child in node_children(parent) if node_get(child, A_NAME) == name),
        None,
    )
    now = int(time.time())
    if node is None:
        node = [name, T_FILE, uid, gid, len(content), stat.S_IFREG | mode, now, [], None, None]
        node_children(parent).append(node)

    node_set(node, A_NAME, name)
    node_set(node, A_TYPE, T_FILE)
    node_set(node, A_UID, uid)
    node_set(node, A_GID, gid)
    node_set(node, A_SIZE, len(content))
    node_set(node, A_MODE, stat.S_IFREG | mode)
    if not node_get(node, A_CTIME):
        node_set(node, A_CTIME, now)
    node_set(node, A_CONTENTS, [])
    node_set(node, A_TARGET, None)
    node_set(node, A_REALFILE, None)
    return node


def load_pickle(path):
    with path.open("rb") as handle:
        try:
            return pickle.load(handle)
        except UnicodeDecodeError:
            handle.seek(0)
            return pickle.load(handle, encoding="utf-8")


def rename_default_home(root):
    phil = find_node(root, "home/phil")
    if phil is not None:
        node_set(phil, A_NAME, LOGIN_USER)
        node_set(phil, A_UID, LOGIN_UID)
        node_set(phil, A_GID, LOGIN_GID)
        node_set(phil, A_MODE, stat.S_IFDIR | 0o755)
        for child in node_children(phil):
            node_set(child, A_UID, LOGIN_UID)
            node_set(child, A_GID, LOGIN_GID)


def update_pickle_metadata(root):
    rename_default_home(root)
    ensure_dir(root, "home/ubuntu", LOGIN_UID, LOGIN_GID, 0o755)
    ensure_dir(root, "etc", 0, 0, 0o755)
    ensure_dir(root, "proc", 0, 0, 0o555)

    for relative_path, text in TEXT_FILES.items():
        mode = 0o644
        if relative_path == "etc/shadow":
            mode = 0o640
        ensure_file(root, relative_path, text.encode("utf-8"), 0, 0, mode)

    for name, text in HOME_FILES.items():
        ensure_file(
            root,
            f"home/ubuntu/{name}",
            text.encode("utf-8"),
            LOGIN_UID,
            LOGIN_GID,
            0o644,
        )


def write_text_file(path, text, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.chmod(path, mode)


def copy_and_patch_honeyfs(source, target):
    if target.exists():
        shutil.rmtree(target)
    if source.is_dir():
        shutil.copytree(source, target, copy_function=shutil.copy2)
    else:
        target.mkdir(parents=True)

    for relative_path, text in TEXT_FILES.items():
        mode = 0o644
        if relative_path == "etc/shadow":
            mode = 0o640
        write_text_file(target / relative_path, text, mode)

    home = target / "home" / LOGIN_USER
    home.mkdir(parents=True, exist_ok=True)
    os.chmod(home, 0o755)
    for name, text in HOME_FILES.items():
        write_text_file(home / name, text, 0o644)


def validate_no_phil(pickle_path, honeyfs):
    offenders = []
    if b"phil" in pickle_path.read_bytes().lower():
        offenders.append(str(pickle_path))

    for item in honeyfs.rglob("*"):
        if "phil" in item.name.lower():
            offenders.append(str(item))
            continue
        if item.is_file() and b"phil" in item.read_bytes().lower():
            offenders.append(str(item))

    if offenders:
        raise RuntimeError("Generated Cowrie filesystem still contains 'phil': " + ", ".join(offenders))


def validate_expected_markers(pickle_path, honeyfs):
    pickle_bytes = pickle_path.read_bytes()
    if pickle_path.stat().st_size < MIN_PICKLE_SIZE:
        raise RuntimeError(
            f"Generated pickle is unexpectedly small: {pickle_path.stat().st_size} bytes"
        )
    if LOGIN_USER.encode("ascii") not in pickle_bytes:
        raise RuntimeError(f"Generated pickle does not contain {LOGIN_USER}")
    if HOSTNAME.encode("ascii") not in (honeyfs / "etc" / "hostname").read_bytes():
        raise RuntimeError(f"Generated honeyfs does not contain {HOSTNAME}")
    if not (honeyfs / "home" / LOGIN_USER).is_dir():
        raise RuntimeError(f"Generated honeyfs does not contain /home/{LOGIN_USER}")


def main():
    args = parse_args()
    cowrie_root = args.cowrie_root.resolve()
    work_dir = args.work_dir.resolve()
    pickle_path = cowrie_root / "src" / "cowrie" / "data" / "fs.pickle"
    source_honeyfs = cowrie_root / "honeyfs"
    generated_pickle_path = work_dir / "fs.pickle"
    generated_honeyfs = work_dir / "honeyfs"

    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)

    tree = load_pickle(pickle_path)
    update_pickle_metadata(tree)
    with generated_pickle_path.open("wb") as handle:
        pickle.dump(tree, handle)

    copy_and_patch_honeyfs(source_honeyfs, generated_honeyfs)

    validate_no_phil(generated_pickle_path, generated_honeyfs)
    validate_expected_markers(generated_pickle_path, generated_honeyfs)

    shutil.move(generated_pickle_path, pickle_path)
    if source_honeyfs.exists():
        shutil.rmtree(source_honeyfs)
    shutil.copytree(generated_honeyfs, source_honeyfs, copy_function=shutil.copy2)


if __name__ == "__main__":
    main()
