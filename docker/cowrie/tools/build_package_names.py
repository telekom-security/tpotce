#!/usr/bin/env python3
"""Build the package name lists of the personas (docker/cowrie/dist/tpot_personas/package_names/).

The input is a folder with the package indexes of the releases (downloaded as data). The inputs are
pinned: SOURCES names each file with the URL of an archive of a day (snapshot.debian.org,
snapshot.ubuntu.com) or of a release that does not change (OpenWrt, the vaults of CentOS and
AlmaLinux) and its sha256. Only the names and the Provides are read; nothing of the indexes is run.
  python3 -I docker/cowrie/tools/build_package_names.py --fetch   download what the cache lacks, check each file
      against its sha256, build (the cache: $XDG_CACHE_HOME/tpot-cowrie/apt, else ~/.cache/tpot-cowrie/apt)
  python3 -I docker/cowrie/tools/build_package_names.py --offline  the cache only (also the default)
  python3 -I docker/cowrie/tools/build_package_names.py DOWNLOADS [OUT]   a folder of your own, read as it is
The recorded inputs deb-base-<letter>.txt and deb-files-<letter>.txt (dpkg of the official images, see the
README) cannot be downloaded: they must be in the folder too. popcon-by_inst.gz and the Raspberry Pi archive
have no archive of a day: their file changes, the pin is the hash of the build of the lists, and a newer file
is taken only with --accept-moved, which needs an OUT folder (the lists then differ from the checked-in ones,
which are never overwritten that way).

apt.txt.gz     one line per name: "<name> <releases>", releases the letters of APT_RELEASES; per
               release the names of the indexes its sources name (components and pockets): those of
               the whole indexes all, of the others the names in the Debian popcon top POPCON_TOP and
               EXTRA; no names of a kernel ABI (the persona adds those of its own kernel)
apt-virtual.txt.gz  one line per virtual name: "<name> <releases> <provider,...>", the packages of the
               release's list that provide a name no package of its indexes has (not the names of
               libraries and language modules, LIBRARY)
yum.txt.gz     the names of CentOS 7.9.2009 (os, updates, extras) and "<name> <provider,...>" for the
               plain names and the listed programs (/bin, /sbin, /usr/bin, /usr/sbin) their packages provide
               (no sonames; every provider, the shortest name first: derived; yum provides sorts them: recorded)
opkg.txt.gz    one line per name: "<name> <releases> <feed>", the feeds of OpenWrt 21.02.7 / 22.03.7
apt-meta.txt.gz  one line per candidate: "<name>\t<releases>\t<version>\t<component>\t<pocket>\t<installed
               size>\t<size>\t<all or ->\t<section>\t<maintainer>\t<description>" of the names in the Debian popcon top META_TOP, EXTRA and the
               packages the personas model (MODELLED): the version apt takes on the release (the highest
               of the release and its -updates / -security pockets, of -backports only without another)
apt-relations.txt.gz  "<name>\t<releases>\t<version>" and the RELATION_FIELDS of the candidates of apt-meta.txt.gz for the
               packages the personas model (installed and available ones), EXTRA, SERVICES and the base system
               (a line break as \\n; the long descriptions are in the Translation files, not in a Packages index)
opkg-meta.txt.gz  "<name>\t<releases>\t<version>\t<description>" of every package of the feeds, the
               description on the first row of a name only
yum-meta.txt.gz  "<name>\t<arch>\t<epoch:version-release>\t<repo>\t<size>\t<installed size>\t<summary>"
               of the newest x86_64 / noarch build of each name of CentOS 7.9.2009 (base = os, updates, extras)
dnf.txt.gz     "<name> <baseos or appstream>" of the repositories of AlmaLinux 9.4 (vault), the names of
               the AlmaLinux brand as those of Red Hat (REBRAND), and "<name> > <provider,...>" for the plain names
               and the programs (/usr/bin, /usr/sbin) their packages provide (every provider, the shortest name first:
               derived; dnf provides sorts them: recorded)
rpm-man.txt.gz "<manual page> <releases> <package>": the manual pages (sections 1 and 8) of the programs of
               each package of CentOS 7.9.2009 (c) and of RHEL 9 (r, AlmaLinux 9.4 vault, the brand names as
               those of Red Hat), as the filelists of the repositories name them
"""
import argparse
import gzip
import hashlib
import http.client
import lzma
import os
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections import namedtuple
from pathlib import Path

