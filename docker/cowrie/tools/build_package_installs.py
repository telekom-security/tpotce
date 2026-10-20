"""Builds dist/tpot_personas/package_installs.json.gz from the recordings of Task 12 (refs/t12.json of round 4):
what installing a package that ships a service does on each release, as recorded in containers with systemd.

Usage: python3 -I build_package_installs.py REFS_JSON [OUT]

Per Debian / Ubuntu release (bookworm, trixie, jammy, noble):
- installs: per recorded package the packages its install brought (name, version, all or -, pocket, component,
  download size, in the order of the Get lines) and the units that ran after it (the units of the package that are
  enabled stay counted as started where only the container's conditions held them: timesyncd, irqbalance,
  bluetooth, ModemManager; a wait-online unit not);
- blocks: what each package's postinst wrote ("Setting up" to the next one), the key lines of openssh-server as
  "\\0key <bits> <type>" (their fingerprints and host are the session's);
- files: dpkg -L of the packages below bin, sbin, libexec and the unit folders;
- units: the unit files of the covered services.
Red Hat (AlmaLinux 9 for RHEL 9, CentOS 7): the dependencies dnf / yum 3 named (weak ones apart), rpm -ql of the
same folders, the unit files, the units the presets enable.
"""
import gzip
import json
import re
import sys
from pathlib import Path

RELEASES = {"debian-12": "bookworm", "debian-13": "trixie", "ubuntu-22-04": "jammy", "ubuntu-24-04": "noble"}
GET = re.compile(r"^Get:\d+ \S+ (\S+?)/(\S+) \S+ (\S+?)(?::\S+)? (\S+) (\S+) \[([\d.,]+) ([kMG]?)B\]$")
SETTING = re.compile(r"^Setting up (\S+?)(?::\S+)? \((\S+)\) \.\.\.$")
KEY = re.compile(r"^(\d+) SHA256:\S+ root@\S+ \((\w+)\)$")
REBRAND = {"almalinux-logos-httpd": "redhat-logos-httpd"}
# units a container's conditions keep from starting that a host starts
HELD = ("systemd-timesyncd.service", "irqbalance.service", "bluetooth.service", "ModemManager.service")
SCALE = {"": 1, "k": 1000, "M": 1000 ** 2, "G": 1000 ** 3}


def entries(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {entry["id"]: entry for entry in data["entries"]}


def deb_install(text):
    """(new packages, blocks) of an apt-get install."""
    new, blocks, current = [], {}, None
    selected = set()
    for line in text.splitlines():
        hit = GET.match(line)
        if hit:
            suite, component, name, arch, version, size, unit = hit.groups()
            pocket = suite.split("-", 1)[1] if "-" in suite else "-"
            new.append([name, version, arch == "all", pocket, component,
                        int(float(size.replace(",", "")) * SCALE[unit])])
            continue
        if line.startswith("Selecting previously unselected package "):
            selected.add(line.rsplit(" ", 1)[1].rstrip(".").split(":")[0])
            continue
        setting = SETTING.match(line)
        if setting:
            current = setting.group(1)
            blocks[current] = []
            continue
        # a block ends at the next dpkg step too: dpkg configures some packages (adduser, libsigsegv2, ...)
        #   between the unpacking of others
        if line.startswith(("Processing triggers for ", "Reading ", "Building ", "Fetched ", "(Reading database ",
                            "Selecting previously unselected package ", "Preparing to unpack ", "Unpacking ")):
            current = None
            continue
        if current is not None:
            key = KEY.match(line)
            blocks[current].append(f"\0key {key.group(1)} {key.group(2)}" if key else line)
    return [row for row in new if row[0] in selected], blocks


def deb_active(text):
    started = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0].endswith((".service", ".socket")):
            unit, enabled, active = parts
            if active == "active" or (enabled == "enabled" and unit in HELD):
                started.append(unit)
    return started


def files_of(text):
    found = {}
    for line in text.splitlines():
        package, _, path = line.partition(" ")
        if path.startswith("/") and not path.endswith(("/", ".d")) and " " not in path and "." not in package[:1] \
                and not package.endswith(":"):
            if re.fullmatch(r"/(usr/)?(s?bin|lib/systemd/system|libexec|lib/systemd/system)", path):
                continue  # a folder of the listing
            found.setdefault(package, []).append(path)
    return {name: sorted(set(paths)) for name, paths in found.items()}


def unit_texts(text):
    found, name = {}, None
    for line in text.splitlines(keepends=True):
        if line.startswith("=== "):
            name = line[4:].strip()
            found[name] = ""
        elif name and name != "files":
            found[name] += line
    return {k: v for k, v in found.items() if k != "files" and v.strip()}


