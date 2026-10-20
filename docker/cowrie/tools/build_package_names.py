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
               plain names and the listed programs (/bin, /sbin, /usr/bin, /usr/sbin) their packages provide
               (no sonames)
opkg.txt.gz    one line per name: "<name> <releases> <feed>", the feeds of OpenWrt 21.02.7 / 22.03.7
apt-meta.txt.gz  one line per candidate: "<name>\t<releases>\t<version>\t<component>\t<pocket>\t<installed
               size>\t<size>\t<all or ->\t<section>\t<maintainer>\t<description>" of the names in the Debian popcon top META_TOP, EXTRA and the
               packages the personas model (MODELLED): the version apt takes on the release (the highest
               of the release and its -updates / -security pockets, of -backports only without another)
opkg-meta.txt.gz  "<name>\t<releases>\t<version>\t<description>" of every package of the feeds, the
               description on the first row of a name only
yum-meta.txt.gz  "<name>\t<arch>\t<epoch:version-release>\t<repo>\t<size>\t<installed size>\t<summary>"
               of the newest x86_64 / noarch build of each name of CentOS 7.9.2009 (base = os, updates, extras)
dnf.txt.gz     "<name> <baseos or appstream>" of the repositories of AlmaLinux 9.4 (vault), the names of
               the AlmaLinux brand as those of Red Hat (REBRAND), and "<name> > <provider>" for the plain names
               and the programs (/usr/bin, /usr/sbin) their packages provide
rpm-man.txt.gz "<manual page> <releases> <package>": the manual pages (sections 1 and 8) of the programs of
               each package of CentOS 7.9.2009 (c) and of RHEL 9 (r, AlmaLinux 9.4 vault, the brand names as
               those of Red Hat), as the filelists of the repositories name them
