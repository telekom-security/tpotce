#!/usr/bin/env python3
"""Build the package name lists of the personas (docker/cowrie/dist/tpot_personas/package_names/).

The input is a folder with the package indexes of the releases (downloaded as data, see the README
next to the lists for the file names and URLs). Only the names are read; nothing of the indexes is
run. Run it with `python3 -I docker/cowrie/tools/build_package_names.py DOWNLOADS [OUT]`.

apt.txt.gz     one line per name: "<name> <releases>", releases the letters of APT_RELEASES; per
               release the names of its index that are in the Debian popcon top POPCON_TOP, the
               whole index of Ubuntu main and of the Raspberry Pi archive, and EXTRA
yum.txt.gz     the names of CentOS 7.9.2009 (os, updates, extras)
opkg.txt.gz    one line per name: "<name> <releases> <feed>", the feeds of OpenWrt 21.02.7 / 22.03.7
"""
import gzip
import lzma
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

POPCON_TOP = 20000
NAME = re.compile(r"[a-z0-9][a-z0-9+.-]{1,99}")
RPM_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9+._-]{0,99}")
# release letter -> index files of the download folder (the first: main, the others added)
APT_RELEASES = {
    "b": ("deb-bookworm.xz",),
    "t": ("deb-trixie.xz",),
    "r": ("deb-bookworm-arm64.xz", "deb-bookworm-nff-arm64.xz", "rpi-bookworm-arm64.gz"),
    "j": ("ub-jammy-main.xz", "ub-jammy-universe.xz"),
    "n": ("ub-noble-main.xz", "ub-noble-universe.xz"),
}
# indexes taken whole (curated archives), not cut by popcon
APT_WHOLE = {"r": ("rpi-bookworm-arm64.gz",), "j": ("ub-jammy-main.xz",), "n": ("ub-noble-main.xz",)}
# names scripts install that rank low in popcon: kept where the release has them
EXTRA = ("arp-scan", "cpulimit", "dsniff", "ettercap-text-only", "hping3", "hydra", "masscan", "medusa",
         "nbtscan", "ncrack", "netdiscover", "proxychains", "proxychains4", "sshpass", "torsocks", "xmrig",
         "zmap")
YUM_FILES = ("c7-os-primary.xml.gz", "c7-updates-primary.xml.gz", "c7-extras-primary.xml.gz")
OPKG_RELEASES = {"a": "21.02.7", "b": "22.03.7"}
OPKG_FEEDS = ("core", "base", "luci", "packages", "routing", "telephony")


def text(path):
    if path.name.endswith(".xz"):
        return lzma.open(path, "rt", encoding="utf-8", errors="replace")
    return gzip.open(path, "rt", encoding="utf-8", errors="replace")


def deb_names(path):
    with text(path) as f:
        names = {line[9:].strip() for line in f if line.startswith("Package: ")}
    return {name for name in names if NAME.fullmatch(name)}


def rpm_names(path):
    found = set()
    with gzip.open(path, "rb") as f:
        for _, element in ET.iterparse(f):
            if element.tag.endswith("}name") and element.text and RPM_NAME.fullmatch(element.text):
                found.add(element.text)
            if element.tag.endswith("}package"):
                element.clear()
    return found


def popcon(path, limit):
    top = set()
    with text(path) as f:
        for line in f:
            parts = line.split()
            if len(parts) > 2 and parts[0].isdigit() and int(parts[0]) <= limit and NAME.fullmatch(parts[1]):
                top.add(parts[1])
    return top


def write(path, lines):
    data = "".join(line + "\n" for line in lines).encode()
    path.write_bytes(gzip.compress(data, 9, mtime=0))
    return len(lines), path.stat().st_size


def build(downloads, out):
    top = popcon(downloads / "popcon-by_inst.gz", POPCON_TOP) | set(EXTRA)
    releases = {}
    for letter, files in APT_RELEASES.items():
        index = set().union(*(deb_names(downloads / name) for name in files))
        whole = set().union(set(), *(deb_names(downloads / name) for name in APT_WHOLE.get(letter, ())))
        releases[letter] = (index & top) | whole
    names = sorted(set().union(*releases.values()))
    print("apt", write(out / "apt.txt.gz",
                       [f"{n} {''.join(k for k in APT_RELEASES if n in releases[k])}" for n in names]))
    print("yum", write(out / "yum.txt.gz", sorted(set().union(*(rpm_names(downloads / n) for n in YUM_FILES)))))
    feeds = {}
    for letter, version in OPKG_RELEASES.items():
        for feed in OPKG_FEEDS:
            for name in deb_names(downloads / f"ow-{version}-{feed}.gz"):
                feeds.setdefault(name, {})[letter] = feed
    print("opkg", write(out / "opkg.txt.gz",
                        [f"{n} {''.join(sorted(feeds[n]))} {feeds[n][max(feeds[n])]}" for n in sorted(feeds)]))


if __name__ == "__main__":
    here = Path(__file__).resolve().parent
    build(Path(sys.argv[1]), Path(sys.argv[2]) if len(sys.argv) > 2
          else here.parent / "dist" / "tpot_personas" / "package_names")
