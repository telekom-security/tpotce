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
_EDITION_RE = re.compile(r"^# T-Pot: ([A-Z_]+)\s*$")
_PORT_RE = re.compile(
    r"^(?:(?P<ip>\d{1,3}(?:\.\d{1,3}){3}):)?(?P<host>\d+):(?P<cont>\d+)(?:/(?P<proto>tcp|udp))?$")
_XTPOT_RE = re.compile(r"^    x-tpot:")


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
    match = _EDITION_RE.match(lines[0]) if lines else None
    sections = _sections(lines)
    return ComposeFile(
        path=path,
        edition=match.group(1) if match else "",
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
                match = _EDITION_RE.match(handle.readline())
            # a left-over docker-compose-custom.yml has no edition of its own
            if match and match.group(1) != CUSTOM_EDITION:
                self.editions[match.group(1)] = load_compose(path)
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


def format_overrides(ports: PortOverrides) -> str:
    items = []
    for (service, host, proto), new in sorted(ports.items()):
        items.append(f"{service}:{host}/{proto}={'-' if new is None else new}")
    return ",".join(items)


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
    if not lines or not _EDITION_RE.match(lines[0]) or _EDITION_RE.match(lines[0]).group(1) != CUSTOM_EDITION:
        return None
    values = {}
    for line in lines[1:]:
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
    match = _EDITION_RE.match(text.split("\n", 1)[0])
    return (match.group(1) if match else ""), parse_header(text)


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
