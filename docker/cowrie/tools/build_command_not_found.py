#!/usr/bin/env python3
"""Build the command -> package table of Ubuntu's command-not-found for the Ubuntu personas
(docker/cowrie/dist/tpot_personas/command_not_found/ubuntu.txt.gz).

The input is a folder with the cnf indexes of the releases, as data:
    http://archive.ubuntu.com/ubuntu/dists/<suite>/<component>/cnf/Commands-amd64.xz
saved as ub-<suite>-<component>-cnf-amd64.xz (see the README next to the table). Nothing of them
is run. Run it with `python3 -I docker/cowrie/tools/build_command_not_found.py DOWNLOADS [OUT]`.

The table has the rules of command-not-found 22.04 / 23.04 (CommandNotFound/db/creator.py): the
suites and components of the persona's sources, no -backports; a package's priority is that of its
component (main 120, universe 100, restricted 60, multiverse 20) plus its priority-bonus; of a
package in several suites the highest version counts; visible-pkgname and ignore-commands are
honoured. The database is filled as creator.py fills it (sqlite3) and read with its lookup (ORDER BY
priority DESC; providers of one priority in the order they went in). Only the commands of COMMANDS
are kept (those a session is likely to type), one line per release and command:
    <release letter> <command> install <package>=<version>[,<package>=<version>...]
(the version only of several packages, one alone is named without it)
and for a command of COMMANDS no package has, the spelling suggestions of command-not-found
(CommandNotFound.similar_words: one deletion, transposition, replacement or insertion; up to 15):
    <release letter> <command> similar <command>/<package>=<version>[,...]
    <release letter> <command> similar-count <n>         (more than 15: "there are <n> similar ones")
"""
import gzip
import lzma
import re
import sys
from pathlib import Path

PRIORITIES = {"main": 120, "universe": 100, "contrib": 80, "non-free-firmware": 60, "restricted": 60,
              "non-free": 40, "multiverse": 20}
# the suites and components of the personas' sources.list (as tpot_personas.servers writes them)
RELEASES = {
    "n": [(suite, component) for suite in ("noble", "noble-updates", "noble-security")
          for component in ("main", "restricted", "universe", "multiverse")],
    "j": [("jammy", "main"), ("jammy", "restricted"), ("jammy", "universe"), ("jammy-updates", "main"),
          ("jammy-updates", "restricted"), ("jammy-security", "main"), ("jammy-security", "restricted"),
          ("jammy-security", "universe")],
}
# the commands of the table: tools of downloads and scans, compilers, interpreters, servers and
#   system tools a minimal cloud image does not have
COMMANDS = sorted(set("""
7z 7za ab ack ansible apache2 aria2c arp arping asciinema autossh aws awk axel bc bind blkid bmon
bpytop btop busybox bzip2 cargo cc clang cmake composer conntrack cowsay cpulimit crontab curl dig
docker docker-compose dos2unix elinks emacs ethtool expect fail2ban-client fdisk ffmpeg figlet file
finger fping ftp g++ gcc gdb git go gpg hashcat hping3 htop httpd hydra ifconfig ifstat iftop inetd
iotop ip ipcalc iperf iperf3 iptables irssi java javac jq john kubectl links lnav locate lsof lua
lynx make masscan mc mkpasswd mongo mosh mtr mutt mysql nano nasm nc ncat ncdu neofetch net netcat
netstat nethogs nginx ngrep nikto nload nmap node nodejs npm nping nslookup ntpdate openvpn p7zip
perl php pip pip3 ping proxychains proxychains4 psql pv pwgen python2 python3 qemu-img rar
redis-cli redis-server route rsync ruby rustc screen sensors sftp sl smbclient snmpwalk socat sqlite3
sqlmap ssh sshpass sshd strace stress svn tcpdump tcptraceroute telnet tftp tmux tor traceroute
tree tshark ufw unrar unzip upx vim virtualenv w3m wget whois wireshark xxd xz yarn zip zmap zsh
""".split()))


def _order(char):
    if char == "~":
        return -1
    if char.isalpha():
        return ord(char)
    return ord(char) + 256 if char else 0


def _verrevcmp(a, b):
    """dpkg's verrevcmp (lib/dpkg/version.c)."""
    i = j = 0
    while i < len(a) or j < len(b):
        diff = 0
        while (i < len(a) and not a[i].isdigit()) or (j < len(b) and not b[j].isdigit()):
            ac = _order(a[i]) if i < len(a) and not a[i].isdigit() else 0
            bc = _order(b[j]) if j < len(b) and not b[j].isdigit() else 0
            if ac != bc:
                return ac - bc
            i, j = i + 1, j + 1
        while i < len(a) and a[i] == "0":
            i += 1
        while j < len(b) and b[j] == "0":
            j += 1
        while i < len(a) and a[i].isdigit() and j < len(b) and b[j].isdigit():
            if not diff:
                diff = ord(a[i]) - ord(b[j])
            i, j = i + 1, j + 1
        if i < len(a) and a[i].isdigit():
            return 1
        if j < len(b) and b[j].isdigit():
            return -1
        if diff:
            return diff
    return 0


def version_compare(a, b):
    """dpkg --compare-versions a b: negative, 0 or positive."""
    def split(version):
        epoch, sep, rest = version.partition(":")
        if not sep:
            epoch, rest = "0", version
        upstream, sep, revision = rest.rpartition("-")
        if not sep:
            upstream, revision = rest, ""
        return int(epoch or 0), upstream, revision
    (ea, ua, ra), (eb, ub, rb) = split(a), split(b)
    if ea != eb:
        return ea - eb
    return _verrevcmp(ua, ub) or _verrevcmp(ra, rb)


