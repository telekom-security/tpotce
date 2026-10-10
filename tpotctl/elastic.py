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
from dataclasses import dataclass
from typing import Optional

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
    if policy is None:
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


def main(argv) -> int:
    if len(argv) == 2 and argv[0] == "value-of":
        try:
            with open(argv[1], encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return 1
        print(value_of(data.get("policy", data) if isinstance(data, dict) else None))
        return 0
    print("usage: python3 -m tpotctl.elastic value-of POLICY.json", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