"""
import gzip
import lzma
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

POPCON_TOP = 20000
# the names whose version, component and description apt-meta.txt.gz keeps
META_TOP = 2000
# the names (popcon, as rpm names them too) whose description yum-meta and dnf-meta keep
RPM_DESCRIBED = 3000
# the releases of deb-files-<letter>.txt (Raspberry Pi OS takes Debian 12's)
DEB_FILE_RELEASES = ("b", "t", "j", "n")
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


# release letter -> (indexes taken whole: main of Debian, main and restricted of Ubuntu, the Raspberry Pi
#   archive; indexes cut by popcon): the components and pockets of the persona's sources
def _debian(files):
    """(indexes taken whole: main, the others cut by popcon) of a Debian release."""
    return [name for name in files if "-main-" in name], [name for name in files if "-main-" not in name]


APT_RELEASES = {
    "b": _debian(_DEB),
    "t": _debian(_TRIXIE),
    "r": (["rpi-bookworm-main-arm64.gz"] + _debian(_PI)[0], _debian(_PI)[1]),
    "j": _ubuntu(_JAMMY),
    "n": _ubuntu(_NOBLE),
}
# names scripts install that rank low in popcon: kept where the release has them
EXTRA = ("arp-scan", "cpulimit", "dsniff", "ettercap-text-only", "hping3", "hydra", "masscan", "medusa",
         "nbtscan", "ncrack", "netdiscover", "proxychains", "proxychains4", "sshpass", "torsocks", "xmrig",
         "zmap",
         # services the personas run or that scripts set up
         "inetutils-telnetd", "telnetd", "openbsd-inetd", "inetutils-inetd", "isc-dhcp-client", "isc-dhcp-common",
         "network-manager", "dhcpcd-base", "dhcpcd5", "udhcpc", "telnet", "inetutils-telnet")
YUM_FILES = ("c7-os-primary.xml.gz", "c7-updates-primary.xml.gz", "c7-extras-primary.xml.gz")
OPKG_RELEASES = {"a": "21.02.7", "b": "22.03.7"}
OPKG_FEEDS = ("core", "base", "luci", "packages", "routing", "telephony")
DNF_REPOS = {"baseos": "el94-BaseOS-primary.xml.gz", "appstream": "el94-AppStream-primary.xml.gz"}
# names of the AlmaLinux brand and those of Red Hat Enterprise Linux 9 (None: RHEL has no such package)
REBRAND = {"almalinux-release": "redhat-release", "almalinux-logos": "redhat-logos",
           "almalinux-logos-httpd": "redhat-logos-httpd", "almalinux-logos-ipa": "redhat-logos-ipa",
           "almalinux-backgrounds": "redhat-backgrounds", "almalinux-indexhtml": "redhat-indexhtml",
           "lorax-templates-almalinux": "lorax-templates-rhel", "almalinux-gpg-keys": None, "almalinux-repos": None}
# the rebuild tag of AlmaLinux in a release (1.24.0-1.module_el9.4.0+106+015ce7f8.1.alma.1)
ALMA_TAG = re.compile(r"\.alma(\.\d+)?(?=$|\.src\.rpm$)")
# the programs of a package that yum and dnf take as a name to install (CentOS 7 has /bin and /sbin of its own)
EL7_PROGRAM_PATH = re.compile(r"/(usr/)?s?bin/[A-Za-z0-9][A-Za-z0-9+._-]{0,99}")
PROGRAM_PATH = re.compile(r"/usr/s?bin/[A-Za-z0-9][A-Za-z0-9+._-]{0,99}")
# the pocket of an index ("-" the release itself) and the order apt reads them in (the sources' order)
POCKETS = ("-", "updates", "backports", "security")
# words no persona output may carry (tpot_personas.validate.FORBIDDEN_MARKERS): names with them are left out
MARKERS = ("phil", "svr04")


def deb_stanzas(path):
    """The fields of each package of a Packages index (the first line of a field, continuation lines
    joined with a space; the first line alone as "<field>:first")."""
    entry, key = {}, None
    with text(path) as f:
        for line in f:
            if not line.strip():
                if entry:
                    yield entry
                entry, key = {}, None
            elif line[0] in " \t":
                if key:
                    entry[key] = f"{entry[key]} {line.strip()}".strip()
            elif ":" in line:
                key, _, value = line.partition(":")
                entry[key] = entry[f"{key}:first"] = value.strip()
    if entry:
        yield entry


def _version_part(a, b):
    """dpkg's verrevcmp: digits as numbers, ~ before everything, letters before other characters."""
    def order(char):
        if char.isdigit():
            return 0
        if char == "~":
            return -1
        if char.isalpha():
            return ord(char)
        return ord(char) + 256 if char else 0

    i = j = 0
    while i < len(a) or j < len(b):
        diff = 0
        while (i < len(a) and not a[i].isdigit()) or (j < len(b) and not b[j].isdigit()):
            ac = order(a[i]) if i < len(a) else 0
            bc = order(b[j]) if j < len(b) else 0
            if ac != bc:
                return -1 if ac < bc else 1
            i, j = i + 1, j + 1
        while i < len(a) and a[i] == "0":
            i += 1
        while j < len(b) and b[j] == "0":
            j += 1
        while i < len(a) and a[i].isdigit() and j < len(b) and b[j].isdigit():
            if not diff:
                diff = (a[i] > b[j]) - (a[i] < b[j])
            i, j = i + 1, j + 1
        if i < len(a) and a[i].isdigit():
            return 1
        if j < len(b) and b[j].isdigit():
            return -1
        if diff:
            return diff
    return 0


def deb_compare(a, b):
    """-1, 0 or 1 as dpkg --compare-versions orders two versions (epoch, upstream, revision)."""
    def split(version):
        # the epoch ends at the first colon, the revision begins after the last hyphen
        epoch, _, rest = version.partition(":") if ":" in version else ("0", "", version)
        upstream, _, revision = rest.rpartition("-") if "-" in rest else (rest, "", "")
        return int(epoch) if epoch.isdigit() else 0, upstream, revision

    (ea, ua, ra), (eb, ub, rb) = split(a), split(b)
    if ea != eb:
        return -1 if ea < eb else 1
    return _version_part(ua, ub) or _version_part(ra, rb)


