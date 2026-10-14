"""T-Pot customizer core: catalog, selection, checks and rendering, no user interface.

A custom configuration is an edition from compose/ (the base) plus a delta: services
added from the catalog (tpot_services.yml), services removed from the base and host
port overrides. The delta is stored in the header of the generated file, so that
update.sh can build it again from the catalog and editions of a newer release.
"""

from __future__ import annotations

import glob
import hashlib
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import yaml

COMPOSE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(COMPOSE_DIR)
CATALOG_FILE = os.path.join(COMPOSE_DIR, "tpot_services.yml")
HEADER_VERSION = "2"
CUSTOM_EDITION = "CUSTOM"
# Docker's default address pools hold 31 networks, docker0 takes one and Docker
# Desktop reserves another one, so 29 are left for T-Pot (measured on Docker 29).
DEFAULT_MAX_NETWORKS = 29
GROUPS = [
    ("core", "T-Pot"),
    ("honeypots", "Honeypots"),
    ("llm", "LLM honeypots"),
    ("conpot", "Conpot (ICS)"),
    ("nsm", "NSM"),
    ("elk", "ELK"),
    ("tools", "Tools"),
]
GROUP_TITLES = dict(GROUPS)
EDITION_ORDER = ["STANDARD", "SENSOR", "MINI", "LLM", "TARPIT", "MOBILE", "MAC_WIN"]

_ENTRY_RE = re.compile(r"^  ([A-Za-z0-9_.-]+):\s*$")
_TOPLEVEL_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):\s*$")
_BANNER_RE = re.compile(r"^####")
_EDITION_RE = re.compile(r"^# T-Pot:\s*([A-Z_]+)\s*$")      # also without the space, as update.sh
_PORT_RE = re.compile(
    r"^(?:(?P<ip>\d{1,3}(?:\.\d{1,3}){3}):)?(?P<host>\d+):(?P<cont>\d+)(?:/(?P<proto>tcp|udp))?$")
_XTPOT_RE = re.compile(r"^    x-tpot:")


def edition_line(lines: List[str]) -> int:
    """The index of the line that names the edition: the first "# T-Pot:" line of the leading comment
    block (the lines above the first one that is neither empty nor a comment), -1 without one. A comment
    of your own above it keeps the edition. update.sh (fuEDITION_LINE) and tpotctl/ops.py (edition_line)
    have the same rule."""
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        if not line.lstrip().startswith("#"):
            break
        if line.startswith("# T-Pot:"):
            return index
    return -1


def edition_of(lines: List[str]) -> str:
    """The edition the lines of a compose file name, "" without one."""
    index = edition_line(lines)
    match = _EDITION_RE.match(lines[index]) if index >= 0 else None
    return match.group(1) if match else ""


class CustomizerError(Exception):
    """Broken input the customizer cannot work with."""


# ---------------------------------------------------------------------------
# Compose files
# ---------------------------------------------------------------------------

@dataclass
class ComposeFile:
    path: str
    edition: str                      # STANDARD, SENSOR, ... or "" for the catalog
    data: dict
    service_text: Dict[str, List[str]]
    network_text: Dict[str, List[str]]

    @property
    def services(self) -> dict:
        return self.data.get("services") or {}

    @property
    def networks(self) -> dict:
        return self.data.get("networks") or {}


def _split_entries(lines: List[str]) -> Dict[str, List[str]]:
    """Split the lines of a networks: / services: section into one block per entry.

    Comment lines right above an entry belong to it; anything else up to the next
    entry, such as commented-out options at column 0, stays with the entry above.
    Section banners (#### ...) are dropped, the renderer writes its own.
    """
    heads = [i for i, line in enumerate(lines) if _ENTRY_RE.match(line)]
    leads = []
    for n, start in enumerate(heads):
        floor = heads[n - 1] + 1 if n else 0
        lead = start
        while lead > floor and lines[lead - 1].startswith("#"):
            lead -= 1
        leads.append(lead)
    blocks = {}
    for n, start in enumerate(heads):
        end = leads[n + 1] if n + 1 < len(heads) else len(lines)
        block = [line for line in lines[leads[n]:end] if not _BANNER_RE.match(line)]
        while block and not block[-1].strip():
            block.pop()
        blocks[_ENTRY_RE.match(lines[start]).group(1)] = block
    return blocks


def _sections(lines: List[str]) -> Dict[str, List[str]]:
    sections, current = {}, None
    for line in lines:
        match = _TOPLEVEL_RE.match(line)
        if match:
            current = match.group(1)
            sections[current] = []
        elif current:
            sections[current].append(line)
    return sections


def load_compose(path: str) -> ComposeFile:
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as err:
        raise CustomizerError(f"{path} is not valid YAML: {err}")
    lines = text.split("\n")
    sections = _sections(lines)
    return ComposeFile(
        path=path,
        edition=edition_of(lines),
        data=data,
        service_text=_split_entries(sections.get("services", [])),
        network_text=_split_entries(sections.get("networks", [])),
    )


class Catalog:
    """tpot_services.yml plus every edition in compose/."""

    def __init__(self, compose_dir: str = COMPOSE_DIR, catalog_file: Optional[str] = None):
        self.compose_dir = compose_dir
        self.catalog = load_compose(catalog_file or os.path.join(compose_dir, "tpot_services.yml"))
        self.editions: Dict[str, ComposeFile] = {}
        for path in sorted(glob.glob(os.path.join(compose_dir, "*.yml"))):
            if os.path.abspath(path) == os.path.abspath(self.catalog.path):
                continue
            with open(path, encoding="utf-8") as handle:
                name = edition_of(handle.read().split("\n"))
            # a left-over docker-compose-custom.yml has no edition of its own
            if name and name != CUSTOM_EDITION:
                self.editions[name] = load_compose(path)
        self.order = list(self.catalog.services)
        self.meta = {name: dict(svc.get("x-tpot") or {}) for name, svc in self.catalog.services.items()}

    def edition_names(self) -> List[str]:
        return [e for e in EDITION_ORDER if e in self.editions] + \
               [e for e in self.editions if e not in EDITION_ORDER]

    def group(self, name: str) -> str:
        return self.meta.get(name, {}).get("group", "tools")

    def description(self, name: str) -> str:
        return self.meta.get(name, {}).get("description", "")

    def flag(self, name: str, key: str) -> bool:
        return bool(self.meta.get(name, {}).get(key))

    def listed(self, name: str, key: str) -> List[str]:
        return list(self.meta.get(name, {}).get(key) or [])

    def source(self, base: str, name: str) -> ComposeFile:
        """Where a service's definition comes from: the base edition, else the catalog."""
        edition = self.editions.get(base)
        if edition and name in edition.services:
            return edition
        return self.catalog

    def offered(self, base: str, name: str) -> bool:
        """Can the service be picked at all when starting from this base?"""
        if name not in self.catalog.services:
            return False
        if name in self.editions[base].services:
            return True
        if base == "SENSOR" and self.flag(name, "hive_only"):
            return False
        if base == "MAC_WIN" and self.flag(name, "linux_only"):
            return False
        return True


