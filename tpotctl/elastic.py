"""The retention of the Elasticsearch events: the ILM policy tpot and TPOT_ES_RETENTION.

T-Pot marks the policy it writes with _meta.tpot_retention. A policy whose marker no
longer matches its delete phase was changed in Kibana: it is "foreign" and T-Pot leaves
it alone, so that nobody loses events they chose to keep. tpotinit applies the same
rule on every start (docker/tpotinit/dist/bin/es_retention.sh), both pass the cases in
docker/tpotinit/tests/ilm_cases.yml. Standard library only: update.sh runs
`python3 -m tpotctl.elastic value-of FILE` with the python3 of the system.
"""

import copy
import json
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, List, Optional

POLICY = "tpot"
ES_URL = "http://127.0.0.1:64298"        # as in tpotctl.events
DEFAULT = "30"
MAX_DAYS = 3650
LEGACY_DESCRIPTION = "T-Pot ILM policy with a retention of 30 days"
DESCRIPTION = "T-Pot ILM policy, its retention is TPOT_ES_RETENTION in .env"
DELETE_ACTIONS = {"delete": {"delete_searchable_snapshot": True}}


@dataclass
class Decision:
    action: str                 # create | update | keep | foreign
    policy: Optional[dict]      # what to PUT (create / update), None otherwise
    current: str                # value_of the policy as it is ("" none or not derivable)
    managed: bool               # T-Pot wrote it last (or it is the policy of an earlier release)


def _min_age(policy: dict) -> Optional[str]:
    delete = (policy.get("phases") or {}).get("delete")
    return None if delete is None else str(delete.get("min_age", ""))


def value_of(policy: Optional[dict]) -> str:
    """The TPOT_ES_RETENTION a policy stands for: days, "off" without a delete phase, "" if none."""
    if not isinstance(policy, dict) or not isinstance(policy.get("phases"), dict):
        return ""
    min_age = _min_age(policy)
    if min_age is None:
        return "off"
    match = re.fullmatch(r"([0-9]+)([dh])", min_age)
    if not match:
        return ""
    number, unit = int(match.group(1)), match.group(2)
    if unit == "h":
        if number % 24:
            return ""
        number //= 24
    return str(number) if 1 <= number <= MAX_DAYS else ""


def is_managed(policy: dict) -> bool:
    meta = policy.get("_meta") or {}
    if "tpot_retention" in meta:
        mark = str(meta["tpot_retention"])
        return _min_age(policy) is None if mark == "off" else _min_age(policy) == f"{mark}d"
    return (meta.get("description") == LEGACY_DESCRIPTION
            and sorted((policy.get("phases") or {})) == ["delete", "hot"]
            and _min_age(policy) == "30d")


def build(value: str, base: Optional[dict]) -> dict:
    """base with the delete phase of value and the marker; every other part of base stays."""
    policy = copy.deepcopy(base) if base else {"phases": {"hot": {"min_age": "0ms", "actions": {}}}}
    phases = policy.setdefault("phases", {})
    if value == "off":
        phases.pop("delete", None)
    else:
        delete = phases.get("delete") or {"actions": copy.deepcopy(DELETE_ACTIONS)}
        delete["min_age"] = f"{value}d"
        phases["delete"] = delete
    meta = dict(policy.get("_meta") or {})
    meta.update({"managed": True, "tpot_retention": value, "description": DESCRIPTION})
    policy["_meta"] = meta
    return policy


def decide(value: str, policy: Optional[dict], force: bool = False) -> Decision:
    if policy is None:
        wanted = value or DEFAULT
        return Decision("create", build(wanted, None), "", True)
    current, managed = value_of(policy), is_managed(policy)
    if not value:
        return Decision("keep", None, current, managed)
    if not (managed or force):
        return Decision("foreign", None, current, False)
    if managed and current == value and str((policy.get("_meta") or {}).get("tpot_retention", "")) == value:
        return Decision("keep", None, current, True)
    return Decision("update", build(value, policy), current, managed)


def _days(value: str) -> float:
    return float("inf") if value in ("", "off") else float(value)


def shortens(old: str, new: str) -> bool:
    """new deletes events old keeps ("" and "off" keep them all; an empty new changes nothing)."""
    return bool(new) and _days(new) < _days(old)


class ElasticError(Exception):
    """Elasticsearch did not do what was asked."""