POPCON_TOP = 20000
# the names whose version, component and description apt-meta.txt.gz keeps (besides EXTRA, SERVICES and modelled())
META_TOP = 2000
# the fields of a candidate that apt-relations.txt.gz keeps (apt show, apt-cache show and dpkg -s print them)
RELATION_FIELDS = ("Source", "Multi-Arch", "Original-Maintainer", "Replaces", "Provides", "Depends", "Pre-Depends",
                   "Recommends", "Suggests", "Conflicts", "Breaks", "Enhances", "Homepage", "Tag", "Task", "Build-Essential",
                   "Original-Vcs-Browser", "Original-Vcs-Git")
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
# the common server packages: apt show knows them on every apt release, whatever their popcon rank. Not in EXTRA:
# EXTRA also keeps the descriptions of the rpm indexes (yum-meta, dnf-meta)
SERVICES = ("nginx", "apache2", "mariadb-server", "postgresql", "redis-server", "docker.io", "curl", "wget",
            "net-tools", "python3-pip", "git", "unzip", "screen", "tmux", "postfix")
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


# The pinned inputs. The archives of a day: the time of a snapshot at which every index of that archive had the
# sha256 the lists were built from (read from the hashes in the Release files of the snapshots of 2026-10-09 to 15;
# a snapshot of Ubuntu answers for any time, one of Debian for the times of its own runs):
SNAPSHOTS = {"debian": "20261013T143410Z", "debian-security": "20261013T073402Z", "ubuntu": "20261013T160000Z"}
# sha256 of each input as the build of the lists read it (2026-10-13 to 15)
SHA256 = {
    "c7-extras-filelists.xml.gz": "e72094fa3c5f686a92550dc92ad509926d501ca399fe5b7dd79fc493db9766ae",
    "c7-extras-primary.xml.gz": "6f4160fb1c4f94da6bf6d4295b3ae98fb4abcf01645072917b6b84acf87af159",
    "c7-os-filelists.xml.gz": "5319616dde574d636861a6e632939f617466a371e59b555cf816cf1f52f3e873",
    "c7-os-primary.xml.gz": "2b479c0f3efa73f75b7fb76c82687744275fff78e4a138b5b3efba95f91e099e",
    "c7-updates-filelists.xml.gz": "a54fd4a52a33703bdab6b05fc38fba5eb4ecfe0afd02abb31794bead337c548e",
    "c7-updates-primary.xml.gz": "04fc3905eafcdc8422d475812ed81841986f18e3150b571c6920ba94b9c1898e",
    "deb-bookworm-contrib-arm64.xz": "2c8d4d73c617dee3178b63085b037371183a44573c3c0dc929cdfd86e9555e95",
    "deb-bookworm-main-amd64.xz": "9e0b5aabb2465b3d2e7a7fe27f9913846277833f7a2826e7767acccff5b588c5",
    "deb-bookworm-main-arm64.xz": "2ddb1737692e8c45c53e8d57c0ce4cd21c78c5703b830c3226b1423566a06c00",
    "deb-bookworm-non-free-arm64.xz": "5f70e8e31d64f9241904e54a83dc639d92f4eea9ad1a57b26d40f3943f474929",
    "deb-bookworm-non-free-firmware-arm64.xz": "a1330073ccb0787ab9864190877bccec16642f93f0dfaf9bdbff597c6326a6e3",
    "deb-bookworm-security-contrib-arm64.xz": "e0d2c18d6618ed1d768f9bc0110729c1f92f1ae59371d76c55db8d68fc28869b",
    "deb-bookworm-security-main-amd64.xz": "816aa77b2d8d70c7f17494007692511bc6848598909cff4ee8fc642231c40007",
    "deb-bookworm-security-main-arm64.xz": "4fb4648b49bee1f132b2d2e21c0812b15a5add9ced6ddc0b4314d20f8e80f01a",
    "deb-bookworm-security-non-free-arm64.xz": "c1e66449d14669229c89ee2fe7665a2b9d9bb4abb00e8486f0be0cfda36e2dbe",
    "deb-bookworm-security-non-free-firmware-arm64.xz":
        "15818983d3701516780d9b08c2e5dcefbb75ae5ee66afefa57581c26e7ae9f32",
    "deb-bookworm-updates-contrib-arm64.xz": "0040f94d11d0039505328a90b2ff48968db873e9e7967307631bf40ef5679275",
    "deb-bookworm-updates-main-amd64.xz": "87e7e94047fb7fb6f4ceecc7022d4bee55b66031cc2a7666d3196f3e0aabb846",
    "deb-bookworm-updates-main-arm64.xz": "5dfd52317071685ad6e58c4ef0720128b7db68624079f83be02bdf55f07ac070",
    "deb-bookworm-updates-non-free-arm64.xz": "0040f94d11d0039505328a90b2ff48968db873e9e7967307631bf40ef5679275",
    "deb-bookworm-updates-non-free-firmware-arm64.xz":
        "0040f94d11d0039505328a90b2ff48968db873e9e7967307631bf40ef5679275",
    "deb-trixie-backports-main-amd64.xz": "0b78cc1000b3ecfd67f298fc989ed3c27b78c0972b2c570715ba2e4728efe56e",
    "deb-trixie-main-amd64.xz": "7778d3e3f303b7ddb8ce0fe7c8d57473a076c6bf2e8f241f75421d2396352498",
    "deb-trixie-security-main-amd64.xz": "cf9ed1763e683ee0d51b976b6632e3b4c7f6c7abc965a1965ea85df07fd25b86",
    "deb-trixie-updates-main-amd64.xz": "10012cb384dc2613586d7b3d391d92a5637c9faa36017239739f2a6109a0d45f",
    "el94-AppStream-filelists.xml.gz": "32a4efbf03582df7195f8c5e4e036a13e04dc6487c6f87c6ab597c0615060092",
    "el94-AppStream-primary.xml.gz": "1fc5e4d02ee250bb81c612a5e748da2f5f78f77c1bd9ebe4717ecf3b9026e1e4",
    "el94-BaseOS-filelists.xml.gz": "b1ba073ba4fe48ee0af324713f6578a499e5f24eec00679849a637bbf710c323",
    "el94-BaseOS-primary.xml.gz": "9c977a15fac064f2892b571691366d9ad29f0675d9c603db6f1d319ca3db6ce6",
    "ow-21.02.7-base.gz": "93ec547a86ba2c9662090dfcada30fa593abd2d8ba4f268ae5f892654e364aa3",
    "ow-21.02.7-core.gz": "be894c4f14d2cea2842b5515ce32119c8f02d838377c76c6b719157e01d2c2c6",
    "ow-21.02.7-luci.gz": "300e998b681278925b882e66ef5fef0b45ca28342a9d03da61da56e00c4da8d9",
    "ow-21.02.7-packages.gz": "eb2fd091487d057f7d2ad967220b3e7a39e972831c68b1ca26da88ea3244fe36",
    "ow-21.02.7-routing.gz": "57c80620caaadeb56c20eca155b7e67a9b0d2c73f3c11dd4f5d88e34de45a40a",
    "ow-21.02.7-telephony.gz": "436f9f6d627e095090e9ba3c43dab4a67e4c5ea4ffde8b257bfd288c9ef1a1a2",
    "ow-22.03.7-base.gz": "db38fc1e41c1217ed9c15d286b315ff51d581627ba934b5be8065923c3d0728f",
    "ow-22.03.7-core.gz": "fdad002bb506b3b07c167e77974ae4a480bc72c3ebf6dd8aaa6883ebbdeb2d27",
    "ow-22.03.7-luci.gz": "65fed9b36a742c4253fa5566210c0a4a159d1f397c4a38e25b400d4b9dfe0b73",
    "ow-22.03.7-packages.gz": "7a5fdba3adef4f65b515915265210218e09f2cbd23cd2427e7cbd087e039610b",
    "ow-22.03.7-routing.gz": "c0631947ffb4ee09843e3e05c48c5a60f094e2f693ed8290c3c8ef49fe0c2ae1",
    "ow-22.03.7-telephony.gz": "b3538407ab2ce8d6ebb5a3c0b4a00107bb55e404f66e73d9ee32895a2ad4d965",
    "popcon-by_inst.gz": "a58f9cd45b6fe8f1f45ee16a289cc56d8e453a833753556d2a78d49cc971f44e",
    "rpi-bookworm-main-arm64.gz": "fdfa2c9010646fe76ec6d011efe28fd2dce7df7d3c8b381d66c28d6ad75c5bd2",
    "ub-jammy-main-amd64.xz": "37cb57f1554cbfa71c5a29ee9ffee18a9a8c1782bb0568e0874b7ff4ce8f9c11",
    "ub-jammy-restricted-amd64.xz": "92102b5d9dfb7804293891528d5e57c3d05949df71af613d4f99fcb7d6a3f488",
    "ub-jammy-security-main-amd64.xz": "cf1f011040c59e9123b1d5243caca7d223186cab6892d1101341a369030f7748",
    "ub-jammy-security-restricted-amd64.xz": "24c128c83879684b1776877d25d01e4924556a500fb1a904280a01c80c3ace61",
    "ub-jammy-security-universe-amd64.xz": "4f1ed259f79558c910de1a6facc9eaecd98030d6b9db51e8746a092e560bd7d7",
    "ub-jammy-universe-amd64.xz": "d29cb24c93fec5f43255706bce7eb46d4779952039d8c68ac1bb14a6f3655ce2",
    "ub-jammy-updates-main-amd64.xz": "6b6d61be8e24ba9b728ff4608df6912a02231afde62189dfe71ab300e7f1623f",
    "ub-jammy-updates-restricted-amd64.xz": "e81c86b18aa382b23fcdadb2bb2a874942f44b61d7d66ee885a3fcf9343d344c",
    "ub-noble-backports-main-amd64.xz": "b962c8d83acab25c27b3367cf94b8566d0da28bdf696eb324c263836d9e41a94",
    "ub-noble-backports-multiverse-amd64.xz": "e224a0da236577f82600151bef134d7ddb3eebd0fed8f4574f65dc7e88e0bf2d",
    "ub-noble-backports-restricted-amd64.xz": "0040f94d11d0039505328a90b2ff48968db873e9e7967307631bf40ef5679275",
    "ub-noble-backports-universe-amd64.xz": "4d689e32b0131a398ecbe07597d51b55074a20f70d66587c2c9c14d8f6fcc040",
    "ub-noble-main-amd64.xz": "2a6a199e1031a5c279cb346646d594993f35b1c03dd4a82aaa0323980dd92451",
    "ub-noble-multiverse-amd64.xz": "8080460fe0864ab72bfc2a6d52e76d8c82d55f2cc2188a12f83d6fbe3e1750ea",
    "ub-noble-restricted-amd64.xz": "aa3f9520f4e46cdcd610c7f0ce9450cda430f20085b028845e95122e9d61680c",
    "ub-noble-security-main-amd64.xz": "5c9b60d7fc31c9becdbaaa6443e3953b94ada356aedd84ce9113390a78cb4353",
    "ub-noble-security-multiverse-amd64.xz": "80ea3c5875729fc29893b7105f3e81a584fa4489f4fa701056ec1603f46b254c",
    "ub-noble-security-restricted-amd64.xz": "fa9d41fd43c8c7d784089207a1ca211d7d8dccefe1c6bb3c35ea382417f04673",
    "ub-noble-security-universe-amd64.xz": "d7e845a6768173e69332c106fed950dd846385474c394629c26ab93f1a83b102",
    "ub-noble-universe-amd64.xz": "ba9057fa1b91438cc8a1d26808d00c85389fe101d0c1496254df97236405599a",
    "ub-noble-updates-main-amd64.xz": "1b82f23ac8ff47ec232b711545febb7a596e0dce82e71cef7c65dd76023aa217",
    "ub-noble-updates-multiverse-amd64.xz": "f99e154c0eee0b2fa6a01c2a0ba7a19f68949c3296032bbec5cbdbaa110e42cf",
    "ub-noble-updates-restricted-amd64.xz": "4cb3139cdb7d3d8ef9ad806d0dcf5f663736accaf15a34e4120f2ba53ea7a634",
    "ub-noble-updates-universe-amd64.xz": "7648697581e4da732a404961c3d8477e36bf31d18d4cfbde68b080345eab9057",
}
# the files with no archive of a day: popcon changes daily, the Raspberry Pi archive keeps no old state; a newer
# file is refused unless --accept-moved says the lists may differ from the checked-in ones
MOVING = {
    "popcon-by_inst.gz": "https://popcon.debian.org/by_inst.gz changes every day, no archive of the day exists",
    "rpi-bookworm-main-arm64.gz": "archive.raspberrypi.com keeps no old state of its indexes",
}
Source = namedtuple("Source", "name url sha256")
DEB_FILE = re.compile(r"(deb|ub)-(bookworm|trixie|jammy|noble)(?:-(updates|backports|security))?-"
                      r"(main|contrib|non-free-firmware|non-free|restricted|universe|multiverse)-(amd64|arm64)\.xz")
