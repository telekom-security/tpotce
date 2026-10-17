"""The stages of a check per honeypot, and how each one is judged.

    1 port           the probe reached the published ports (probes.ProbeResult.state)
    2 answer         the honeypot answered as it should ("–" where only the port was checked)
    3 log            the probe's line in the honeypot's log: by the token where the probe sent it,
                     else by an own address and a source port of the probe (where the source ports
                     survive: a honeypot that logs only the connect or encodes the payload), by the
                     time window for dicompot (its DICOM client leaves no source port), by an own
                     address for conpot_ipmi (it logs the source port of an address's first contact)
    4 elasticsearch  the probe's event in logstash-* (127.0.0.1:64298)
    5 geoip          the injected geo event (a fixture with a fixed test IP): geoip.latitude, no
                     _geoip_lookup_failure; found by its test IP and marker port (or the token), by
                     its test IP and type in the window where the honeypot logs no source port
    6 attack_map     the geo event on Redis (attack-map-production) and on the WebSocket of map_web
                     (n/a for a honeypot that logs no destination port: map_data shows none)
    7 ewsposter      ewsposter confirmed the hold (hold.ack) during the run

Each stage is ✓ ok, ✗ failed, – not applicable or · not reached (a stage before it failed or the
check stopped). A SENSOR runs stages 1 to 3 only, the others say "check on the HIVE".

Everything here is pure: the runner (tpotctl.checks.run) hands in what it saw.
"""

import json
import re
import urllib.parse
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from tpotctl.checks.inventory import EXTERNAL_HINT, Honeypot
from tpotctl.checks.probes import CLOSED, NOT_REACHED as PROBE_NOT_REACHED, OPEN, ProbeResult

OK, FAILED, NOT_APPLICABLE, NOT_REACHED = "ok", "failed", "n/a", "not_reached"
STATES = (OK, FAILED, NOT_APPLICABLE, NOT_REACHED)
GLYPHS = {OK: "✓", FAILED: "✗", NOT_APPLICABLE: "–", NOT_REACHED: "·"}
ASCII_GLYPHS = {OK: "+", FAILED: "x", NOT_APPLICABLE: "-", NOT_REACHED: "."}

STAGES = ("port", "answer", "log", "elasticsearch", "geoip", "attack_map", "ewsposter")
TITLES = {"port": "Port", "answer": "Answer", "log": "Log line", "elasticsearch": "Elasticsearch",
          "geoip": "GeoIP", "attack_map": "Attack Map", "ewsposter": "ewsposter held"}
# the column titles of the table in a terminal (80 columns: the honeypot, seven glyphs and a note)
SHORT_TITLES = {"port": "Port", "answer": "Answer", "log": "Log", "elasticsearch": "ES", "geoip": "Geo",
                "attack_map": "Map", "ewsposter": "Held"}
SENSOR_STAGES = STAGES[:3]
ON_THE_HIVE = "check on the HIVE"

# the fixed test source IPs of T-Pot (attackmap_pipeline_test.sh myFALLBACKIPS): IPv4 only
TEST_IPS = ("8.8.8.8", "208.67.222.222", "8.8.4.4", "4.2.2.2")
# the source port of the injected geo event of the n-th honeypot is MARKER_PORT + n
MARKER_PORT = 51234
# honeypots whose log is matched by the time window: their probe leaves no source port
TIME_WINDOW = ("dicompot",)
# honeypots whose log is matched by an own address alone: conpot's IPMI keeps one attack session
# per source address (ipmi_server.py attack_sessions) until it closes, so a later probe from this
# host is logged with the source port of the first one; the port of the line found then counts as
# one of the probe's (the cleanup takes it)
ADDRESS_ONLY = ("conpot_ipmi",)
# map_data (t-pot-attack-map DataServer_v2.py) polls every 0.5 s and takes the events up to 10 s
# old; Elasticsearch shows an event 5 s after it is indexed (refresh_interval): the seconds an event
# has, from the moment the check finds it in Elasticsearch, to come on Redis and the WebSocket
MAP_WAIT = 30.0
MAP_SKIPPED = "not shown by the Attack Map (map_data skips events indexed after newer ones)"
MAP_SILENT = "nothing came on Redis ({channel}) during the check: does map_data run? (docker logs map_data)"
# the types the Attack Map leaves out (events.NOT_ATTACKS)
NOT_ON_THE_MAP = ("Suricata", "P0f", "DdospotTarget")
# honeypots that never log a destination port (dicompot: only the source in "Connection from"):
# map_data drops an event without dest_port, so the Attack Map never shows them
NO_DEST_PORT = ("dicompot",)
REDIS_CHANNEL = "attack-map-production"