# ---------------------------------------------------------------------------
# Ports
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Port:
    raw: str
    ip: str          # "" = all addresses
    host: int
    container: int
    proto: str       # tcp | udp
    explicit_proto: bool

    @property
    def key(self) -> str:
        return f"{self.host}/{self.proto}"

    def with_host(self, host: int) -> "Port":
        raw = (f"{self.ip}:" if self.ip else "") + f"{host}:{self.container}"
        if self.explicit_proto:
            raw += f"/{self.proto}"
        return Port(raw, self.ip, host, self.container, self.proto, self.explicit_proto)

    def overlaps(self, other: "Port") -> bool:
        if self.host != other.host or self.proto != other.proto:
            return False
        any_ip = ("", "0.0.0.0")
        return self.ip in any_ip or other.ip in any_ip or self.ip == other.ip


def parse_port(value) -> Optional[Port]:
    """A compose short-syntax port; None for a container port without host port."""
    raw = str(value).strip()
    if re.match(r"^\d+(/(tcp|udp))?$", raw):
        return None
    match = _PORT_RE.match(raw)
    if not match:
        raise CustomizerError(
            f"port '{raw}' is not supported, use [IP:]HOST:CONTAINER[/tcp|udp] without ranges or variables")
    return Port(raw, match.group("ip") or "", int(match.group("host")), int(match.group("cont")),
                match.group("proto") or "tcp", bool(match.group("proto")))


def service_ports(definition: dict) -> List[Port]:
    ports = []
    for value in definition.get("ports") or []:
        port = parse_port(value)
        if port:
            ports.append(port)
    return ports


def service_networks(definition: dict) -> List[str]:
    if definition.get("network_mode"):
        return []
    networks = definition.get("networks")
    if not networks:
        return ["default"]
    return list(networks)


def service_depends(definition: dict) -> List[str]:
    depends = definition.get("depends_on") or []
    return list(depends)


# ---------------------------------------------------------------------------
# Selection and its header
# ---------------------------------------------------------------------------

PortOverrides = Dict[Tuple[str, int, str], Optional[int]]


@dataclass
class Selection:
    base: str
    add: List[str] = field(default_factory=list)
    remove: List[str] = field(default_factory=list)
    ports: PortOverrides = field(default_factory=dict)   # (service, host, proto) -> new host | None

    def copy(self) -> "Selection":
        return Selection(self.base, list(self.add), list(self.remove), dict(self.ports))


def override_items(ports: PortOverrides) -> List[str]:
    """One SERVICE:HOST/PROTO=NEW|- per override, sorted."""
    return [f"{service}:{host}/{proto}={'-' if new is None else new}"
            for (service, host, proto), new in sorted(ports.items())]


def format_overrides(ports: PortOverrides) -> str:
    return ",".join(override_items(ports))


def parse_overrides(text: str) -> PortOverrides:
    overrides = {}
    for item in filter(None, (part.strip() for part in text.split(","))):
        match = re.match(r"^([A-Za-z0-9_.-]+):(\d+)(?:/(tcp|udp))?=(-|\d+)$", item)
        if not match:
            raise CustomizerError(f"port override '{item}' is not SERVICE:HOSTPORT[/tcp|udp]=NEWPORT|-")
        new = None if match.group(4) == "-" else int(match.group(4))
        if new is not None and not 1 <= new <= 65535:
            raise CustomizerError(f"port override '{item}': {new} is not a port")
        overrides[(match.group(1), int(match.group(2)), match.group(3) or "tcp")] = new
    return overrides


def _split_names(text: str) -> List[str]:
    return [part.strip() for part in text.split(",") if part.strip()]


def parse_header(text: str) -> Optional[Selection]:
    """The selection a customizer file was built from, None for any other file."""
    lines = text.split("\n")
    if edition_of(lines) != CUSTOM_EDITION:
        return None
    values = {}
    for line in lines[edition_line(lines) + 1:]:
        if not line.startswith("# customizer: "):
            break
        for token in line[len("# customizer: "):].split():
            key, _, value = token.partition("=")
            values[key] = value
    if values.get("version") != HEADER_VERSION or not values.get("base"):
        return None
    return Selection(
        base=values["base"],
        add=_split_names(values.get("add", "")),
        remove=_split_names(values.get("remove", "")),
        ports=parse_overrides(values.get("port", "")),
    )


def checksum_ok(text: str) -> Optional[bool]:
    """Does the body still match the checksum? None if the file has none."""
    match = re.search(r"^# customizer: sha256=([0-9a-f]{64})\n", text, re.M)
    if not match:
        return None
    body = text[match.end():]
    return hashlib.sha256(body.encode("utf-8")).hexdigest() == match.group(1)


def current_edition(repo_dir: str = REPO_DIR) -> Tuple[str, Optional[Selection]]:
    """Edition and, for a customizer file, selection of ~/tpotce/docker-compose.yml."""
    path = os.path.join(repo_dir, "docker-compose.yml")
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return "", None
    return edition_of(text.split("\n")), parse_header(text)