OPENWRT_FILE = re.compile(r"ow-(\d+\.\d+\.\d+)-(core|base|luci|packages|routing|telephony)\.gz")
RPM_FILE = re.compile(r"(c7|el94)-(os|updates|extras|BaseOS|AppStream)-(primary|filelists)\.xml\.gz")
VAULTS = {"c7": "https://vault.centos.org/7.9.2009/{repo}/x86_64/repodata/",
          "el94": "https://vault.almalinux.org/9.4/{repo}/x86_64/os/repodata/"}
# the inputs the builder cannot download: recorded in the official images (README)
RECORDED_KINDS = ("deb-base", "deb-files")
RECORDED_LETTERS = DEB_FILE_RELEASES


class FetchError(Exception):
    """An input is missing, cannot be downloaded or is not the pinned file."""


def source_url(name, sha256):
    """The pinned URL of an input by its file name (the README names the layout)."""
    if name == "popcon-by_inst.gz":
        return "https://popcon.debian.org/by_inst.gz"
    if name == "rpi-bookworm-main-arm64.gz":
        return "https://archive.raspberrypi.com/debian/dists/bookworm/main/binary-arm64/Packages.gz"
    match = DEB_FILE.fullmatch(name)
    if match:
        kind, suite, pocket, component, arch = match.groups()
        pocket = f"-{pocket}" if pocket else ""
        if kind == "ub":
            archive, stamp = "ubuntu", SNAPSHOTS["ubuntu"]
            base = f"https://snapshot.ubuntu.com/ubuntu/{stamp}"
        else:
            archive = "debian-security" if pocket == "-security" else "debian"
            base = f"https://snapshot.debian.org/archive/{archive}/{SNAPSHOTS[archive]}"
        return f"{base}/dists/{suite}{pocket}/{component}/binary-{arch}/Packages.xz"
    match = OPENWRT_FILE.fullmatch(name)
    if match:
        version, feed = match.groups()
        where = "targets/ramips/mt7621/packages" if feed == "core" else f"packages/mipsel_24kc/{feed}"
        return f"https://downloads.openwrt.org/releases/{version}/{where}/Packages.gz"
    match = RPM_FILE.fullmatch(name)
    if match:
        vault, repo, kind = match.groups()
        return f"{VAULTS[vault].format(repo=repo)}{sha256}-{kind}.xml.gz"
    raise ValueError(f"no source for {name!r}")


