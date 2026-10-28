"""Adds the shared libraries of the packages of package_installs.json.gz to their file lists (rpm -qf / rpm -ql of the
libraries a package brings: httpd's modules, MariaDB's plugins).

Usage: python3 -I add_package_libs.py [CACHE] [OUT]

CACHE is the folder of build_package_names.py (the pinned filelists of the CentOS 7.9.2009 and AlmaLinux 9.4
vault repositories; the cross-check recording rpm_libs.json is almalinux:9 at 9.8 and the 9.4 to 9.6 images);
default $XDG_CACHE_HOME/tpot-cowrie/apt, else ~/.cache/tpot-cowrie/apt. The libraries are the paths the
filelists name for the build the persona has (yum-meta.txt.gz / dnf-meta.txt.gz); the first run adds them, a second
changes nothing. The recorded lists of Task 12 filtered them out (folders bin, sbin, libexec and the unit folders only).
"""
import gzip
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAMES = HERE.parent / "dist" / "tpot_personas" / "package_names"
INSTALLS = HERE.parent / "dist" / "tpot_personas" / "package_installs.json.gz"
LIBRARY = re.compile(r"/(usr/)?lib(64)?/\S+\.so(\.[0-9]+)*")
NS = "{http://linux.duke.edu/metadata/filelists}"
REPOS = {"centos": ("meta", "yum-meta.txt.gz", ("c7-os", "c7-updates", "c7-extras")),
         "rhel": ("meta", "dnf-meta.txt.gz", ("el94-BaseOS", "el94-AppStream"))}


def builds(path):
    """{name: epoch:version-release} of a meta list."""
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return {row[0]: row[2] for row in (line.split("\t") for line in f.read().splitlines()) if len(row) > 2}


def libraries(cache, repos, wanted):
    """{package: [library paths]} of the wanted {name: evr} builds in the filelists of the repositories."""
    found = {}
    for repo in repos:
        with gzip.open(cache / f"{repo}-filelists.xml.gz", "rb") as f:
            for _, element in ET.iterparse(f):
                if element.tag != f"{NS}package":
                    continue
                name = element.get("name")
                version = element.find(f"{NS}version")
                if name in wanted and version is not None and \
                        f"{version.get('epoch')}:{version.get('ver')}-{version.get('rel')}" == wanted[name] \
                        and element.get("arch") in ("x86_64", "noarch"):
                    files = {e.text for e in element.iterfind(f"{NS}file") if LIBRARY.fullmatch(e.text or "")}
                    found[name] = sorted(files)
                element.clear()
    return found


def main(cache, out):
    with gzip.open(INSTALLS, "rt", encoding="utf-8") as f:
        data = json.load(f)
    for family, (_, meta, repos) in REPOS.items():
        files = data[family]["files"]
        meta_builds = builds(NAMES / meta)
        wanted = {name: meta_builds[name] for name in files if name in meta_builds}
        for name, paths in libraries(cache, repos, wanted).items():
            files[name] = sorted(set(files[name]) | set(paths))
        print(family, {name: len(paths) for name, paths in files.items()}, "missing", sorted(set(files) - set(wanted)))
    with gzip.open(out, "wt", encoding="utf-8", compresslevel=9) as f:
        json.dump(data, f, sort_keys=True, separators=(",", ":"))


if __name__ == "__main__":
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(base) / "tpot-cowrie" / "apt",
         Path(sys.argv[2]) if len(sys.argv) > 2 else INSTALLS)