def env_value(key: str, repo_dir: str = REPO_DIR) -> str:
    try:
        with open(os.path.join(repo_dir, ".env"), encoding="utf-8") as handle:
            for line in handle:
                if line.startswith(f"{key}="):
                    return line.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return ""


# ---------------------------------------------------------------------------
# Resolution and checks
# ---------------------------------------------------------------------------

@dataclass
class Finding:
    level: str                         # error | warning
    kind: str                          # service | dependency | conflict | port | network | env
    text: str
    services: Tuple[str, ...] = ()


@dataclass
class Result:
    selection: Selection
    services: List[str]                        # catalog order
    sources: Dict[str, ComposeFile]
    ports: Dict[str, List[Port]]               # after overrides
    applied: Dict[str, Dict[Port, Optional[Port]]]  # original -> replacement / None
    networks: List[str]
    needed_by: Dict[str, List[str]]            # pulled in by depends_on / requires
    findings: List[Finding]

    @property
    def errors(self) -> List[Finding]:
        return [f for f in self.findings if f.level == "error"]

    @property
    def warnings(self) -> List[Finding]:
        return [f for f in self.findings if f.level == "warning"]

    def role(self) -> str:
        return "SENSOR" if self.selection.base == "SENSOR" else "HIVE"

    def problems_of(self, name: str, kind: Optional[str] = None) -> List[Finding]:
        return [f for f in self.errors if name in f.services and kind in (None, f.kind)]


def normalize(catalog: Catalog, selection: Selection) -> Selection:
    """Drop add/remove entries that do not change anything, keep catalog order."""
    base = catalog.editions[selection.base].services
    add = [n for n in catalog.order if n in selection.add and n not in base]
    remove = [n for n in catalog.order if n in selection.remove and n in base]
    return Selection(selection.base, add, remove, dict(selection.ports))


def resolve(catalog: Catalog, selection: Selection, max_networks: int = DEFAULT_MAX_NETWORKS,
            strict: bool = True) -> Result:
    """Work out the services, ports and networks of a selection and check them.

    strict: unknown service names and overrides are errors (command line); without
    it they are dropped with a warning (rebuild after an update removed them).
    """
    findings: List[Finding] = []
    if selection.base not in catalog.editions:
        raise CustomizerError(
            f"edition '{selection.base}' does not exist, choose one of {', '.join(catalog.edition_names())}")
    base = selection.base
    unknown_level = "error" if strict else "warning"

    add, remove = [], []
    for name in selection.add:
        if name not in catalog.catalog.services:
            findings.append(Finding(unknown_level, "service", f"{name} is not a T-Pot service, left out", (name,)))
        elif not catalog.offered(base, name):
            why = "a SENSOR has no " if base == "SENSOR" else "Docker Desktop cannot run "
            findings.append(Finding("error", "service", f"{why}{name}", (name,)))
        else:
            add.append(name)
    for name in selection.remove:
        if name not in catalog.catalog.services and name not in catalog.editions[base].services:
            findings.append(Finding(unknown_level, "service", f"{name} is not a T-Pot service, nothing to remove", (name,)))
        elif catalog.flag(name, "required"):
            findings.append(Finding("error", "dependency", f"{name} is required and cannot be removed", (name,)))
        else:
            remove.append(name)
    selection = Selection(base, add, remove, dict(selection.ports))

    wanted = [n for n in catalog.editions[base].services if n not in remove] + add
    wanted += [n for n in catalog.order if catalog.flag(n, "required") and n not in wanted]
    chosen: List[str] = []
    needed_by: Dict[str, List[str]] = {}
    queue = list(wanted)
    while queue:
        name = queue.pop(0)
        if name in chosen:
            continue
        chosen.append(name)
        definition = catalog.source(base, name).services.get(name, {})
        for dep in service_depends(definition) + catalog.listed(name, "requires"):
            if dep not in catalog.catalog.services and dep not in catalog.editions[base].services:
                findings.append(Finding("error", "dependency", f"{name} depends on {dep}, which does not exist", (name,)))
                continue
            needed_by.setdefault(dep, []).append(name)
            if dep in remove:
                findings.append(Finding("error", "dependency", f"{dep} cannot be removed, {name} needs it", (dep, name)))
            if dep not in chosen:
                queue.append(dep)
    for name in chosen:
        if name not in add and name not in catalog.editions[base].services and not catalog.offered(base, name):
            findings.append(Finding("error", "dependency", f"{', '.join(needed_by.get(name, []))} needs {name}, "
                                             f"which the {base} edition cannot run", (name,)))

    services = [n for n in catalog.order if n in chosen]
    services += [n for n in chosen if n not in services]       # only in an edition
    sources = {n: catalog.source(base, n) for n in services}

    for name in services:
        home = catalog.meta.get(name, {}).get("edition")
        if not home or home == base or home not in catalog.editions:
            continue
        text = f"{name} belongs to the {home} edition"
        if catalog.group(name) == "llm":
            text += f": it needs an LLM ({name.upper()}_LLM_* in .env)"
        there = {p.host for p in service_ports(catalog.editions[home].services.get(name, {}))}
        here = {p.host for p in service_ports(catalog.catalog.services[name])}
        if there - here:
            listed = ", ".join(str(p) for p in sorted(here)) or "none"
            text += f", and outside it only port {listed} is published" if len(here) == 1 else \
                f", and outside it only ports {listed} are published"
        findings.append(Finding("warning", "edition", text, (name,)))

    pairs = set()
    for name in services:
        for other in catalog.listed(name, "conflicts"):
            if other in services:
                pairs.add(tuple(sorted((name, other))))
    for first, second in sorted(pairs):
        findings.append(Finding("error", "conflict", f"{first} and {second} cannot run at the same time",
                                (first, second)))

    ports: Dict[str, List[Port]] = {}
    applied: Dict[str, Dict[Port, Optional[Port]]] = {}
    pending = dict(selection.ports)
    for name in services:
        try:
            original = service_ports(sources[name].services[name])
        except CustomizerError as err:
            findings.append(Finding("error", "port", f"{name}: {err}", (name,)))
            original = []
        current, changes = [], {}
        for port in original:
            key = (name, port.host, port.proto)
            if key in pending:
                new = pending.pop(key)
                changes[port] = None if new is None else port.with_host(new)
                if new is not None:
                    current.append(changes[port])
            else:
                current.append(port)
        ports[name], applied[name] = current, changes
    for (name, host, proto), _new in pending.items():
        findings.append(Finding(unknown_level, "port", f"{name} has no host port {host}/{proto}, override left out", (name,)))
        selection.ports.pop((name, host, proto), None)

    by_key: Dict[Tuple[int, str], List[Tuple[str, Port]]] = {}
    for name in services:
        for port in ports[name]:
            by_key.setdefault((port.host, port.proto), []).append((name, port))
    for (host, proto), users in sorted(by_key.items()):
        clash = [a for a, pa in users if any(b != a and pa.overlaps(pb) for b, pb in users)]
        clash = [n for n in services if n in clash]
        if clash:
            findings.append(Finding("error", "port", f"port {host}/{proto} is used by {' and '.join(clash)}",
                                    tuple(clash)))

    networks: List[str] = []
    for name in services:
        for network in service_networks(sources[name].services[name]):
            if network not in networks:
                networks.append(network)
    if len(networks) > max_networks:
        findings.append(Finding(
            "error", "network",
            f"{len(networks)} networks, but Docker's default address pools leave room for about "
            f"{max_networks}; remove services or raise the limit with --max-networks if you "
            "configured more default-address-pools"))

    role = "SENSOR" if base == "SENSOR" else "HIVE"
    tpot_type = env_value("TPOT_TYPE")
    if tpot_type and tpot_type != role:
        findings.append(Finding("warning", "env", f"TPOT_TYPE in .env is {tpot_type}, this configuration is a {role}"))

    return Result(normalize(catalog, selection), services, sources, ports, applied, networks, needed_by, findings)