SOURCES = tuple(Source(name, source_url(name, sha256), sha256) for name, sha256 in sorted(SHA256.items()))


def input_names():
    """The files of the download folder that the builder reads (the recorded inputs apart)."""
    names = set()
    for whole, cut in APT_RELEASES.values():
        names.update(whole + cut)
    names.update(YUM_FILES)
    names.update(DNF_REPOS.values())
    names.update(name.replace("primary", "filelists") for name in DNF_REPOS.values())
    names.update(f"ow-{version}-{feed}.gz" for version in OPKG_RELEASES.values() for feed in OPKG_FEEDS)
    names.update(name for files in RPM_FILELISTS.values() for name in files)
    names.add("popcon-by_inst.gz")
    return sorted(names)


def recorded_names():
    return [f"{kind}-{letter}.txt" for kind in RECORDED_KINDS for letter in RECORDED_LETTERS]


def cache_dir(environ=None):
    """$XDG_CACHE_HOME/tpot-cowrie/apt (an absolute path only), else ~/.cache/tpot-cowrie/apt."""
    environ = os.environ if environ is None else environ
    base = environ.get("XDG_CACHE_HOME", "")
    if not base or not os.path.isabs(base):
        base = os.path.join(environ.get("HOME") or os.path.expanduser("~"), ".cache")
    return Path(base) / "tpot-cowrie" / "apt"


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mismatch(source, got):
    """The text of a file that is not the pinned one."""
    text = f"{source.name} is not the pinned file: sha256 {got}, pinned {source.sha256}"
    if source.name in MOVING:
        text += (f" ({MOVING[source.name]}); --accept-moved takes it (with DOWNLOADS OUT: the lists then differ "
                 "from the checked-in ones)")
    return text