@dataclass
class Stage:
    state: str = NOT_REACHED
    detail: str = ""

    def __post_init__(self) -> None:
        if self.state not in STATES:
            raise ValueError(f"stage state {self.state!r} is none of {', '.join(STATES)}")


@dataclass
class GeoEvent:
    """The injected geo event of a honeypot."""
    src_ip: str
    src_port: int
    file: str = ""          # the log it went to, relative to the data folder
    # the honeypot logs a source port, so the event carries the marker port; without one it is
    # found by its test IP and type in the window (the test IPs are the check's own)
    marker: bool = True
    line: str = ""          # the line as it was appended ("" when nothing was written)
    es_id: str = ""
    es_index: str = ""
    location: Dict[str, Any] = field(default_factory=dict)     # lat, lon, country, ip_rep


@dataclass
class Row:
    """One honeypot of a check: its stages and what each one found."""
    name: str
    group: str = ""
    container: str = ""
    running: bool = False
    external: bool = False
    logstash_type: str = ""
    ports: List[str] = field(default_factory=list)
    stages: Dict[str, Stage] = field(default_factory=lambda: {name: Stage() for name in STAGES})
    log_line: str = ""
    es_id: str = ""
    es_index: str = ""
    geo: Optional[GeoEvent] = None

    def set(self, stage: str, state: str, detail: str = "") -> None:
        self.stages[stage] = Stage(state, detail)

    def state(self, stage: str) -> str:
        return self.stages[stage].state

    @property
    def failed(self) -> bool:
        return any(stage.state == FAILED for stage in self.stages.values())

    def as_json(self) -> Dict[str, Any]:
        data = asdict(self)
        data["stages"] = {name: asdict(self.stages[name]) for name in STAGES}
        return data


def row_for(honeypot: Honeypot) -> Row:
    return Row(honeypot.name, honeypot.group, honeypot.container, honeypot.running, honeypot.external,
               honeypot.logstash_type, [str(port) for port in honeypot.ports])


def glyph(state: str, ascii_only: bool = False) -> str:
    return (ASCII_GLYPHS if ascii_only else GLYPHS)[state]