def el_sections(text, yum=False):
    """{section: [names]} of a dnf / yum 3 transaction table."""
    found, section = {}, None
    titles = {"Installing:": "install", "Installing dependencies:": "deps", "Installing weak dependencies:": "weak",
              "Installing for dependencies:": "deps"}
    for line in text.splitlines():
        if line.strip() in titles:
            section = titles[line.strip()]
            continue
        if section and (not line.startswith(" ") or not line.strip()):
            section = None
            continue
        if section:
            name = line.split()[0]
            found.setdefault(section, []).append(REBRAND.get(name, name))
    return found


def build(refs, out):
    found = entries(refs)
    data = {"deb": {}, "rhel": {}, "centos": {}}
    for tag, release in RELEASES.items():
        release_data = {"installs": {}, "blocks": {}, "files": {}, "units": {}}
        for eid, entry in sorted(found.items()):
            prefix = f"{tag}-t12r2-install-"
            if eid.startswith(prefix):
                package = eid[len(prefix):]
                new, blocks = deb_install(entry["stdout"])
                state = found.get(f"{tag}-t12r2-state-{package}", {}).get("stdout", "")
                release_data["installs"][package] = {"new": new, "active": deb_active(state)}
                for name, lines in blocks.items():
                    release_data["blocks"].setdefault(name, lines)
                release_data["files"].update(files_of(found.get(f"{tag}-t12r2-files-{package}", {})
                                                      .get("stdout", "")))
        for eid, entry in sorted(found.items()):
            if eid.startswith(f"{tag}-t12r2-files-"):
                release_data["files"].update(files_of(entry["stdout"]))
        units = found.get(f"{tag}-t12r2-unit-files", {}).get("stdout", "")
        release_data["units"] = unit_texts(units)
        release_data["files"].update(files_of(units.split("=== files", 1)[1] if "=== files" in units else ""))
        data["deb"][release] = release_data
    rhel = {"installs": {}, "files": {}, "enabled": [], "units": {}}
    for eid, entry in sorted(found.items()):
        prefix = "almalinux-9-t12r2-install-"
        if eid.startswith(prefix):
            package = eid[len(prefix):]
            sections = el_sections(entry["stdout"])
            rhel["installs"][package] = {"deps": sections.get("deps", []), "weak": sections.get("weak", [])}
            rhel["files"].update(files_of(found.get(f"almalinux-9-t12r2-files-{package}", {}).get("stdout", "")))
            for line in found.get(f"almalinux-9-t12r2-state-{package}", {}).get("stdout", "").splitlines():
                parts = line.split()
                if len(parts) == 3 and parts[1] == "enabled":
                    rhel["enabled"].append(parts[0])
    rhel["units"] = unit_texts(found.get("almalinux-9-t12r2-unit-files", {}).get("stdout", ""))
    rhel["enabled"] = sorted(set(rhel["enabled"]))
    data["rhel"] = rhel
    centos = {"installs": {}, "files": files_of(found["centos-7-t12r2-files"]["stdout"]), "enabled": [], "units": {}}
    # one transaction installed httpd, mariadb-server, net-tools and iproute: the dependencies of httpd are those of
    #   its own requires (apr, apr-util, centos-logos, httpd-tools, mailcap), iproute's netfilter libraries and
    #   iptables none of the two, the rest mariadb-server's
    sections = el_sections(found["centos-7-t12r2-install"]["stdout"], yum=True)
    httpd = ("apr", "apr-util", "centos-logos", "httpd-tools", "mailcap")
    tools = ("iptables", "libmnl", "libnetfilter_conntrack", "libnfnetlink")
    deps = sections.get("deps", [])
    centos["installs"]["httpd"] = {"deps": [n for n in deps if n in httpd], "weak": []}
    centos["installs"]["mariadb-server"] = {"deps": [n for n in deps if n not in httpd + tools], "weak": []}
    text = found["centos-7-t12r2-units"]["stdout"]
    start = text.index("[Unit]")
    second = text.index("# It's not recommended")
    centos["units"] = {"httpd.service": text[start:second], "mariadb.service": text[second:]}
    data["centos"] = centos
    with gzip.open(out, "wt", encoding="utf-8", compresslevel=9) as f:
        json.dump(data, f, sort_keys=True, separators=(",", ":"))
    return data


if __name__ == "__main__":
    here = Path(__file__).resolve().parent
    result = build(sys.argv[1], Path(sys.argv[2]) if len(sys.argv) > 2
                   else here.parent / "dist" / "tpot_personas" / "package_installs.json.gz")
    for release, value in result["deb"].items():
        print(release, sorted(value["installs"]), len(value["blocks"]), len(value["files"]), sorted(value["units"]))
    print("rhel", {k: v for k, v in result["rhel"]["installs"].items() if k in ("nginx", "httpd")},
          result["rhel"]["enabled"])
    print("centos", result["centos"]["installs"], sorted(result["centos"]["files"]))