def fetch(cache, sources=SOURCES, offline=False, accept_moved=False, opener=urllib.request.urlopen, say=print):
    """The pinned files in the folder cache, each checked against its sha256: a file already there that matches
    is kept, a missing or damaged one is downloaded (with offline: an error). A download goes to <name>.part and
    takes its place only when the hash is right. A file of MOVING with another hash is taken only with
    accept_moved. Every file is tried; the errors come together in one FetchError. Returns the paths."""
    cache = Path(cache)
    if not offline:
        try:
            cache.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise FetchError(f"the cache folder {cache} cannot be made: {error}") from error
    paths, errors = [], []
    for source in sources:
        try:
            paths.append(_fetch_one(cache, source, offline, accept_moved, opener, say))
        except FetchError as error:
            errors.append(str(error))
    if errors:
        raise FetchError("\n".join(errors))
    return paths


def _fetch_one(cache, source, offline, accept_moved, opener, say):
    if Path(source.name).name != source.name or source.name in ("", ".", ".."):
        raise FetchError(f"{source.name!r} is no file name of the cache")
    path = cache / source.name
    moved_ok = accept_moved and source.name in MOVING
    if path.is_file():
        got = file_sha256(path)
        if got == source.sha256 or moved_ok:
            return path
        if offline:
            raise FetchError(_mismatch(source, got))
    if offline:
        raise FetchError(f"{source.name} is not in the cache {cache} (--fetch downloads it from {source.url})")
    say(f"fetch {source.name}")
    part = cache / f"{source.name}.part"
    digest = hashlib.sha256()
    try:
        request = urllib.request.Request(source.url, headers={"User-Agent": "tpot-cowrie-package-names"})
        with opener(request, timeout=120) as response, open(part, "wb") as handle:
            for chunk in iter(lambda: response.read(1 << 20), b""):
                digest.update(chunk)
                handle.write(chunk)
    except (OSError, urllib.error.URLError, http.client.HTTPException) as error:
        part.unlink(missing_ok=True)
        raise FetchError(f"{source.name}: {source.url} cannot be read: {error}") from error
    if digest.hexdigest() != source.sha256 and not moved_ok:
        part.unlink(missing_ok=True)
        raise FetchError(_mismatch(source, digest.hexdigest()))
    os.replace(part, path)
    return path