def free_port(port: Port, taken: List[Port]) -> int:
    """The next host port without a clash: 80 -> 8080, 2404 -> 2405."""
    start = port.host + 1 if port.host >= 1024 else 8000 + port.host
    for candidate in list(range(start, 65536)) + list(range(1024, start)):
        if not any(p.overlaps(port.with_host(candidate)) for p in taken):
            return candidate
    raise CustomizerError(f"no free host port left for {port.key}")


def suggestions(catalog: Catalog, selection: Selection, name: str,
                max_networks: int = DEFAULT_MAX_NETWORKS) -> List[Tuple[str, PortOverrides]]:
    """Port overrides that resolve the port conflicts of one service.

    First the host ports other editions use for it (wordpot on 8080 as in STANDARD),
    then the next free ports. Only suggestions that leave the service conflict-free.
    """
    result = resolve(catalog, selection, max_networks, strict=False)
    if name not in result.services:
        return []
    source = result.sources[name]
    original = service_ports(source.services[name])
    others = [p for n in result.services if n != name for p in result.ports[n]]
    variants: List[Tuple[List[str], PortOverrides]] = []
    for label in catalog.edition_names() + ["catalog"]:
        compose = catalog.editions.get(label, catalog.catalog)
        if compose is source or name not in compose.services:
            continue
        try:
            overrides = _variant_overrides(name, original, service_ports(compose.services[name]))
        except CustomizerError:
            continue
        if not overrides:
            continue
        for labels, known in variants:
            if known == overrides:
                labels.append(label)
                break
        else:
            variants.append(([label], overrides))
    candidates = [(f"host ports as in {', '.join(labels)}", overrides) for labels, overrides in variants]
    conflicting = [p for p in result.ports[name] if any(p.overlaps(o) for o in others)]
    if conflicting:
        overrides, taken = {}, list(others) + list(result.ports[name])
        reverse = {v: k for k, v in result.applied[name].items() if v is not None}
        for port in conflicting:
            new = free_port(port, taken)
            first = reverse.get(port, port)
            overrides[(name, first.host, first.proto)] = new
            taken.append(port.with_host(new))
        if overrides not in [c[1] for c in candidates]:
            candidates.append(("next free host ports", overrides))
    good = []
    for label, overrides in candidates:
        trial = selection.copy()
        trial.ports = {k: v for k, v in trial.ports.items() if k[0] != name}
        trial.ports.update(overrides)
        trial_result = resolve(catalog, trial, max_networks, strict=False)
        if not trial_result.problems_of(name, "port"):
            good.append((label, overrides))
    return good


def _variant_overrides(name: str, original: List[Port], variant: List[Port]) -> PortOverrides:
    """Overrides that turn the original host ports into the variant's (remap or drop)."""
    overrides: PortOverrides = {}
    same = {(p.ip, p.host, p.container, p.proto) for p in variant}
    for port in original:
        if (port.ip, port.host, port.container, port.proto) in same:
            continue
        mates = [p for p in variant if p.container == port.container and p.proto == port.proto and p.ip == port.ip]
        siblings = [p for p in original if p.container == port.container and p.proto == port.proto]
        if len(mates) == 1 and len(siblings) == 1:
            overrides[(name, port.host, port.proto)] = mates[0].host
        elif not mates:
            overrides[(name, port.host, port.proto)] = None
    return overrides


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _strip_xtpot(block: List[str]) -> List[str]:
    """Drop the x-tpot key, one flow-style line or a block nested below it."""
    out, nested = [], False
    for line in block:
        if _XTPOT_RE.match(line):
            nested = line.split(":", 1)[1].strip() == ""
            continue
        if nested and line.startswith("      "):
            continue
        nested = False
        out.append(line)
    return out


