"""Which honeypots T-Pot runs here, and on which ports.

The services and their published ports come from `docker compose config --format json` of the
compose file in use, with the rules of hptest.sh `fuPORTS`: ports bound to 127.0.0.1 / ::1 and
the ports of T-Pot itself (64290 - 64309) are left out. A port the customizer moved keeps its
target (the port inside the container), which tells the probe what the service speaks.

The services are matched with the catalogue compose/tpot_services.yml: the groups `honeypots`,
`conpot` and `llm` are honeypots, `nsm` is passive (external). Whether a honeypot runs comes from
the containers docker knows (`ops.containers()`).
"""

import json
import os
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional

from tpotctl.checks import CheckError

HONEYPOT_GROUPS = ("honeypots", "conpot", "llm")
PASSIVE_GROUPS = ("nsm",)
CATALOGUE = os.path.join("compose", "tpot_services.yml")

# what only sees traffic from outside: honeytrap and glutton take the connections the NFQ rules of
# tpotinit hand them, p0f and suricata capture passively; a probe from this host reaches none
EXTERNAL = {"honeytrap", "glutton", "p0f", "suricata"}
EXTERNAL_HINT = "seen only for traffic from outside (NFQ / passive capture)"

# 64290 - 64309 are the ports of T-Pot itself (SSH, web UI, sensor ingest), as in hptest.sh
OWN_PORTS = range(64290, 64310)
LOCAL_ADDRESSES = ("127.0.0.1", "::1")

# The Logstash input of each honeypot (docker/elk/logstash/dist/logstash.conf): its type and its
# path under the data folder (TPOT_DATA_PATH, /data in Logstash). test_checks keeps both in step
# with logstash.conf. Conpot has one input for conpot/log/*.json, each template its own file.
LOGS: Dict[str, tuple] = {
    "adbhoney": ("Adbhoney", "adbhoney/log/adbhoney.json"),
    "beelzebub": ("Beelzebub", "beelzebub/log/beelzebub.json"),
    "ciscoasa": ("Ciscoasa", "ciscoasa/log/ciscoasa.json"),
    "citrixhoneypot": ("CitrixHoneypot", "citrixhoneypot/log/server.log"),
    "conpot_IEC104": ("ConPot", "conpot/log/conpot_IEC104.json"),
    "conpot_guardian_ast": ("ConPot", "conpot/log/conpot_guardian_ast.json"),
    "conpot_ipmi": ("ConPot", "conpot/log/conpot_ipmi.json"),
    "conpot_kamstrup_382": ("ConPot", "conpot/log/conpot_kamstrup_382.json"),
    "conpot_beckhoff_cx": ("ConPot", "conpot/log/conpot_beckhoff_cx.json"),
    "conpot_building_automation": ("ConPot", "conpot/log/conpot_building_automation.json"),
    "conpot_hart_gateway": ("ConPot", "conpot/log/conpot_hart_gateway.json"),
    "conpot_iccp": ("ConPot", "conpot/log/conpot_iccp.json"),
    "conpot_plc_modbus": ("ConPot", "conpot/log/conpot_plc_modbus.json"),
    "cowrie": ("Cowrie", "cowrie/log/cowrie.json"),
    "ddospot": ("Ddospot", "ddospot/log/*.log"),
    "dicompot": ("Dicompot", "dicompot/log/dicompot.log"),
    "dionaea": ("Dionaea", "dionaea/log/dionaea.json"),
    "elasticpot": ("ElasticPot", "elasticpot/log/elasticpot.json"),
    "endlessh": ("Endlessh", "endlessh/log/endlessh.log"),
    "galah": ("Galah", "galah/log/galah.json"),
    "glutton": ("Glutton", "glutton/log/glutton.log"),
    "go-pot": ("Go-pot", "go-pot/log/go-pot.json"),
    "h0neytr4p": ("H0neytr4p", "h0neytr4p/log/log.json"),
    "hellpot": ("Hellpot", "hellpot/log/hellpot.log"),
    "heralding": ("Heralding", "heralding/log/auth.csv"),
    "honeyaml": ("Honeyaml", "honeyaml/log/honeyaml.log"),
    "honeypots": ("Honeypots", "honeypots/log/*.log"),
    "honeytrap": ("Honeytrap", "honeytrap/log/attackers.json"),
    "ipphoney": ("Ipphoney", "ipphoney/log/ipphoney.json"),
    "log4pot": ("Log4pot", "log4pot/log/log4pot.log"),
    "mailoney": ("Mailoney", "mailoney/log/log.json"),
    "medpot": ("Medpot", "medpot/log/medpot.log"),
    "miniprint": ("Miniprint", "miniprint/log/miniprint.json"),
    "p0f": ("P0f", "p0f/log/p0f.json"),
    "rdphoneypot": ("RDPHoneypot", "rdphoneypot/log/rdphoneypot.json"),
    "redishoneypot": ("Redishoneypot", "redishoneypot/log/redishoneypot.log"),
    "sentrypeer": ("Sentrypeer", "sentrypeer/log/sentrypeer.json"),
    "suricata": ("Suricata", "suricata/log/eve.json"),
    "wordpot": ("Wordpot", "wordpot/log/wordpot.json"),
}


@dataclass(frozen=True)
class Port:
    published: int          # the port of the host, what a probe connects to
    target: int             # the port inside the container: what the service speaks there
    protocol: str = "tcp"   # tcp or udp
    host_ip: str = ""       # the address it is bound to, "" for every address

    def __str__(self) -> str:
        return f"{self.published}/{self.protocol}"