def check_recorded(folder):
    missing = [name for name in recorded_names() if not (Path(folder) / name).is_file()]
    if missing:
        raise FetchError(f"recorded inputs missing in {folder}: {', '.join(missing)} (dpkg of the official images, "
                         "they cannot be downloaded: see the README next to the lists)")


def deb_stanzas(path):
    """The fields of each package of a Packages index (the first line of a field, continuation lines
    joined with a space; the first line alone as "<field>:first", the lines as they are as "<field>:raw")."""
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
                    entry[f"{key}:raw"] += "\n" + line.rstrip()
            elif ":" in line:
                key, _, value = line.partition(":")
                entry[key] = entry[f"{key}:first"] = entry[f"{key}:raw"] = value.strip()
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


def apt_candidates(indexes, keep, pocket_of):
    """{letter: {name: (stanza, pocket)}}: the candidate of each kept name. indexes {letter: [index paths in the
    order of the sources]}, keep {letter: names}, pocket_of(path) the pocket of an index."""
    found = {}
    for letter, paths in indexes.items():
        best = found[letter] = {}
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
    return found


def apt_meta(indexes, keep, pocket_of):
    """The rows of apt-meta.txt.gz (the candidates of apt_candidates)."""
    rows = {}
    for letter, best in apt_candidates(indexes, keep, pocket_of).items():
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