def _apply_port_changes(name: str, block: List[str], changes: Dict[Port, Optional[Port]]) -> List[str]:
    if not changes:
        return block
    block = list(block)
    start = next((i for i, line in enumerate(block) if re.match(r"^    ports:\s*$", line)), None)
    if start is None:
        raise CustomizerError(f"{name}: ports have to be a block list to change them")
    end = start + 1
    while end < len(block) and (block[end].startswith("#") or block[end].startswith("     ")
                                or not block[end].strip()):
        end += 1
    for old, new in changes.items():
        for i in range(start + 1, end):
            if block[i] is None:
                continue
            match = re.match(r"^(\s+-\s*)([\"']?)([^\"'#\s]+)\2(\s*(#.*)?)$", block[i])
            if match and match.group(3) == old.raw:
                block[i] = None if new is None else (
                    f"{match.group(1)}{match.group(2)}{new.raw}{match.group(2)}{match.group(4)}")
                break
        else:
            raise CustomizerError(f"{name}: port line '{old.raw}' not found")
    block = [line for line in block if line is not None]
    end = start + 1
    while end < len(block) and (block[end].startswith("#") or block[end].startswith("     ")
                                or not block[end].strip()):
        end += 1
    if not any(re.match(r"^\s+-\s", line) for line in block[start + 1:end]):
        del block[start]
    return block


def render(catalog: Catalog, result: Result) -> str:
    """The compose file for a resolved selection, header and checksum included."""
    selection = result.selection
    body: List[str] = [
        f"# Built by compose/customizer.py from the {selection.base} edition. Change it with the",
        "# customizer, update.sh rebuilds it on updates unless it was edited by hand.",
    ]
    named = [n for n in result.networks if n != "default"]
    if named:
        body.append("networks:")
        for network in named:
            owners = [n for n in result.services if network in service_networks(result.sources[n].services[n])]
            for compose in [result.sources[n] for n in owners] + [catalog.editions[selection.base], catalog.catalog]:
                if network in compose.network_text:
                    body.extend(compose.network_text[network])
                    break
            else:
                raise CustomizerError(f"network {network} is not defined anywhere")
        body.append("")
    body.append("services:")
    group = None
    for name in result.services:
        if catalog.group(name) != group:
            group = catalog.group(name)
            title = GROUP_TITLES.get(group, group)
            body.extend(["", "#" * 18, f"#### {title}", "#" * 18])
        block = _strip_xtpot(result.sources[name].service_text[name])
        block = _apply_port_changes(name, block, result.applied[name])
        body.append("")
        body.extend(block)
    text_body = "\n".join(body) + "\n"
    header = [
        f"# T-Pot: {CUSTOM_EDITION}",
        f"# customizer: version={HEADER_VERSION} base={selection.base}",
        f"# customizer: add={','.join(selection.add)}",
        f"# customizer: remove={','.join(selection.remove)}",
        f"# customizer: port={format_overrides(selection.ports)}",
        f"# customizer: sha256={hashlib.sha256(text_body.encode('utf-8')).hexdigest()}",
    ]
    text = "\n".join(header) + "\n" + text_body
    _check_rendered(text, result)
    return text


def _check_rendered(text: str, result: Result) -> None:
    """The rendered text has to say exactly what the resolution worked out."""
    data = yaml.safe_load(text)
    services = data.get("services") or {}
    if list(services) != result.services:
        raise CustomizerError("rendered services differ from the selection")
    for name, definition in services.items():
        if "x-tpot" in definition:
            raise CustomizerError(f"{name}: x-tpot left in the output")
        if service_ports(definition) != result.ports[name]:
            raise CustomizerError(f"{name}: rendered ports differ from the selection")
    named = [n for n in result.networks if n != "default"]
    if sorted((data.get("networks") or {})) != sorted(named):
        raise CustomizerError("rendered networks differ from the selection")


# ---------------------------------------------------------------------------
# Adoption: any compose file becomes a selection of this release
# ---------------------------------------------------------------------------

# Services of earlier releases that this one no longer has; update.sh names the same
# in myDROPPED_SERVICES (compose/tests/test_customizer.py keeps both alike).
DROPPED_SERVICES = ["spiderfoot", "fatt", "tanner_redis", "tanner_phpox", "tanner_api", "tanner", "snare"]
# The networks of earlier releases (24.04.0 to 24.04.1) and of this one. A service that sat on
# a network of its own there (cowrie_local) sits on the one of this release now, which is no
# loss; any other network a service of the file is on is named.
EARLIER_NETWORKS = [
    "adbhoney_local", "beelzebub_local", "ciscoasa_local", "citrixhoneypot_local", "conpot_local_IEC104",
    "conpot_local_guardian_ast", "conpot_local_ipmi", "conpot_local_kamstrup_382", "cowrie_local",
    "ddospot_local", "dicompot_local", "dionaea_local", "elasticpot_local", "endlessh_local",
    "ewsposter_local", "galah_local", "go-pot_local", "h0neytr4p_local", "hellpot_local", "heralding_local",
    "honeyaml_local", "honeypot_local", "honeypots_local", "ipphoney_local", "log4pot_local",
    "mailoney_local", "medpot_local", "miniprint_local", "nginx_local", "rdphoneypot_local",
    "redishoneypot_local", "sentrypeer_local", "spiderfoot_local", "suricata_local", "tanner_local",
    "tpotinit_local", "wordpot_local",
]
V1_EDITION_LINE = "# T-Pot: CUSTOM EDITION"           # the customizer of 24.04.1 and earlier
# where the images of T-Pot come from; repository and tag of these are the release's business
_TPOT_REPOS = ("${TPOT_REPO}", "dtagdevsec", "docker.io/dtagdevsec", "ghcr.io/telekom-security")
_SHOW_MAX = 80


class TextPortLoader(yaml.SafeLoader):
    """YAML 1.1 without its base-60 numbers: an unquoted 22:22 stays the text "22:22", as
    docker compose (YAML 1.2) reads it; safe_load would make it 1342."""


TextPortLoader.yaml_implicit_resolvers = {
    first: [(tag, regexp) for tag, regexp in resolvers
            if tag not in ("tag:yaml.org,2002:int", "tag:yaml.org,2002:float")]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()}
TextPortLoader.add_implicit_resolver(
    "tag:yaml.org,2002:int",
    re.compile(r"""^(?:[-+]?0b[0-1_]+
                |[-+]?0[0-7_]+
                |[-+]?(?:0|[1-9][0-9_]*)
                |[-+]?0x[0-9a-fA-F_]+)$""", re.X),
    list("-+0123456789"))
