"""The Elasticsearch queries of the landing page (docker/nginx/dist/html), per time range.

The page asks Elasticsearch itself through /es/ and reads them from assets/queries.json,
which this module writes; the tests run --check:

  python3 -m tpotctl.landing           write queries.json
  python3 -m tpotctl.landing --check   exit 1 when queries.json is not the generated one

Events of the NSM tools are left out as in tpotctl.events, the sources are its query for any range.
"""

import json
import os
import sys
from typing import List, Optional

from tpotctl.events import NOT_ATTACKS

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUERIES = os.path.join(REPO, "docker", "nginx", "dist", "html", "assets", "queries.json")

# key: the name the page uses; span / before: Elasticsearch date math of the range and of the range
# before it; interval: one point of the graphs; points: how many of them; every / lists: seconds
# between two questions for the overview and for the lists
RANGES = [
    {"key": "24h", "span": "24h", "before": "48h", "interval": "30m", "points": 48, "every": 15, "lists": 60,
     "name": "the last 24 hours", "short": "24 h", "per": "30 min"},
    {"key": "1h", "span": "1h", "before": "2h", "interval": "1m", "points": 60, "every": 10, "lists": 30,
     "name": "the last hour", "short": "1 h", "per": "minute"},
    {"key": "1m", "span": "60s", "before": "120s", "interval": "2s", "points": 30, "every": 4, "lists": 10,
     "name": "the last minute", "short": "1 min", "per": "2 s"},
]


def honeypots() -> dict:
    return {"bool": {"must_not": {"terms": {"type.keyword": NOT_ATTACKS}}}}


def histogram(r: dict, before: bool = False) -> dict:
    """The points of the range, or with before of the range before it (the same width, one span earlier)."""
    bounds = {"min": f"now-{r['before']}", "max": f"now-{r['span']}"} if before else {"min": f"now-{r['span']}", "max": "now"}
    return {"date_histogram": {"field": "@timestamp", "fixed_interval": r["interval"], "min_doc_count": 0,
                               "extended_bounds": bounds}}


def overview(r: dict) -> dict:
    """The attacks of the range per point, the seven busiest honeypots each with its own points, the
    counts of sources and honeypots, the newest attack and the attacks of the range before, per point too."""
    return {
        "size": 0,
        "query": {"bool": {"filter": [{"range": {"@timestamp": {"gte": f"now-{r['before']}"}}}, honeypots()]}},
        "aggs": {
            "current": {
                "filter": {"range": {"@timestamp": {"gte": f"now-{r['span']}"}}},
                "aggs": {
                    "slots": histogram(r),
                    "types": {"terms": {"field": "type.keyword", "size": 7}, "aggs": {"slots": histogram(r)}},
                    "sources": {"cardinality": {"field": "src_ip.keyword"}},
                    "honeypots": {"cardinality": {"field": "type.keyword"}},
                },
            },
            "previous": {"filter": {"range": {"@timestamp": {"gte": f"now-{r['before']}", "lt": f"now-{r['span']}"}}},
                         "aggs": {"slots": histogram(r, before=True)}},
            "latest": {"max": {"field": "@timestamp"}},
        },
    }


def sources(r: dict, size: int = 12) -> dict:
    """The source addresses with the most attacks, as tpotctl.events.sources_query; the page shows the
    first five (seven without sensors) whose values pass its checks."""
    return {
        "size": 0,
        "query": {"bool": {"filter": [{"range": {"@timestamp": {"gte": f"now-{r['span']}"}}}, honeypots()]}},
        "aggs": {"sources": {"terms": {"field": "src_ip.keyword", "size": int(size)}, "aggs": {
            "country": {"terms": {"field": "geoip.country_name.keyword", "size": 1}},
            "rep": {"terms": {"field": "ip_rep.keyword", "size": 1}}}}},
    }


def sensors(r: dict) -> dict:
    """The sensors of a HIVE that sent events in the range, without addresses."""
    return {
        "size": 0,
        "query": {"bool": {"filter": [{"range": {"@timestamp": {"gte": f"now-{r['span']}"}}},
                                      {"exists": {"field": "t-pot_sensor"}}]}},
        "aggs": {"sensors": {"terms": {"field": "t-pot_sensor.keyword", "size": 50},
                             "aggs": {"latest": {"max": {"field": "@timestamp"}}}}},
    }


def generate() -> str:
    ranges = {}
    for r in RANGES:
        meta = {k: r[k] for k in ("points", "every", "lists", "name", "short", "per")}
        ranges[r["key"]] = dict(meta, overview=overview(r), sources=sources(r), sensors=sensors(r))
    return json.dumps({"default": RANGES[0]["key"], "ranges": ranges}, indent=2) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    text = generate()
    if argv == ["--check"]:
        try:
            with open(QUERIES, encoding="utf-8") as f:
                same = f.read() == text
        except OSError:
            same = False
        if not same:
            print(f"{os.path.relpath(QUERIES, REPO)} is not the generated one, run python3 -m tpotctl.landing",
                  file=sys.stderr)
        return 0 if same else 1
    if argv:
        print("usage: python3 -m tpotctl.landing [--check]", file=sys.stderr)
        return 2
    with open(QUERIES, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"wrote {os.path.relpath(QUERIES, REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