def apt_relations(indexes, keep, pocket_of):
    """The rows of apt-relations.txt.gz: "<name>\t<release letters>\t<version>" and the RELATION_FIELDS of the candidate
    (a line break as \\n, a backslash doubled; the long descriptions are not in a Packages index)."""
    rows = {}
    for letter, best in apt_candidates(indexes, keep, pocket_of).items():
        for name, (entry, _) in best.items():
            values = tuple(entry.get(f"{field}:raw", "").replace("\\", "\\\\").replace("\n", "\\n")
                           for field in RELATION_FIELDS)
            if any(values):
                rows.setdefault((name, entry["Version"], *values), []).append(letter)
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


def provider_list(providers):
    """The packages that provide a name, comma separated: the shortest name first (what installs it; unverified: yum and
    dnf also weigh arch and repository), the others sorted."""
    first = min(providers, key=lambda n: (len(n), n))
    return ",".join([first, *sorted(set(providers) - {first})])


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
        f"{provided} > {provider_list(by)}" for provided, by in sorted(provides.items())
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


def apt_meta_names(downloads):
    """The names whose apt-meta rows are kept: the popcon top META_TOP, EXTRA, SERVICES and the modelled packages."""
    return popcon(downloads / "popcon-by_inst.gz", META_TOP) | set(EXTRA) | set(SERVICES) | modelled()