def apt_meta(indexes, keep, pocket_of):
    """The rows of apt-meta.txt.gz: indexes {letter: [index paths in the order of the sources]}, keep
    {letter: names}, pocket_of(path) the pocket of an index."""
    rows = {}
    for letter, paths in indexes.items():
        best = {}
        for path in paths:
            pocket = pocket_of(path)
            for entry in deb_stanzas(path):
                name = entry.get("Package", "")
                if name not in keep[letter] or "Version" not in entry:
                    continue
                old = best.get(name)
                # backports only without another candidate; else the highest, the first of equal ones
                replace = old is None
                if not replace:
                    old_backports, backports = old[1] == "backports", pocket == "backports"
                    replace = (old_backports and not backports) or (
                        old_backports == backports and deb_compare(entry["Version"], old[0]["Version"]) > 0)
                if replace:
                    best[name] = (entry, pocket)
        for name, (entry, pocket) in best.items():
            section = entry.get("Section", "")
            component = section.split("/", 1)[0] if "/" in section else "main"
            key = (name, entry["Version"], component, pocket, entry.get("Installed-Size", ""), entry.get("Size", ""),
                   "all" if entry.get("Architecture") == "all" else "-", section.rsplit("/", 1)[-1],
                   entry.get("Maintainer", ""), entry.get("Priority", "optional"),
                   "yes" if entry.get("Essential") == "yes" else "-",
                   entry.get("Description:first", entry.get("Description", "")))
            rows.setdefault(key, []).append(letter)
    return ["\t".join([key[0], "".join(letters), *key[1:]]) for key, letters in sorted(rows.items())]


def base_names(indexes):
    """{letter: names of Priority required and important} of the indexes: the base system of a release."""
    found = {}
    for letter, paths in indexes.items():
        found[letter] = {entry["Package"] for path in paths for entry in deb_stanzas(path)
                         if entry.get("Priority") in ("required", "important") and "Package" in entry}
    return found


def deb_base(downloads, base):
    """The rows of deb-base.txt.gz: "<name> <releases>" of the base system of each release: Priority required and
    important (base) and the packages of the official image (deb-base-<letter>.txt, recorded with dpkg-query -W;
    Raspberry Pi OS takes Debian 12's)."""
    rows = {}
    for letter, names in base.items():
        image = downloads / f"deb-base-{'b' if letter == 'r' else letter}.txt"
        for name in names | set(image.read_text(encoding="utf-8").split()):
            rows.setdefault(name, []).append(letter)
    return [f"{name} {''.join(sorted(letters))}" for name, letters in sorted(rows.items())]


def deb_files(downloads, letters):
    """The rows of deb-files.txt.gz: "<path> <releases> <package>" of the programs in /bin, /sbin, /usr/bin and
    /usr/sbin that the packages of the official images record (deb-files-<letter>.txt, recorded with dpkg -L in
    the images; Raspberry Pi OS has the paths of Debian 12)."""
    rows = {}
    for letter in letters:
        for line in (downloads / f"deb-files-{letter}.txt").read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) == 2:
                rows.setdefault((parts[0], parts[1]), []).append(letter)
    return [f"{path} {''.join(found)} {package}" for (path, package), found in sorted(rows.items())]


def opkg_meta(indexes):
    """The rows of opkg-meta.txt.gz: indexes {letter: [feed index paths]}; the description is on the first
    row of a name (the feeds of both releases have nearly always the same)."""
    rows, described = {}, set()
    for letter, paths in indexes.items():
        for path in paths:
            for entry in deb_stanzas(path):
                name = entry.get("Package", "")
                if NAME.fullmatch(name):
                    rows.setdefault((name, entry.get("Version", "")), [[], entry.get("Description", "")])[0].append(
                        letter)
    found = []
    for (name, version), (letters, description) in sorted(rows.items()):
        found.append("\t".join([name, "".join(letters), version, "" if name in described else description]))
        described.add(name)
    return found