def stanzas(text):
    for block in text.split("\n\n"):
        fields = {}
        for line in block.splitlines():
            key, sep, value = line.partition(": ")
            if sep:
                fields[key.strip()] = value.strip()
        if fields:
            yield fields


# the database of command-not-found (CommandNotFound/db/creator.py)
SCHEMA = """
CREATE TABLE commands ([cmdID] INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, [pkgID] INTEGER NOT NULL, [command] TEXT);
CREATE TABLE packages ([pkgID] INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, [name] TEXT, [version] TEXT,
                       [component] TEXT, [priority] INTEGER);
CREATE INDEX idx_commands_command ON commands (command);
CREATE INDEX idx_packages_name ON packages (name);
"""


def list_name(suite, component):
    """The name apt gives the index in /var/lib/apt/lists (the host of the persona's sources)."""
    host = "security.ubuntu.com" if suite.endswith("-security") else "archive.ubuntu.com"
    return f"{host}_ubuntu_dists_{suite}_{component}_cnf_Commands-amd64.lz4"


def table(downloads, files):
    """The database (sqlite3, in memory) of the cnf indexes files (suite, component), filled as
    creator.py fills it (a higher version replaces the package with the priority of its
    component; the files in the order of their names in /var/lib/apt/lists; on a real system it is the
    order of the directory, so providers of one priority may come in another order there)."""
    import sqlite3
    con = sqlite3.connect(":memory:")
    con.executescript(SCHEMA)
    for suite, component in sorted(files, key=lambda pair: list_name(*pair)):
        path = Path(downloads) / f"ub-{suite}-{component}-cnf-amd64.xz"
        blocks = list(stanzas(lzma.decompress(path.read_bytes()).decode("utf-8")))
        for fields in blocks[1:]:
            name = fields.get("visible-pkgname") or fields["name"]
            version = fields.get("version", "")
            ignored = set(fields.get("ignore-commands", "").split(","))
            priority = PRIORITIES.get(component, 0) + int(fields.get("priority-bonus", "0"))
            for command in fields.get("commands", "").split(","):
                if not command or command in ignored:
                    continue
                known = con.execute("SELECT packages.pkgID, name, version FROM commands INNER JOIN packages ON "
                                    "packages.pkgID = commands.pkgID WHERE commands.command=? AND packages.name=?",
                                    (command, name)).fetchone()
                if known:
                    if version_compare(version, known[2]) > 0:
                        con.execute("DELETE FROM packages WHERE pkgID=?", (known[0],))
                        con.execute("DELETE FROM commands WHERE pkgID=?", (known[0],))
                    else:
                        continue
                row = con.execute("SELECT pkgID FROM packages WHERE name=?", (name,)).fetchone()
                pkg_id = row[0] if row else con.execute(
                    "INSERT INTO packages (name, version, component, priority) VALUES (?, ?, ?, ?)",
                    (name, version, component, priority)).lastrowid
                con.execute("INSERT INTO commands (command, pkgID) VALUES (?, ?)", (command, pkg_id))
    return con


def lookup(con, command):
    return con.execute("SELECT packages.name, packages.version FROM commands INNER JOIN packages ON "
                       "packages.pkgID = commands.pkgID WHERE commands.command=? ORDER BY packages.priority DESC",
                       (command,)).fetchall()


def similar_words(word):
    """CommandNotFound.similar_words: the spellings at distance 1."""
    alphabet = "abcdefghijklmnopqrstuvwxyz-_0123456789"
    s = [(word[:i], word[i:]) for i in range(len(word) + 1)]
    deletes = [a + b[1:] for a, b in s if b]
    transposes = [a + b[1] + b[0] + b[2:] for a, b in s if len(b) > 1]
    replaces = [a + c + b[1:] for a, b in s for c in alphabet if b]
    inserts = [a + c + b for a, b in s for c in alphabet]
    return set(deletes + transposes + replaces + inserts)


def lines(downloads):
    out = []
    for letter, files in RELEASES.items():
        con = table(downloads, files)
        for command in COMMANDS:
            found = lookup(con, command)
            if found:
                # one package: command-not-found names no version (left out of the table)
                if len(found) == 1:
                    found = [(found[0][0], "")]
                out.append(f"{letter} {command} install " + ",".join(f"{name}={version}" for name, version in found))
                continue
            similar = sorted((word, name, version) for word in similar_words(command) if 3 <= len(command)
                             for name, version in lookup(con, word))
            if len(similar) > 15:
                out.append(f"{letter} {command} similar-count {len(similar)}")
            elif similar:
                out.append(f"{letter} {command} similar " + ",".join(f"{w}/{n}={v}" for w, n, v in similar))
    return out


def main(argv):
    if len(argv) not in (2, 3):
        raise SystemExit(__doc__)
    out = Path(argv[2]) if len(argv) == 3 else (Path(__file__).resolve().parent.parent / "dist" / "tpot_personas"
                                                / "command_not_found" / "ubuntu.txt.gz")
    data = "".join(f"{line}\n" for line in lines(argv[1])).encode("utf-8")
    out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.GzipFile(out, "wb", mtime=0) as f:
        f.write(data)
    count = data.count(b"\n")
    print(f"{out}: {count} lines")


if __name__ == "__main__":
    main(sys.argv)