TextPortLoader.add_implicit_resolver(
    "tag:yaml.org,2002:float",
    re.compile(r"""^(?:[-+]?(?:[0-9][0-9_]*)\.[0-9_]*(?:[eE][-+][0-9]+)?
                |\.[0-9][0-9_]*(?:[eE][-+][0-9]+)?
                |[-+]?\.(?:inf|Inf|INF)
                |\.(?:nan|NaN|NAN))$""", re.X),
    list("-+0123456789."))


def load_text_ports(text: str):
    return yaml.load(text, Loader=TextPortLoader)


@dataclass
class Adoption:
    selection: Selection
    dropped: List[str]                 # "name (why)", services the new file does not have
    not_carried: List[str]             # "service: what", what the new file leaves out beyond host ports
    v1: bool                           # built by the customizer of an earlier release
    moved: List[str] = field(default_factory=list)   # host ports the release moved, "svc: C/PROTO host A -> B"
    new: List[str] = field(default_factory=list)     # services of the base the file does not have


def _show(value) -> str:
    if value is None:
        text = "none"
    elif isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, (dict, list)):
        text = yaml.safe_dump(value, default_flow_style=True, width=10000).strip()
    else:
        text = str(value)
    text = " ".join(text.split())
    return text if len(text) <= _SHOW_MAX else text[:_SHOW_MAX - 3] + "..."


def _canon(value):
    """A value as compose means it: numbers and booleans as text, mappings without order."""
    if isinstance(value, dict):
        return {str(k): _canon(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_canon(v) for v in value]
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return None
    return str(value).strip()


def _items(value) -> list:
    """A list key that may also be one string (env_file, tmpfs)."""
    if value is None:
        return []
    if isinstance(value, list):
        return [_canon(v) for v in value]
    return [_canon(value)]


def _environment(value) -> Dict[str, Optional[str]]:
    if isinstance(value, dict):
        return {str(k): (None if v is None else _canon(v)) for k, v in value.items()}
    out: Dict[str, Optional[str]] = {}
    for item in _items(value):
        key, sep, val = str(item).partition("=")
        out[key.strip()] = val if sep else None
    return out


def _same_value(mine: Optional[str], theirs: Optional[str]) -> bool:
    """Equal, or the release only gives a variable of the file a default (${X} -> ${X:-y})."""
    if mine == theirs or mine is None or theirs is None:
        return mine == theirs
    return mine == re.sub(r"\$\{(\w+):?[-?+][^}]*\}", r"${\1}", theirs)


def _depends(value) -> Dict[str, Optional[dict]]:
    if isinstance(value, dict):
        return {str(k): _canon(v) if v else None for k, v in value.items()}
    return {str(k): None for k in (value or [])}


def _image_key(value) -> Tuple[str, str]:
    text = re.sub(r"\$\{TPOT_REPO[^}]*\}|\$TPOT_REPO\b", "${TPOT_REPO}", str(value or "").strip())
    repo, _, rest = text.rpartition("/")
    if repo in _TPOT_REPOS:
        return ("tpot", re.split(r"[:@]", rest, maxsplit=1)[0])
    return ("other", text)


def _command(value):
    if isinstance(value, list):
        return [str(v) for v in value]
    if value is None:
        return None
    try:
        return shlex.split(str(value))
    except ValueError:
        return str(value).split()


def _release_has(new: dict, key: str, shown: Optional[str] = None) -> str:
    if key not in new:
        return " (the release has none)"
    return f" (the release has {shown if shown is not None else _show(new[key])})"


def _ports_of_file(name: str, old: dict, lost: List[str]) -> List[Port]:
    ports = []
    for value in old.get("ports") or []:
        try:
            port = parse_port(value)
        except CustomizerError:
            lost.append(f"{name}: port {_show(value)} (only [IP:]HOST:CONTAINER[/udp] can be carried)")
            continue
        if port:
            ports.append(port)
    return ports


OldPorts = Dict[str, List[Port]]


def old_catalog_ports(text: str) -> Optional[OldPorts]:
    """The host ports of every service of the catalogue an old file was built from (only
    services.*.ports, no x-tpot needed); None if it cannot be read."""
    try:
        data = load_text_ports(text)
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("services"), dict) or not data["services"]:
        return None
    ports: OldPorts = {}
    for name, definition in data["services"].items():
        found = []
        values = definition.get("ports") if isinstance(definition, dict) else None
        for value in values or []:
            try:
                port = parse_port(value)
            except CustomizerError:
                continue
            if port:
                found.append(port)
        ports[str(name)] = found
    return ports


def _host(port: Port) -> str:
    return f"{port.ip}:{port.host}" if port.ip else str(port.host)