@dataclass
class Honeypot:
    name: str               # the service name of the compose file (= the catalogue)
    group: str              # honeypots, conpot, llm or nsm
    container: str          # its container_name
    ports: List[Port] = field(default_factory=list)
    running: bool = False
    external: bool = False  # in EXTERNAL or a passive group: no probe reaches it from this host
    logstash_type: str = ""
    log_path: str = ""      # the log file (a glob for honeypots / ddospot) in the data folder


def _int(value: Any) -> Optional[int]:
    text = str(value if value is not None else "").strip()
    return int(text) if text.isdigit() else None


def _port_entry(entry: Any) -> Optional[dict]:
    """A port of the compose file as docker compose config writes it (a mapping); the short
    form "[ip:]published:target[/protocol]" of a compose file is read the same way."""
    if isinstance(entry, dict):
        return entry
    if not isinstance(entry, (int, str)):
        return None
    text, _, protocol = str(entry).strip().partition("/")
    ip = ""
    if text.startswith("["):
        ip, _, text = text[1:].partition("]")
        text = text.lstrip(":")
    parts = text.split(":")
    if len(parts) == 3 and not ip:
        ip = parts.pop(0)
    if len(parts) == 1:
        parts = ["", parts[0]]
    if len(parts) != 2:
        return None
    return {"host_ip": ip, "published": parts[0], "target": parts[1], "protocol": protocol or "tcp"}


def published_ports(service: dict) -> List[Port]:
    """The ports of a service a probe from outside can reach, as hptest.sh fuPORTS takes them."""
    ports: List[Port] = []
    for raw in service.get("ports") or []:
        entry = _port_entry(raw)
        if not entry:
            continue
        host_ip = str(entry.get("host_ip") or "")
        if host_ip in LOCAL_ADDRESSES:
            continue
        target = _int(entry.get("target"))
        published = _int(entry.get("published") or entry.get("target"))
        # a range of ports, or a port docker picks itself, cannot be probed by number
        if target is None or published is None or published in OWN_PORTS:
            continue
        protocol = "udp" if str(entry.get("protocol") or "tcp").lower() == "udp" else "tcp"
        port = Port(published, target, protocol, "" if host_ip in ("0.0.0.0", "::") else host_ip)
        if port not in ports:
            ports.append(port)
    return ports


def compose_config(path: str, run: Callable = subprocess.run) -> dict:
    """`docker compose config --format json` of the compose file in use."""
    try:
        proc = run(["docker", "compose", "-f", path, "config", "--format", "json"],
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                   cwd=os.path.dirname(path) or None)
    except OSError as exc:
        raise CheckError(f"Cannot run docker compose config: {exc}") from exc
    if proc.returncode != 0:
        text = (proc.stderr or "").strip().splitlines()
        raise CheckError(f"Cannot read the services of {path} (docker compose config)"
                         + (f": {text[-1]}" if text else "."))
    try:
        config = json.loads(proc.stdout)
    except ValueError as exc:
        raise CheckError(f"docker compose config gave no JSON for {path}: {exc}") from exc
    if not isinstance(config, dict):
        raise CheckError(f"docker compose config gave no services for {path}.")
    return config


def catalogue_groups(repo_dir: str) -> Dict[str, str]:
    """service name -> x-tpot group of compose/tpot_services.yml."""
    try:
        import yaml
    except ImportError as exc:  # the venv of the T-Pot Manager has it, the distro PyYAML too
        raise CheckError("PyYAML is missing, the catalogue of the services cannot be read.") from exc
    path = os.path.join(repo_dir, CATALOGUE)
    try:
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise CheckError(f"Cannot read the catalogue {path}: {exc}") from exc
    groups = {}
    for name, service in (data.get("services") or {}).items():
        meta = (service or {}).get("x-tpot") or {}
        groups[name] = str(meta.get("group") or "")
    return groups


def honeypot_names(groups: Dict[str, str]) -> List[str]:
    """The services of the catalogue a check covers: honeypots and the passive tools."""
    return sorted(name for name, group in groups.items() if group in HONEYPOT_GROUPS + PASSIVE_GROUPS)


def _running_names(found: Iterable[Any]) -> set:
    return {c.name for c in found if getattr(c, "state", "") == "running"}


def inventory(repo_dir: str, env: Optional[Dict[str, str]] = None, *,
              config: Optional[dict] = None, found: Optional[Iterable[Any]] = None,
              groups: Optional[Dict[str, str]] = None, run: Callable = subprocess.run) -> List["Honeypot"]:
    """The honeypots and passive tools of the compose file in use, sorted by service name.

    `config` (the compose JSON), `found` (ops.Container list) and `groups` (the catalogue) are
    read here when not given; tests hand them in. CheckError when one of them cannot be read."""
    from tpotctl import ops, system

    env = ops.env_values(repo_dir) if env is None else env
    if config is None:
        config = compose_config(ops.compose_path(repo_dir, env), run)
    if groups is None:
        groups = catalogue_groups(repo_dir)
    if found is None:
        try:
            found = ops.containers(run)
        except ops.OpsError as exc:
            raise CheckError(f"Cannot list the containers: {exc}") from exc
    running = _running_names(found)
    data = system.data_path(env, repo_dir)

    honeypots = []
    for name, service in sorted((config.get("services") or {}).items()):
        group = groups.get(name, "")
        if group not in HONEYPOT_GROUPS + PASSIVE_GROUPS:
            continue
        service = service or {}
        container = str(service.get("container_name") or name)
        logstash_type, log_rel = LOGS.get(name, ("", ""))
        honeypots.append(Honeypot(
            name=name,
            group=group,
            container=container,
            ports=published_ports(service),
            running=container in running,
            external=name in EXTERNAL or group in PASSIVE_GROUPS,
            logstash_type=logstash_type,
            log_path=os.path.join(data, log_rel) if log_rel else "",
        ))
    return honeypots