def iso(moment: datetime) -> str:
    """A time for Elasticsearch and Kibana: UTC, milliseconds, Z."""
    moment = moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (moment.microsecond // 1000)


# -- stages 1 and 2: the probe ------------------------------------------------------------------

def port_stage(honeypot: Honeypot, result: Optional[ProbeResult]) -> Stage:
    if honeypot.external:
        return Stage(NOT_APPLICABLE, EXTERNAL_HINT)
    if not honeypot.running:
        return Stage(FAILED, f"not running (container {honeypot.container})")
    if result is None:
        return Stage(NOT_APPLICABLE, "not probed")
    if result.state == OPEN:
        return Stage(OK, result.detail)
    if result.state == CLOSED:
        return Stage(FAILED, result.detail)
    if result.state == PROBE_NOT_REACHED:
        return Stage(NOT_REACHED, result.detail)
    if result.detail.startswith("probe failed"):
        return Stage(FAILED, result.detail)
    return Stage(NOT_APPLICABLE, result.detail)


def answer_stage(result: Optional[ProbeResult], port: Stage) -> Stage:
    if port.state != OK:
        return Stage(NOT_APPLICABLE if port.state == NOT_APPLICABLE else NOT_REACHED)
    if not result.answer_checked:
        return Stage(NOT_APPLICABLE, "only the port was checked")
    if result.answered:
        return Stage(OK)
    return Stage(FAILED, result.detail)


def expects_trace(result: ProbeResult) -> bool:
    """Whether the probe must leave a log line: it sent the token, or a checked answer came. A
    knock at a port (LLM honeypots, port-only ports) may leave none."""
    return result.token_sent or (result.answer_checked and result.answered)


# -- stage 3: the log line ----------------------------------------------------------------------

def _has_number(line: str, number: int) -> bool:
    return re.search(rf"(?<![0-9]){number}(?![0-9])", line) is not None


def _has_address(line: str, addresses: Iterable[str]) -> bool:
    """An address as a whole: 192.0.2.5 is not in 192.0.2.55; an IPv4 one also counts as
    ::ffff:192.0.2.5 (dionaea)."""
    for address in addresses:
        if ":" in address:
            pattern = rf"(?<![0-9A-Fa-f:]){re.escape(address)}(?![0-9A-Fa-f:])"
        else:
            pattern = rf"(?<![0-9.]){re.escape(address)}(?![0-9]|\.[0-9])"
        if re.search(pattern, line, re.IGNORECASE):
            return True
    return False


def match_line(name: str, result: ProbeResult, lines: Iterable[str], token: str,
               addresses: Iterable[str], skip: Iterable[str] = (), ports_survive: bool = True) -> str:
    """The first new log line of the probe: with the token where the probe sent it, else (where the
    source ports reach the honeypot, `ports_survive`) a line with an own address and a source port
    of the probe: a honeypot may log only the connect (adbhoney) or the payload encoded (medpot).
    For dicompot any new line (the time window), for conpot_ipmi a line with an own address. Lines
    in `skip` (the injected geo events) never count."""
    skip = set(skip)
    addresses = list(addresses)
    lines = [line for line in lines if line.strip() and line not in skip]
    if result.token_sent:
        for line in lines:
            if token in line:
                return line
        if not ports_survive:
            return ""
    elif name in TIME_WINDOW:
        return lines[0] if lines else ""
    for line in lines:
        if _has_address(line, addresses) and any(_has_number(line, port) for port in result.src_ports):
            return line
    if name in ADDRESS_ONLY:
        for line in lines:
            if _has_address(line, addresses):
                return line
    return ""


def line_port(line: str) -> Optional[int]:
    """The source port a JSON log line names (src_port), None for none."""
    try:
        value = json.loads(line).get("src_port")
    except (ValueError, AttributeError):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int) or (isinstance(value, str) and value.isdigit()):
        port = int(value)
        return port if 0 < port <= 65535 else None
    return None


def ignored_text(name: str, entry: str) -> str:
    """Why a probe leaves no log line: the honeypot does not log sources in `entry`."""
    return f"{name} does not log sources in {entry} (its configuration)"


# -- stages 4 and 5: Elasticsearch --------------------------------------------------------------

def _window(start: datetime, end: Optional[datetime]) -> Dict[str, Any]:
    window: Dict[str, Any] = {"gte": iso(start)}
    if end is not None:
        window["lte"] = iso(end)
    return {"range": {"@timestamp": window}}


def token_clause(token: str) -> Dict[str, Any]:
    """The token in any text field (the probes put it into user names, paths, user agents ...)."""
    return {"query_string": {"query": f'"{token}"', "lenient": True}}


SOURCE_FIELDS = ["@timestamp", "type", "src_ip", "src_port", "dest_port", "tags", "t-pot_hostname", "ip_rep",
                 "geoip.latitude", "geoip.longitude", "geoip.country_name", "geoip_ext.latitude"]


def probe_query(honeypot: Honeypot, result: ProbeResult, token: str, addresses: Iterable[str],
                start: datetime, end: Optional[datetime] = None, with_token: bool = True) -> Dict[str, Any]:
    """Stage 4: an event of the honeypot's type in the window, from an own address with a source
    port of the probe, or with the token; never one of the injected geo events (test IPs)."""
    addresses = sorted(set(addresses))
    should: List[Dict[str, Any]] = []
    if result.token_sent and with_token:
        should.append(token_clause(token))
    if honeypot.name in TIME_WINDOW or not result.src_ports:
        should.append({"terms": {"src_ip.keyword": addresses}})
    else:
        should.append({"bool": {"filter": [{"terms": {"src_ip.keyword": addresses}},
                                           {"terms": {"src_port": sorted(set(result.src_ports))}}]}})
    return {
        "size": 1, "sort": [{"@timestamp": "asc"}], "_source": SOURCE_FIELDS,
        "query": {"bool": {
            "filter": [{"term": {"type.keyword": honeypot.logstash_type}}, _window(start, end)],
            "must_not": [{"terms": {"src_ip.keyword": list(TEST_IPS)}}],
            "should": should, "minimum_should_match": 1}},
    }


