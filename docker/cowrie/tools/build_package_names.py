#!/usr/bin/env python3
"""Build the package name lists of the personas (docker/cowrie/dist/tpot_personas/package_names/).

The input is a folder with the package indexes of the releases (downloaded as data, see the README
next to the lists for the file names and URLs). Only the names and the Provides are read; nothing
of the indexes is run. Run it with `python3 -I docker/cowrie/tools/build_package_names.py DOWNLOADS [OUT]`.

apt.txt.gz     one line per name: "<name> <releases>", releases the letters of APT_RELEASES; per
               release the names of the indexes its sources name (components and pockets): those of
               the whole indexes all, of the others the names in the Debian popcon top POPCON_TOP and
               EXTRA; no names of a kernel ABI (the persona adds those of its own kernel)
apt-virtual.txt.gz  one line per virtual name: "<name> <releases> <provider,...>", the packages of the
               release's list that provide a name no package of its indexes has (not the names of
               libraries and language modules, LIBRARY)
yum.txt.gz     the names of CentOS 7.9.2009 (os, updates, extras) and "<name> <provider>" for the
               plain names their packages provide (no sonames)
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
# names of a kernel ABI (linux-image-6.1.0-18-amd64, linux-modules-nvidia-550-5.15.0-91-generic)
KERNEL_ABI = re.compile(r"linux-.*\d+\.\d+\.\d+(-\d+|\+)")
# virtual names of libraries and language modules: left out of apt-virtual.txt.gz
LIBRARY = re.compile(r"(lib|python3?-|golang-|ruby-|node-|php-|r-cran-|perl|elpa-|fonts-|texlive-|ocaml)")
SONAME = re.compile(r".*\.so(\.|$)")
RPM_NS = "{http://linux.duke.edu/metadata/common}"
RPM_FORMAT_NS = "{http://linux.duke.edu/metadata/rpm}"
_DEB = [f"deb-{suite}-main-amd64.xz" for suite in ("bookworm", "bookworm-updates", "bookworm-security")]
_TRIXIE = [f"deb-{suite}-main-amd64.xz"
           for suite in ("trixie", "trixie-updates", "trixie-backports", "trixie-security")]
_PI = [f"deb-{suite}-{component}-arm64.xz" for suite in ("bookworm", "bookworm-updates", "bookworm-security")
       for component in ("main", "contrib", "non-free", "non-free-firmware")]
_NOBLE = [(suite, component) for suite in ("noble", "noble-updates", "noble-backports", "noble-security")
          for component in ("main", "restricted", "universe", "multiverse")]
_JAMMY = [("jammy", "main"), ("jammy", "restricted"), ("jammy", "universe"), ("jammy-updates", "main"),
          ("jammy-updates", "restricted"), ("jammy-security", "main"), ("jammy-security", "restricted"),
          ("jammy-security", "universe")]


def _ubuntu(pairs):
    files = [(f"ub-{suite}-{component}-amd64.xz", component) for suite, component in pairs]
    return ([name for name, component in files if component in ("main", "restricted")],
            [name for name, component in files if component not in ("main", "restricted")])


# release letter -> (indexes taken whole, indexes cut by popcon): the components and pockets of the
#   persona's sources
APT_RELEASES = {
    "b": ([], _DEB),
    "t": ([], _TRIXIE),
    "r": (["rpi-bookworm-main-arm64.gz"], _PI),
    "j": _ubuntu(_JAMMY),
    "n": _ubuntu(_NOBLE),
}
# names scripts install that rank low in popcon: kept where the release has them
EXTRA = ("arp-scan", "cpulimit", "dsniff", "ettercap-text-only", "hping3", "hydra", "masscan", "medusa",
         "nbtscan", "ncrack", "netdiscover", "proxychains", "proxychains4", "sshpass", "torsocks", "xmrig",
         "zmap")
YUM_FILES = ("c7-os-primary.xml.gz", "c7-updates-primary.xml.gz", "c7-extras-primary.xml.gz")
OPKG_RELEASES = {"a": "21.02.7", "b": "22.03.7"}
OPKG_FEEDS = ("core", "base", "luci", "packages", "routing", "telephony")
# words no persona output may carry (tpot_personas.validate.FORBIDDEN_MARKERS): names with them are left out
MARKERS = ("phil", "svr04")


def text(path):
    if path.name.endswith(".xz"):
        return lzma.open(path, "rt", encoding="utf-8", errors="replace")
    return gzip.open(path, "rt", encoding="utf-8", errors="replace")


def deb_index(path):
    """{name: {provided names}} of a Packages index."""
    found, name = {}, None
    with text(path) as f:
        for line in f:
            if line.startswith("Package: "):
                name = line[9:].strip()
                found.setdefault(name, set())
            elif line.startswith("Provides: ") and name:
                for item in line[10:].split(","):
                    provided = item.split("(")[0].strip()
                    if NAME.fullmatch(provided):
                        found[name].add(provided)
    return {n: p for n, p in found.items() if NAME.fullmatch(n)}


def rpm_index(path):
    """{name: {provided plain names}} of a primary.xml."""
    found = {}
    with gzip.open(path, "rb") as f:
        for _, element in ET.iterparse(f):
            if element.tag != f"{RPM_NS}package":
                continue
            name = element.findtext(f"{RPM_NS}name") or ""
            block = element.find(f"{RPM_NS}format/{RPM_FORMAT_NS}provides")
            provided = {entry.get("name", "") for entry in (block if block is not None else ())}
            if RPM_NAME.fullmatch(name):
                found.setdefault(name, set()).update(p for p in provided if RPM_NAME.fullmatch(p) and p != name)
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
    lines = [line for line in lines if not any(marker in line for marker in MARKERS)]
    data = "".join(line + "\n" for line in lines).encode()
    path.write_bytes(gzip.compress(data, 9, mtime=0))
    return len(lines), path.stat().st_size


def merge(indexes):
    merged = {}
    for index in indexes:
        for name, provided in index.items():
            merged.setdefault(name, set()).update(provided)
    return merged


def build(downloads, out):
    top = popcon(downloads / "popcon-by_inst.gz", POPCON_TOP) | set(EXTRA)
    releases, virtuals = {}, {}
    for letter, (whole, cut) in APT_RELEASES.items():
        whole_index = merge(deb_index(downloads / name) for name in whole)
        every = merge((whole_index, merge(deb_index(downloads / name) for name in cut)))
        kept = {name for name in every if (name in whole_index or name in top) and not KERNEL_ABI.match(name)}
        releases[letter] = kept
        providers = {}
        for name in sorted(kept):
            for provided in every[name]:
                if provided not in every and not KERNEL_ABI.match(provided) and not LIBRARY.match(provided):
                    providers.setdefault(provided, []).append(name)
        virtuals[letter] = providers
    names = sorted(set().union(*releases.values()))
    print("apt", write(out / "apt.txt.gz",
                       [f"{n} {''.join(k for k in APT_RELEASES if n in releases[k])}" for n in names]))
    merged = {}
    for letter in APT_RELEASES:
        for virtual, providers in virtuals[letter].items():
            merged.setdefault((virtual, ",".join(providers)), []).append(letter)
    print("apt-virtual", write(out / "apt-virtual.txt.gz",
                               [f"{v} {''.join(k)} {p}" for (v, p), k in sorted(merged.items())]))
    index = merge(rpm_index(downloads / name) for name in YUM_FILES)
    provides = {}
    for name in sorted(index):
        for provided in index[name]:
            if provided not in index and not SONAME.match(provided):
                provides.setdefault(provided, []).append(name)
    # yum takes the provider with the shortest name (unverified: yum also weighs arch and repository)
    print("yum", write(out / "yum.txt.gz", sorted(index) + [f"{v} {min(p, key=lambda n: (len(n), n))}"
                                                            for v, p in sorted(provides.items())]))
    feeds = {}
    for letter, version in OPKG_RELEASES.items():
        for feed in OPKG_FEEDS:
            for name in deb_index(downloads / f"ow-{version}-{feed}.gz"):
                feeds.setdefault(name, {})[letter] = feed
    print("opkg", write(out / "opkg.txt.gz",
                        [f"{n} {''.join(sorted(feeds[n]))} {feeds[n][max(feeds[n])]}" for n in sorted(feeds)]))


if __name__ == "__main__":
    here = Path(__file__).resolve().parent
    build(Path(sys.argv[1]), Path(sys.argv[2]) if len(sys.argv) > 2
          else here.parent / "dist" / "tpot_personas" / "package_names")