def _call(method: str, path: str, url: str, opener: Callable, body: Optional[dict] = None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{url}{path}", data=data, method=method,
                                     headers={"Content-Type": "application/json"})
    with opener(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8") or "null")


def _unreachable(err) -> str:
    return f"Elasticsearch is not reachable ({getattr(err, 'reason', err)})"


def get_policy(url: str = ES_URL, opener: Callable = urllib.request.urlopen) -> Optional[dict]:
    try:
        answer = _call("GET", f"/_ilm/policy/{POLICY}", url, opener)
    except urllib.error.HTTPError as err:
        if err.code == 404:
            return None
        raise ElasticError(f"Elasticsearch answered {err.code} for the ILM policy {POLICY}")
    except (OSError, ValueError) as err:
        raise ElasticError(_unreachable(err))
    return ((answer or {}).get(POLICY) or {}).get("policy")


def put_policy(policy: dict, url: str = ES_URL, opener: Callable = urllib.request.urlopen) -> None:
    try:
        _call("PUT", f"/_ilm/policy/{POLICY}", url, opener, {"policy": policy})
    except (OSError, ValueError) as err:
        raise ElasticError(f"the ILM policy {POLICY} could not be written ({getattr(err, 'reason', err)})")


@dataclass
class Index:
    name: str
    created: float      # epoch seconds
    docs: int
    size: int           # bytes


def indices(url: str = ES_URL, opener: Callable = urllib.request.urlopen) -> List[Index]:
    try:
        rows = _call("GET", "/_cat/indices/logstash-*?format=json&bytes=b&h=index,creation.date,docs.count,store.size",
                     url, opener) or []
    except (OSError, ValueError) as err:
        raise ElasticError(_unreachable(err))
    return sorted((Index(r["index"], int(r.get("creation.date") or 0) / 1000, int(r.get("docs.count") or 0),
                         int(r.get("store.size") or 0)) for r in rows), key=lambda i: i.created)


def doomed(found: List[Index], value: str, now: float) -> List[Index]:
    """The indices ILM deletes right away once the retention is value."""
    if value in ("", "off"):
        return []
    limit = now - int(value) * 86400
    return [index for index in found if index.created < limit]


@dataclass
class Status:
    value: str          # TPOT_ES_RETENTION
    current: str        # what the policy does (value_of), "" if not in days
    state: str          # managed | foreign | missing | unreachable
    problem: str = ""


def status(value: str, url: str = ES_URL, opener: Callable = urllib.request.urlopen) -> Status:
    try:
        policy = get_policy(url, opener)
    except ElasticError as err:
        return Status(value, "", "unreachable", str(err))
    if policy is None:
        return Status(value, "", "missing")
    return Status(value, value_of(policy), "managed" if is_managed(policy) else "foreign")


@dataclass
class Plan:
    decision: Decision
    doomed: List[Index]
    reachable: bool


def plan(value: str, force: bool = False, url: str = ES_URL, opener: Callable = urllib.request.urlopen,
         now: Optional[float] = None) -> Plan:
    """What setting value does now: the decision and the indices it deletes."""
    try:
        policy = get_policy(url, opener)
    except ElasticError:
        return Plan(Decision("keep", None, "", False), [], False)
    decision = decide(value, policy, force)
    gone: List[Index] = []
    if decision.action in ("create", "update"):
        try:
            gone = doomed(indices(url, opener), value or DEFAULT, time.time() if now is None else now)
        except ElasticError:
            return Plan(decision, [], False)
    return Plan(decision, gone, True)


def apply(chosen: Plan, url: str = ES_URL, opener: Callable = urllib.request.urlopen) -> None:
    if not chosen.reachable:
        raise ElasticError("Elasticsearch is not reachable, T-Pot applies the retention on its next start")
    if chosen.decision.action == "foreign":
        raise ElasticError(f"the ILM policy {POLICY} was changed outside of T-Pot (retention: "
                           f"{chosen.decision.current or 'not in days'}); take it over or apply anyway (--force)")
    if chosen.decision.action in ("create", "update"):
        put_policy(chosen.decision.policy, url, opener)


def main(argv) -> int:
    if len(argv) == 2 and argv[0] == "value-of":
        try:
            with open(argv[1], encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return 1
        if not isinstance(data, dict) or ("policy" not in data and "phases" not in data):
            return 1
        print(value_of(data["policy"] if "policy" in data else data))
        return 0
    print("usage: python3 -m tpotctl.elastic value-of POLICY.json", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