def apt_relation_names(downloads):
    """The names whose apt-relations rows are kept (the rest of apt-meta shows no Depends: the file stays small)."""
    return set(EXTRA) | set(SERVICES) | modelled()


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
    print("yum", write(out / "yum.txt.gz", sorted(index) + [f"{v} {provider_list(p)}"
                                                            for v, p in sorted(provides.items())]))
    feeds = {}
    for letter, version in OPKG_RELEASES.items():
        for feed in OPKG_FEEDS:
            for name in deb_index(downloads / f"ow-{version}-{feed}.gz"):
                feeds.setdefault(name, {})[letter] = feed
    print("opkg", write(out / "opkg.txt.gz",
                        [f"{n} {''.join(sorted(feeds[n]))} {feeds[n][max(feeds[n])]}" for n in sorted(feeds)]))
    meta_top = apt_meta_names(downloads)
    indexes = {letter: [downloads / name for name in whole + cut] for letter, (whole, cut) in APT_RELEASES.items()}
    base = {letter: {row.split()[0] for row in deb_base(downloads, base_names(indexes)) if letter in row.split()[1]}
            for letter in APT_RELEASES}
    print("deb-base", write(out / "deb-base.txt.gz", deb_base(downloads, base)))
    print("apt-meta", write(out / "apt-meta.txt.gz", apt_meta(
        indexes, {letter: (releases[letter] & meta_top) | base[letter] for letter in APT_RELEASES}, pocket_of)))
    relation_top = apt_relation_names(downloads)
    print("apt-relations", write(out / "apt-relations.txt.gz", apt_relations(
        indexes, {letter: (releases[letter] & meta_top & relation_top) | base[letter] for letter in APT_RELEASES},
        pocket_of)))
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


def parse_args(argv, environ=None):
    """The command line: [--fetch | --offline] [--accept-moved] [--cache DIR] [DOWNLOADS [OUT]]. Without DOWNLOADS the
    folder is the cache; its files are checked against SOURCES (verify), a folder of your own is read as it is
    unless --fetch / --offline say it is checked."""
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], prog="build_package_names.py")
    parser.add_argument("--fetch", action="store_true", help="download what the folder lacks (pinned, checked)")
    parser.add_argument("--offline", action="store_true", help="use the folder only (the default without DOWNLOADS)")
    parser.add_argument("--accept-moved", action="store_true",
                        help="take a newer popcon / Raspberry Pi index than the pinned one (needs DOWNLOADS OUT, "
                             "never writes the checked-in lists)")
    parser.add_argument("--cache", type=Path, help="the cache folder (default: %(default)s)",
                        default=cache_dir(environ))
    parser.add_argument("paths", nargs="*", type=Path, metavar="DOWNLOADS [OUT]")
    args = parser.parse_args(argv)
    if args.fetch and args.offline:
        parser.error("--fetch and --offline exclude each other")
    if len(args.paths) > 2:
        parser.error("DOWNLOADS and OUT are the only arguments")
    args.downloads = args.paths[0] if args.paths else args.cache
    args.out = args.paths[1] if len(args.paths) > 1 else here.parent / "dist" / "tpot_personas" / "package_names"
    args.verify = args.fetch or args.offline or not args.paths
    args.offline = args.verify and not args.fetch
    if args.accept_moved and not args.verify:
        parser.error("--accept-moved needs --fetch, --offline or no DOWNLOADS (a folder of your own is not checked)")
    if args.accept_moved and len(args.paths) < 2:
        # the lists built from a newer popcon / Raspberry Pi index differ from the checked-in ones
        parser.error("--accept-moved needs an OUT folder: DOWNLOADS OUT (the checked-in lists are not overwritten)")
    return args


def main(argv=None, environ=None, opener=urllib.request.urlopen):
    args = parse_args(sys.argv[1:] if argv is None else argv, environ)
    try:
        if args.verify:
            fetch(args.downloads, offline=not args.fetch, accept_moved=args.accept_moved, opener=opener)
        check_recorded(args.downloads)
    except FetchError as error:
        sys.exit(f"build_package_names.py: {error}")
    build(args.downloads, args.out)


if __name__ == "__main__":
    main()