def yum_meta(paths, described=frozenset()):
    """The rows of yum-meta.txt.gz / dnf-meta.txt.gz: the newest x86_64 / noarch build of each name of the
    repositories (paths {repo: primary.xml path}, in the order yum reads them) with its URL, license, group,
    the description (newlines as \\n) of the names of described and its source package. The builds of a module
    stream (.module in their release) are left out: RHEL 9 enables no stream by default, so dnf installs the
    build outside the streams (nginx 1.20.1, not the 1.24 of nginx:1.24) and finds no name only a stream has."""
    best = {}
    for repo, path in paths.items():
        with gzip.open(path, "rb") as f:
            for _, element in ET.iterparse(f):
                if element.tag != f"{RPM_NS}package":
                    continue
                name, arch = element.findtext(f"{RPM_NS}name") or "", element.findtext(f"{RPM_NS}arch") or ""
                version, size = element.find(f"{RPM_NS}version"), element.find(f"{RPM_NS}size")
                if RPM_NAME.fullmatch(name) and arch in ("x86_64", "noarch") and version is not None:
                    evr = f"{version.get('epoch', '0')}:{version.get('ver', '')}-{version.get('rel', '')}"
                    old = best.get(name)
                    key = (int(version.get("epoch", "0") or 0), version.get("ver", ""), version.get("rel", ""))
                    if ".module" in key[2]:
                        element.clear()
                        continue
                    if old is None or rpm_compare(key, old[0]) > 0:
                        fmt = element.find(f"{RPM_NS}format")
                        description = (element.findtext(f"{RPM_NS}description") or "").strip() \
                            if name in described else ""
                        if any(marker in description.lower() for marker in MARKERS):
                            # a marker only in the long description (gnupg2: "design philosophy"): the row
                            #   stays without it, write() would leave the whole name out
                            description = ""
                        best[name] = (key, [name, arch, evr, repo, size.get("package", "0") if size is not None
                                            else "0", size.get("installed", "0") if size is not None else "0",
                                            (element.findtext(f"{RPM_NS}summary") or "").strip(),
                                            (element.findtext(f"{RPM_NS}url") or "").strip(),
                                            (fmt.findtext(f"{RPM_FORMAT_NS}license") or "") if fmt is not None else "",
                                            (fmt.findtext(f"{RPM_FORMAT_NS}group") or "") if fmt is not None else "",
                                            description.replace("\t", " ").replace("\n", "\\n"),
                                            (fmt.findtext(f"{RPM_FORMAT_NS}sourcerpm") or "") if fmt is not None
                                            else ""])
                element.clear()
    return ["\t".join(row) for _, (_, row) in sorted(best.items())]


def rpm_compare(a, b):
    """-1, 0 or 1 as rpm orders (epoch, version, release) (rpmvercmp, without the ~ and ^ of rpm 4.10+)."""
    if a[0] != b[0]:
        return -1 if a[0] < b[0] else 1
    for x, y in ((a[1], b[1]), (a[2], b[2])):
        xs, ys = re.findall(r"[0-9]+|[A-Za-z]+", x), re.findall(r"[0-9]+|[A-Za-z]+", y)
        for i, j in zip(xs, ys):
            if i.isdigit() and j.isdigit():
                if int(i) != int(j):
                    return -1 if int(i) < int(j) else 1
            elif i.isdigit() != j.isdigit():
                return 1 if i.isdigit() else -1
            elif i != j:
                return -1 if i < j else 1
        if len(xs) != len(ys):
            return -1 if len(xs) < len(ys) else 1
    return 0