def _adopt_ports(name: str, old: dict, new: dict, lost: List[str], moved: List[str],
                 old_ports: Optional[OldPorts] = None) -> PortOverrides:
    """Host port overrides that carry the host ports of the file, per container port.

    A container port of both with another host port in the file becomes an override when the
    file's host port is no default of the old catalogue (old_ports) either; one it is the default
    of is named in moved (the release's wins); without old_ports it is named in lost. One
    only the file has is named in moved when the old catalogue had it too (the release removed
    it), else in lost; one only the release has stays published. Adopting
    never drops a port of the release (no override to "-"): an earlier file was built from an
    earlier catalogue, a port missing there is no choice of the user's.
    """
    mine = _ports_of_file(name, old, lost)
    release = service_ports(new)
    any_ip = ("", "0.0.0.0")

    def same_ip(a: Port, b: Port) -> bool:
        return a.ip == b.ip or (a.ip in any_ip and b.ip in any_ip)

    def same(a: Port, b: Port) -> bool:
        return a.host == b.host and same_ip(a, b)

    overrides: PortOverrides = {}
    groups: Dict[Tuple[int, str], Tuple[List[Port], List[Port]]] = {}
    for port in mine:
        groups.setdefault((port.container, port.proto), ([], []))[1].append(port)
    for port in release:
        groups.setdefault((port.container, port.proto), ([], []))[0].append(port)
    for (container, proto), (theirs, ours) in groups.items():
        if not ours:
            continue                                   # the release's port stays published
        defaults = [p for p in (old_ports or {}).get(name, []) if (p.container, p.proto) == (container, proto)]

        def removed_by_release(port: Port) -> bool:
            """A port of the old catalogue that the release gave up: no change of yours."""
            return old_ports is not None and any(same(d, port) for d in defaults)

        if not theirs:
            if all(removed_by_release(o) for o in ours):
                moved.append(f"{name}: {container}/{proto} (removed by the release)")
            else:
                lost.append(f"{name}: port {container}/{proto} (the release no longer has it)")
            continue
        left_theirs = [t for t in theirs if not any(same(t, o) for o in ours)]
        left_ours = [o for o in ours if not any(same(t, o) for t in theirs)]
        for there, here in zip(left_theirs, left_ours):
            if old_ports is None:
                # no old catalogue: the file's host port may be its default, the release's wins
                lost.append(f"{name}: host port {_host(here)} for {container}/{proto} (the release has {_host(there)})")
            elif any(same(d, here) for d in defaults):
                moved.append(f"{name}: {container}/{proto} host {_host(here)} -> {_host(there)} (changed by the release)")
            else:
                if here.host != there.host:
                    overrides[(name, there.host, there.proto)] = here.host
                if not same_ip(here, there):
                    lost.append(f"{name}: port {here.raw} (only the host port is carried, the release has {there.raw})")
        for here in left_ours[len(left_theirs):]:
            if removed_by_release(here):
                moved.append(f"{name}: {here.raw} (removed by the release)")
            else:
                lost.append(f"{name}: port {here.raw} (the release has no such port)")
    container_only = {str(v).strip() for v in new.get("ports") or [] if re.match(r"^\d+(/(tcp|udp))?$", str(v).strip())}
    for value in old.get("ports") or []:
        raw = str(value).strip()
        if re.match(r"^\d+(/(tcp|udp))?$", raw) and raw not in container_only:
            lost.append(f"{name}: port {raw} (the release has no such port)")
    return overrides


def _file_networks(definition: dict) -> List[str]:
    networks = definition.get("networks")
    if isinstance(networks, dict):
        return [str(n) for n in networks]
    return [str(n) for n in networks or []]


def _flat(value) -> str:
    """driver_opts {a: 1} as a=1, anything else as _show does."""
    if isinstance(value, dict) and all(not isinstance(v, (dict, list)) for v in value.values()):
        return _show(",".join(f"{k}={_show(v)}" for k, v in value.items()))
    return _show(value)


def _own_network(service: str, network: str) -> bool:
    """A network an earlier release gave this service (cowrie_local, conpot_local_IEC104)."""
    return network in EARLIER_NETWORKS and network.startswith(service.split("_", 1)[0] + "_")


_ADOPT_KEYS = ("image", "command", "entrypoint", "environment", "env_file", "volumes", "tmpfs", "networks",
               "mem_limit", "cpus", "cap_add", "network_mode", "restart", "read_only", "depends_on")
_LIST_LABELS = {"env_file": "env_file", "volumes": "volume", "tmpfs": "tmpfs", "cap_add": "cap_add"}


def _not_carried(name: str, old: dict, new: dict) -> List[str]:
    """One line per thing of the file's definition the release's definition does not have."""
    lost: List[str] = []
    for key in list(_ADOPT_KEYS) + [k for k in old if k not in _ADOPT_KEYS]:
        if key not in old or key in ("ports", "x-tpot"):
            continue
        mine, theirs = old[key], new.get(key)
        if key == "image":
            if _image_key(mine) != _image_key(theirs):
                lost.append(f"{name}: image {_show(mine)}" + _release_has(new, key))
        elif key in ("command", "entrypoint"):
            if _command(mine) != _command(theirs):
                lost.append(f"{name}: {key} {_show(mine)}" + _release_has(new, key))
        elif key == "environment":
            have = _environment(theirs)
            for var, val in _environment(mine).items():
                shown = var if val is None else f"{var}={val}"
                if var not in have:
                    lost.append(f"{name}: environment {shown}")
                elif not _same_value(val, have[var]):
                    other = var if have[var] is None else f"{var}={have[var]}"
                    lost.append(f"{name}: environment {_show(shown)} (the release has {_show(other)})")
        elif key in _LIST_LABELS:
            have = _items(theirs)
            for item in _items(mine):
                if item not in have:
                    lost.append(f"{name}: {_LIST_LABELS[key]} {_show(item)}")
        elif key == "networks":
            have = theirs if isinstance(theirs, dict) else {n: None for n in (theirs or [])}
            entries = mine if isinstance(mine, dict) else {n: None for n in (mine or [])}
            for network, settings in entries.items():
                settings = _canon(settings) or None
                if network in have:
                    if settings and settings != (_canon(have[network]) or None):
                        lost.append(f"{name}: network {network} {_show(settings)}")
                elif settings or not _own_network(name, network):
                    lost.append(f"{name}: network {network}" + (f" {_show(settings)}" if settings else ""))
        elif key == "depends_on":
            have = _depends(theirs)
            for dep, cond in _depends(mine).items():
                if dep not in have:
                    lost.append(f"{name}: depends_on {dep}")
                elif cond and cond != have[dep]:
                    lost.append(f"{name}: depends_on {dep} {_show(cond)}"
                                f" (the release has {_show(have[dep])})")
        elif key == "cpus":
            try:
                same = theirs is not None and float(mine) == float(theirs)
            except (TypeError, ValueError):
                same = _canon(mine) == _canon(theirs)
            if not same:
                lost.append(f"{name}: cpus {_show(mine)}" + _release_has(new, key))
        elif key == "mem_limit":
            if theirs is None or str(mine).strip().lower() != str(theirs).strip().lower():
                lost.append(f"{name}: mem_limit {_show(mine)}" + _release_has(new, key))
        elif _canon(mine) != _canon(theirs):
            lost.append(f"{name}: {key} {_show(mine)}" + _release_has(new, key))
    return lost


