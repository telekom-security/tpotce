"""Attacks on this HIVE from Elasticsearch: per minute of the last hour, 24 hours, top honeypots.

Events of the NSM tools (Suricata, P0f) are not attacks of their own, they describe
the same connections the honeypots see, so they are left out, as on the Attack Map.
"""

import json
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, List, Tuple

ES_URL = "http://127.0.0.1:64298"        # as in tpotctl.sensors
NOT_ATTACKS = ["Suricata", "P0f"]


@dataclass
class Attacks:
    per_minute: List[int] = field(default_factory=list)     # oldest first, 60 values
    last_day: int = 0
    top: List[Tuple[str, int]] = field(default_factory=list)
    problem: str = ""


def query() -> dict:
    honeypots = {"bool": {"must_not": {"terms": {"type.keyword": NOT_ATTACKS}}}}
    return {
        "size": 0,
        "track_total_hits": True,
        "query": {"bool": {"filter": [{"range": {"@timestamp": {"gte": "now-24h"}}}, honeypots]}},
        "aggs": {
            "hour": {
                "filter": {"range": {"@timestamp": {"gte": "now-60m/m"}}},
                "aggs": {"minutes": {"date_histogram": {
                    "field": "@timestamp", "fixed_interval": "1m", "min_doc_count": 0,
                    "extended_bounds": {"min": "now-59m/m", "max": "now/m"}}}},
            },
            "top": {"terms": {"field": "type.keyword", "size": 5}},
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
    return Attacks(minutes, int(total), top)


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
    honeypots = {"bool": {"must_not": {"terms": {"type.keyword": NOT_ATTACKS}}}}
    return {
        "size": 0,
        "query": {"bool": {"filter": [{"range": {"@timestamp": {"gte": f"now-{int(hours)}h"}}}, honeypots]}},
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
