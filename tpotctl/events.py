"""Honeypot event documents indexed on this HIVE, including its sensors.

Only known honeypot log types count, independently of the locally running services.
NSM observations, infrastructure logs and reflection victims are not honeypot events.
Document counts are not unique attacks; source cardinality is an estimate.
"""

import json
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from tpotctl.checks.inventory import LOGS

ES_URL = "http://127.0.0.1:64298"        # as in tpotctl.sensors
HONEYPOT_TYPES = tuple(sorted({kind for service, (kind, _path) in LOGS.items()
                              if service not in ("p0f", "suricata")}))


def honeypot_filter() -> dict:
    """Positive classification shared by the Manager, attackers command and landing page."""
    return {"terms": {"type.keyword": list(HONEYPOT_TYPES)}}


@dataclass
class Attacks:
    per_minute: List[int] = field(default_factory=list)     # oldest first, 60 values
    last_day: int = 0
    top: List[Tuple[str, int]] = field(default_factory=list)
    problem: str = ""
    latest: Optional[float] = None      # epoch seconds of the newest attack of 24 hours
    sources: int = 0                    # distinct source addresses of the last hour
    last_hour: int = 0                  # exact rolling-hour count, not the sum of rounded buckets


def query() -> dict:
    return {
        "size": 0,
        "track_total_hits": True,
        "query": {"bool": {"filter": [{"range": {"@timestamp": {"gte": "now-24h", "lt": "now"}}},
                                     honeypot_filter()]}},
        "aggs": {
            "hour": {
                "filter": {"range": {"@timestamp": {"gte": "now-1h", "lt": "now"}}},
                "aggs": {"minutes": {"date_histogram": {
                    "field": "@timestamp", "fixed_interval": "1m", "min_doc_count": 0,
                    "extended_bounds": {"min": "now-59m/m", "max": "now/m"}}},
                    "sources": {"cardinality": {"field": "src_ip.keyword"}}},
            },
            "top": {"terms": {"field": "type.keyword", "size": 5}},
            "latest": {"max": {"field": "@timestamp"}},
        },
    }


def parse(answer: dict) -> Attacks:
    total = (answer.get("hits") or {}).get("total") or 0
    if isinstance(total, dict):
        total = total.get("value", 0)
    aggs = answer.get("aggregations") or {}
    buckets = ((aggs.get("hour") or {}).get("minutes") or {}).get("buckets", [])
    minutes = [int(b.get("doc_count", 0)) for b in buckets][-60:]
    top = [(b["key"], int(b.get("doc_count", 0))) for b in (aggs.get("top") or {}).get("buckets", [])]
    latest = (aggs.get("latest") or {}).get("value")             # epoch milliseconds, None without hits
    sources = ((aggs.get("hour") or {}).get("sources") or {}).get("value") or 0
    return Attacks(minutes, int(total), top, latest=latest / 1000 if latest else None, sources=int(sources),
                   last_hour=int((aggs.get("hour") or {}).get("doc_count", 0)))


def fetch(url: str = ES_URL, opener: Callable = urllib.request.urlopen) -> Attacks:
    request = urllib.request.Request(f"{url}/logstash-*/_search", data=json.dumps(query()).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
    try:
        with opener(request, timeout=3) as response:
            answer = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError) as err:
        return Attacks(problem=f"Elasticsearch is not reachable ({getattr(err, 'reason', err)})")
    return parse(answer)


@dataclass
class Source:
    ip: str
    count: int
    country: str = ""
    reputation: str = ""        # ip_rep of Logstash, i.e. known attacker, mass scanner


@dataclass
class Sources:
    sources: List[Source] = field(default_factory=list)
    problem: str = ""


def sources_query(hours: int, size: int) -> dict:
    return {
        "size": 0,
        "query": {"bool": {"filter": [{"range": {"@timestamp": {"gte": f"now-{int(hours)}h", "lt": "now"}}},
                                     honeypot_filter()]}},
        "aggs": {"sources": {"terms": {"field": "src_ip.keyword", "size": int(size)}, "aggs": {
            "country": {"terms": {"field": "geoip.country_name.keyword", "size": 1}},
            "rep": {"terms": {"field": "ip_rep.keyword", "size": 1}}}}},
    }


def top_sources(hours: int = 24, size: int = 10, url: str = ES_URL,
                opener: Callable = urllib.request.urlopen) -> Sources:
    """The source IPs with the most attacks (was mytopips.sh), with country and reputation."""
    request = urllib.request.Request(f"{url}/logstash-*/_search", data=json.dumps(sources_query(hours, size)).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
    try:
        with opener(request, timeout=5) as response:
            answer = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError) as err:
        return Sources(problem=f"Elasticsearch is not reachable ({getattr(err, 'reason', err)})")

    def first(bucket: dict, name: str) -> str:
        found = (bucket.get(name) or {}).get("buckets") or []
        return str(found[0]["key"]) if found else ""

    buckets = ((answer.get("aggregations") or {}).get("sources") or {}).get("buckets") or []
    return Sources([Source(str(b["key"]), int(b.get("doc_count", 0)), first(b, "country"), first(b, "rep"))
                    for b in buckets])


def sparkline(values: List[int], chars: str, rows: int = 1) -> List[str]:
    """Bars scaled to the highest value, `rows` text rows high (top row first)."""
    if not values:
        return [""] * rows
    top = max(values) or 1
    steps = len(chars) * rows
    out = []
    for row in range(rows - 1, -1, -1):
        line = ""
        for value in values:
            level = 0 if value == 0 else max(1, round(value / top * steps))
            part = level - row * len(chars)
            line += " " if part <= 0 else chars[min(part, len(chars)) - 1]
        out.append(line)
    return out