def rhel_rows(rows):
    """The rows of dnf-meta.txt.gz as RHEL 9 names its builds: no rows of the AlmaLinux brand (REBRAND names
    those), the release without AlmaLinux's rebuild tag (.alma.1) and with Red Hat's module tag (module+el9),
    the brand in the texts as Red Hat's, no URL of almalinux.org."""
    found = []
    for line in rows:
        row = line.split("\t")
        if row[0] in REBRAND or row[0].startswith("almalinux"):
            continue
        for index in (2, 11):
            row[index] = ALMA_TAG.sub("", row[index]).replace("module_el", "module+el")
        for index in (6, 10):
            row[index] = row[index].replace("AlmaLinux", "Red Hat Enterprise Linux")
        if "almalinux" in row[7].lower():
            row[7] = ""
        found.append("\t".join(row))
    return found


def dnf_names(paths):
    """The rows of dnf.txt.gz (paths {repo id: primary.xml path}): "<name> <repo id>" of the repositories (AlmaLinux
    names as those of Red Hat), then "<name> > <provider>" for the plain names and programs a package provides that
    no package is called (the provider with the shortest name, as for yum). The builds of a module stream are
    left out, as for yum_meta: RHEL 9 enables no stream by default."""
    names, provides = {}, {}
    for repo, path in paths.items():
        with gzip.open(path, "rb") as f:
            for _, element in ET.iterparse(f):
                if element.tag != f"{RPM_NS}package":
                    continue
                name = element.findtext(f"{RPM_NS}name") or ""
                name = REBRAND.get(name, name)
                version = element.find(f"{RPM_NS}version")
                if version is not None and ".module" in version.get("rel", ""):
                    element.clear()
                    continue
                if name and RPM_NAME.fullmatch(name):
                    names.setdefault(name, repo)
                    block = element.find(f"{RPM_NS}format/{RPM_FORMAT_NS}provides")
                    for entry in block if block is not None else ():
                        provided = entry.get("name", "")
                        if RPM_NAME.fullmatch(provided) and not SONAME.match(provided):
                            provides.setdefault(provided, set()).add(name)
                    for listed in element.iterfind(f"{RPM_NS}format/{RPM_NS}file"):
                        if PROGRAM_PATH.fullmatch(listed.text or ""):
                            provides.setdefault(listed.text, set()).add(name)
                element.clear()
    # the names of the AlmaLinux brand its packages still provide: RHEL has none of them
    provides = {provided: by for provided, by in provides.items() if "alma" not in provided.lower()}
    return [f"{name} {repo}" for name, repo in sorted(names.items())] + [
        f"{provided} > {min(by, key=lambda n: (len(n), n))}" for provided, by in sorted(provides.items())
        if provided not in names]


FILELISTS_NS = "{http://linux.duke.edu/metadata/filelists}"
# the filelists of rpm-man.txt.gz per release letter
RPM_FILELISTS = {"c": ("c7-os-filelists.xml.gz", "c7-updates-filelists.xml.gz", "c7-extras-filelists.xml.gz"),
                 "r": ("el94-BaseOS-filelists.xml.gz", "el94-AppStream-filelists.xml.gz")}
MANUAL_PATH = re.compile(r"/usr/share/man/man([18])/([A-Za-z0-9][A-Za-z0-9+._-]{0,99})\.\1\.gz")


def rpm_manuals(paths):
    """The rows of rpm-man.txt.gz (paths {release letter: [filelists.xml paths]}): "<manual page> <releases>
    <package>" of the manual pages in sections 1 and 8 that are named as one of the package's own programs."""
    found = {}
    for letter, files in paths.items():
        for path in files:
            with gzip.open(path, "rb") as f:
                for _, element in ET.iterparse(f):
                    if element.tag != f"{FILELISTS_NS}package":
                        continue
                    name = element.get("name", "")
                    name = REBRAND.get(name, name) or ""
                    listed = [entry.text or "" for entry in element.iterfind(f"{FILELISTS_NS}file")]
                    programs = {entry.rsplit("/", 1)[-1] for entry in listed if EL7_PROGRAM_PATH.fullmatch(entry)}
                    for entry in listed:
                        manual = MANUAL_PATH.fullmatch(entry)
                        if RPM_NAME.fullmatch(name) and manual and manual.group(2) in programs:
                            found.setdefault((entry, name), set()).add(letter)
                    element.clear()
    return [f"{entry} {''.join(sorted(letters))} {name}" for (entry, name), letters in sorted(found.items())]


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
                # the programs the primary lists: yum installs by their path (/bin/mail gives mailx)
                found[name].update(listed.text for listed in element.iterfind(f"{RPM_NS}format/{RPM_NS}file")
                                   if EL7_PROGRAM_PATH.fullmatch(listed.text or ""))
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
    lines = [line for line in lines if not any(marker in line.lower() for marker in MARKERS)]
    data = "".join(line + "\n" for line in lines).encode()
    path.write_bytes(gzip.compress(data, 9, mtime=0))
    return len(lines), path.stat().st_size