def geo_query(logstash_type: str, event: GeoEvent, token: str, start: datetime,
              end: Optional[datetime] = None, with_token: bool = True) -> Dict[str, Any]:
    """Stage 5: the injected event, by its test IP and its marker port (or the token); by its test
    IP and type in the window where the honeypot logs no source port."""
    query: Dict[str, Any] = {"filter": [{"term": {"type.keyword": logstash_type}},
                                        {"term": {"src_ip.keyword": event.src_ip}}, _window(start, end)]}
    if event.marker:
        should: List[Dict[str, Any]] = [{"term": {"src_port": event.src_port}}]
        if with_token:
            should.append(token_clause(token))
        query.update(should=should, minimum_should_match=1)
    return {"size": 1, "_source": SOURCE_FIELDS, "query": {"bool": query}}


def first_hit(answer: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    hits = ((answer or {}).get("hits") or {}).get("hits") or []
    return hits[0] if hits else None


def _field(source: Dict[str, Any], path: str) -> Any:
    if path in source:
        return source[path]
    value: Any = source
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def geo_stage(hit: Optional[Dict[str, Any]], waited: float) -> Stage:
    if hit is None:
        return Stage(FAILED, f"the geo event did not reach Elasticsearch within {waited:g} s")
    source = hit.get("_source") or {}
    tags = source.get("tags") or []
    tags = [tags] if isinstance(tags, str) else tags
    if "_geoip_lookup_failure" in tags:
        return Stage(FAILED, "GeoIP lookup failed (_geoip_lookup_failure)")
    latitude, longitude = _field(source, "geoip.latitude"), _field(source, "geoip.longitude")
    if latitude is None:
        return Stage(FAILED, "no geoip.latitude in the event")
    text = f"{latitude}, {longitude}"
    country = _field(source, "geoip.country_name")
    if country:
        text += f" ({country})"
    if source.get("ip_rep"):
        text += f", ip_rep {source['ip_rep']}"
    return Stage(OK, text)


def location(hit: Dict[str, Any]) -> Dict[str, Any]:
    source = hit.get("_source") or {}
    return {key: value for key, value in (
        ("lat", _field(source, "geoip.latitude")), ("lon", _field(source, "geoip.longitude")),
        ("country", _field(source, "geoip.country_name")), ("ip_rep", source.get("ip_rep"))) if value is not None}


# -- stage 6: the Attack Map --------------------------------------------------------------------

def on_the_map(honeypot: Honeypot) -> bool:
    return honeypot.group != "nsm" and honeypot.logstash_type not in NOT_ON_THE_MAP


def no_dest_port_text(name: str) -> str:
    return f"the Attack Map shows only events with a destination port; {name} logs none"


def seen(lines: Iterable[str], src_ip: str, src_port: Optional[int], honeypot: str = "") -> bool:
    """An event of src_ip and src_port in the output of redis-cli SUBSCRIBE or the WebSocket; with
    no src_port (a honeypot that logs none: map_data says 0) one of src_ip and the type `honeypot`."""
    ip = re.compile(rf'"src_ip":\s*"{re.escape(src_ip)}"')
    if src_port is None:
        kind = re.compile(rf'"honeypot":\s*"{re.escape(honeypot)}"')
        return any(ip.search(line) and kind.search(line) for line in lines)
    port = re.compile(rf'"src_port":\s*"?{src_port}(?![0-9])')
    return any(ip.search(line) and port.search(line) for line in lines)


# -- the cleanup and the link -------------------------------------------------------------------

CLEANUP_SLACK = timedelta(minutes=1)


def cleanup_window(start: datetime, end: datetime) -> Dict[str, str]:
    """From the start of the run (whole seconds: some honeypots log no fractions) to its end + 1 min."""
    return {"gte": iso(start.replace(microsecond=0)), "lte": iso(end + CLEANUP_SLACK)}


# never deleted: the passive tools see the host's own traffic (DNS, pulls) and get no test event
NEVER_DELETED = ("Suricata", "P0f")


@dataclass
class OwnEvents:
    """What a run sent, the only events its cleanup removes: (a) the token, (b) a test IP with the
    marker port of its injection (with its type alone where the honeypot logs no source port), (c)
    the probe source with a source port the probe recorded, each within the types the run probed or
    injected."""
    token: str
    source: str = ""                                            # the probe source, "" when ports do not survive
    probes: Dict[str, List[int]] = field(default_factory=dict)  # type -> source ports of its probes
    probed: List[str] = field(default_factory=list)             # every type a probe went to
    token_types: List[str] = field(default_factory=list)        # types whose events may carry the token
    # (type, test IP, marker port), the port None where the honeypot logs none (test IP and type then)
    geos: List[tuple] = field(default_factory=list)

    @property
    def types(self) -> List[str]:
        found = set(self.probed) | {geo[0] for geo in self.geos}
        return sorted(found - set(NEVER_DELETED))


def cleanup_query(start: datetime, end: datetime, own: OwnEvents,
                  with_token: bool = True) -> Optional[Dict[str, Any]]:
    """The body of _delete_by_query on logstash-*, None when the run sent nothing it can name. Every
    condition is within the run's window and its types; never by an own address alone."""
    types = own.types
    if not types:
        return None

    def kind(name: str) -> Dict[str, Any]:
        return {"term": {"type.keyword": name}}

    should: List[Dict[str, Any]] = []
    token_types = sorted(set(own.token_types) & set(types))
    if with_token and token_types:
        should.append({"bool": {"filter": [{"terms": {"type.keyword": token_types}}, token_clause(own.token)]}})
    for name, ip, port in own.geos:
        if name in types:
            # a test IP is never a real source: without a marker port its type and the window suffice
            match = [kind(name), {"term": {"src_ip.keyword": ip}}]
            if port is not None:
                match.append({"term": {"src_port": port}})
            should.append({"bool": {"filter": match}})
    if own.source:
        for name, ports in sorted(own.probes.items()):
            if name in types and ports:
                should.append({"bool": {"filter": [kind(name), {"term": {"src_ip.keyword": own.source}},
                                                   {"terms": {"src_port": sorted(set(ports))}}]}})
    if not should:
        return None
    return {"query": {"bool": {"filter": [{"range": {"@timestamp": cleanup_window(start, end)}},
                                          {"terms": {"type.keyword": types}}],
                               "should": should, "minimum_should_match": 1}}}


def _probed(own: OwnEvents) -> List[str]:
    return sorted(set(own.probed) - set(NEVER_DELETED))


def leftover_query(start: datetime, end: datetime, own: OwnEvents, host: str) -> Optional[Dict[str, Any]]:
    """The probe events the cleanup could not tell apart from real ones: the probed types, from the
    probe address, in the window (counted after the cleanup)."""
    if not _probed(own) or not host:
        return None
    return {"query": {"bool": {"filter": [{"range": {"@timestamp": cleanup_window(start, end)}},
                                          {"terms": {"type.keyword": _probed(own)}},
                                          {"term": {"src_ip.keyword": host}}]}}}


def sourceless_query(start: datetime, end: datetime, own: OwnEvents) -> Optional[Dict[str, Any]]:
    """The events of the probed types in the window without a source address (dicompot's
    "Connection Finished"): they may be the probes' or real ones, so they are counted, never
    deleted."""
    if not _probed(own):
        return None
    return {"query": {"bool": {"filter": [{"range": {"@timestamp": cleanup_window(start, end)}},
                                          {"terms": {"type.keyword": _probed(own)}}],
                               "must_not": [{"exists": {"field": "src_ip"}}]}}}


def leftover_kql(own: OwnEvents, host: str) -> str:
    return (f'(src_ip:"{host}" or not src_ip:*) and type:('
            + " or ".join(f'"{name}"' for name in _probed(own)) + ")")


def run_kql(token: str, sources: Iterable[str]) -> str:
    """The run in Kibana: its token or its sources."""
    sources = sorted(set(sources))
    query = f'"{token}"'
    if sources:
        query += " or src_ip:(" + " or ".join(f'"{source}"' for source in sources) + ")"
    return query


def _rison(text: str) -> str:
    return "'" + text.replace("!", "!!").replace("'", "!'") + "'"


def kibana_link(host: str, port: int, query: str, start: datetime, end: datetime) -> str:
    """Discover in Kibana with a KQL query, in the run's window."""
    window = cleanup_window(start, end)
    state = (f"_g=(time:(from:{_rison(window['gte'])},to:{_rison(window['lte'])}))"
             f"&_a=(query:(language:kuery,query:{_rison(query)}))")
    address = f"[{host}]" if ":" in host else host
    return f"https://{address}:{port}/kibana/app/discover#/?" + urllib.parse.quote(state, safe="()':,!&=")