def adopt(catalog: Catalog, text: str, tpot_type: str = "HIVE", old_ports: Optional[OldPorts] = None) -> Adoption:
    """The selection of this release closest to a compose file of any origin, what it drops
    and what it does not carry. The base is the edition of the T-Pot type: STANDARD for a
    HIVE, SENSOR for a SENSOR. old_ports (old_catalog_ports) tells host ports of your own from
    the defaults of the catalogue the file was built from; without it no host port is carried."""
    try:
        data = load_text_ports(text)
    except yaml.YAMLError as err:
        raise CustomizerError(f"the file is not valid YAML: {err}")
    if not isinstance(data, dict) or not isinstance(data.get("services"), dict) or not data["services"]:
        raise CustomizerError("the file has no services")
    lines = text.split("\n")
    index = edition_line(lines)
    v1 = index >= 0 and lines[index].strip() == V1_EDITION_LINE
    base = "SENSOR" if str(tpot_type).strip().upper() == "SENSOR" else "STANDARD"
    edition = catalog.editions[base]

    dropped: List[str] = []
    kept: List[str] = []
    for name in data["services"]:
        if name in DROPPED_SERVICES:
            dropped.append(f"{name} (removed in this release)")
        elif name not in catalog.catalog.services and name not in edition.services:
            dropped.append(f"{name} (not a T-Pot service)")
        elif not catalog.offered(base, name):
            dropped.append(f"{name} (a {base} has no {name})")
        else:
            kept.append(name)

    # everything the kept services need comes along, the base keeps only that
    wanted: List[str] = []
    queue = kept + [n for n in catalog.order if catalog.flag(n, "required")]
    while queue:
        name = queue.pop(0)
        if name in wanted:
            continue
        wanted.append(name)
        definition = catalog.source(base, name).services.get(name) or {}
        queue += [d for d in service_depends(definition) + catalog.listed(name, "requires") if d not in wanted]
    add = [n for n in catalog.order if n in kept and n not in edition.services]
    remove = [n for n in catalog.order if n in edition.services and n not in wanted]

    not_carried: List[str] = []
    moved: List[str] = []
    ports: PortOverrides = {}
    for name in kept:
        old = data["services"][name] or {}
        if not isinstance(old, dict):
            raise CustomizerError(f"service {name} is no mapping")
        new = catalog.source(base, name).services.get(name) or {}
        ports.update(_adopt_ports(name, old, new, not_carried, moved, old_ports))
        not_carried += _not_carried(name, old, new)

    # top level: settings of a network the new file has, anything besides services and networks
    networks = data.get("networks") if isinstance(data.get("networks"), dict) else {}
    used = {n for name in wanted for n in service_networks(catalog.source(base, name).services.get(name) or {})}
    for network, settings in networks.items():
        settings = _canon(settings) or None
        if not settings:
            continue                                   # a plain network: nothing to lose
        if network not in used:
            sat = [n for n in kept if network in _file_networks(data["services"][n] or {})]
            if not sat:
                continue                               # no service the new file keeps was on it
            now = []
            for name in sat:
                for other in service_networks(catalog.source(base, name).services.get(name) or {}):
                    if other not in now:
                        now.append(other)
            where = f"puts {', '.join(sat)} on {', '.join(now)}" if now else \
                f"gives {', '.join(sat)} no network of its own"
            for key, value in settings.items() if isinstance(settings, dict) else [("settings", settings)]:
                not_carried.append(f"network {network}: {key} {_flat(value)} (the release {where})")
            continue
        release = None
        for compose in (edition, catalog.catalog):
            if network in compose.networks:
                release = _canon(compose.networks[network]) or None
                break
        if settings != release:
            not_carried.append(f"network {network}: {_show(settings)}"
                               f" (the release has {_show(release)})")
    for key in data:
        if key not in ("services", "networks", "version"):
            not_carried.append(f"{key}: {_show(data[key])}")

    new = [f"{n} (in this release, not in your file: compose/customizer.py --add {n})"
           for n in remove if not catalog.flag(n, "hidden")]
    selection = normalize(catalog, Selection(base, add, remove, ports))
    return Adoption(selection, dropped, not_carried, v1, moved, new)


def adopt_type(text: str, env_type: str = "") -> str:
    """HIVE or SENSOR for --adopt without --tpot-type: TPOT_TYPE of .env, else the edition of
    the file (a SENSOR, or a customizer file built from SENSOR), else HIVE."""
    if env_type.strip():
        return "SENSOR" if env_type.strip().upper() == "SENSOR" else "HIVE"
    selection = parse_header(text)
    if edition_of(text.split("\n")) == "SENSOR" or (selection and selection.base == "SENSOR"):
        return "SENSOR"
    return "HIVE"


def compose_check(path: str, repo_dir: str = REPO_DIR) -> Optional[str]:
    """Let docker compose validate the file; None if fine or docker is not there."""
    if not shutil.which("docker"):
        return None
    env_file = os.path.join(repo_dir, ".env")
    if not os.path.exists(env_file):
        env_file = os.path.join(repo_dir, "env.example")
    try:
        proc = subprocess.run(
            ["docker", "compose", "--project-directory", repo_dir, "--env-file", env_file, "-f", path, "config", "-q"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode == 0:
        return None
    errors = [line for line in proc.stderr.splitlines() if "level=warning" not in line]
    if errors and "docker: 'compose' is not a docker command" in errors[0]:
        return None
    return "\n".join(errors) or f"docker compose config failed with exit code {proc.returncode}"


def write_output(text: str, path: str, repo_dir: str = REPO_DIR) -> None:
    """Write atomically, only after docker compose accepted the file."""
    directory = os.path.dirname(os.path.abspath(path))
    handle, temp = tempfile.mkstemp(prefix=".customizer-", suffix=".yml", dir=directory)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(text)
        problem = compose_check(temp, repo_dir)
        if problem:
            raise CustomizerError(f"docker compose rejects the result:\n{problem}")
        os.chmod(temp, 0o644)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