def merge(indexes):
    merged = {}
    for index in indexes:
        for name, provided in index.items():
            merged.setdefault(name, set()).update(provided)
    return merged


def modelled():
    """The names of the packages the personas model (their installed and available packages)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dist"))
    from tpot_personas import catalog, system
    found = set()
    for table in (system.APT_INSTALLED, system.APT_AVAILABLE):
        for packages in table.values():
            found |= set(packages)
    for persona in catalog.PERSONAS:
        for release in persona.releases:
            found |= set(release.packages)
    # the rpm packages of RHEL 9 and CentOS 7
    found |= set(system.RPM_INSTALLED) | set(system.RPM_PERSONA) | set(system.RPM_AVAILABLE) | set(system.CENTOS_CORE)
    return found


def pocket_of(path):
    """The pocket of a downloaded index by its name: rpi (the Raspberry Pi archive), updates, backports,
    security or - (the release)."""
    if path.name.startswith("rpi-"):
        return "rpi"
    return next((pocket for pocket in POCKETS[1:] if f"-{pocket}-" in path.name), "-")


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
    meta_top = popcon(downloads / "popcon-by_inst.gz", META_TOP) | set(EXTRA) | modelled()
    indexes = {letter: [downloads / name for name in whole + cut] for letter, (whole, cut) in APT_RELEASES.items()}
    base = {letter: {row.split()[0] for row in deb_base(downloads, base_names(indexes)) if letter in row.split()[1]}
            for letter in APT_RELEASES}
    print("deb-base", write(out / "deb-base.txt.gz", deb_base(downloads, base)))
    print("apt-meta", write(out / "apt-meta.txt.gz", apt_meta(
        indexes, {letter: (releases[letter] & meta_top) | base[letter] for letter in APT_RELEASES}, pocket_of)))
    print("deb-files", write(out / "deb-files.txt.gz", deb_files(downloads, DEB_FILE_RELEASES)))
    described = popcon(downloads / "popcon-by_inst.gz", RPM_DESCRIBED) | set(EXTRA) | modelled()
    print("opkg-meta", write(out / "opkg-meta.txt.gz", opkg_meta(
        {letter: [downloads / f"ow-{version}-{feed}.gz" for feed in OPKG_FEEDS]
         for letter, version in OPKG_RELEASES.items()})))
    print("dnf", write(out / "dnf.txt.gz", dnf_names({repo: downloads / name for repo, name in DNF_REPOS.items()})))
    print("dnf-meta", write(out / "dnf-meta.txt.gz", rhel_rows(yum_meta(
        {repo: downloads / name for repo, name in DNF_REPOS.items()}, described))))
    print("rpm-man", write(out / "rpm-man.txt.gz", rpm_manuals(
        {letter: [downloads / name for name in names] for letter, names in RPM_FILELISTS.items()})))
    print("yum-meta", write(out / "yum-meta.txt.gz", yum_meta(
        {"base": downloads / "c7-os-primary.xml.gz", "updates": downloads / "c7-updates-primary.xml.gz",
         "extras": downloads / "c7-extras-primary.xml.gz"}, described)))


if __name__ == "__main__":
    here = Path(__file__).resolve().parent
    build(Path(sys.argv[1]), Path(sys.argv[2]) if len(sys.argv) > 2
          else here.parent / "dist" / "tpot_personas" / "package_names")
