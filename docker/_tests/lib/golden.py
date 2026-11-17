#!/usr/bin/env python3
"""The golden Logstash documents: what T-Pot's Logstash pipeline makes of every fixture.

docker/_tests/tests/logstash_fixtures.sh renders each fixture of tpotctl/checks/fixtures with fixed
values (time, source address and port, token, the address of the host), feeds them through the
pipeline and hands its output here. The golden file keeps one line per fixture,
{"name": ..., "event": ...}, sorted by name, the event normalised (normalize): without the fields
that depend on the run or on the way into Logstash (the version, the host, the HTTP headers, the
sensor and the file it was read from), its tags sorted and the keys of a JSON object Logstash dumped
into a string sorted. It records the behaviour of the pipeline as it is, failures of a fixture
included, so a change of the pipeline that changes a document shows.

    golden.py write   --golden FILE --events FILE --expect FILE
    golden.py compare --golden FILE --events FILE --expect FILE

--events is the output of the pipeline (JSON lines), --expect the fixtures of the run, one line
"name<TAB>token<TAB>src_port" each. write records the documents, compare names every field that
differs from the golden file. Exit 0, or 1 with the problems.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.append(str(REPO))

from tpotctl.checks.inventory import LOGS  # noqa: E402  (standard library only)

# the run (version, host), the way in (HTTP headers, sensor) and the file read
VOLATILE = ("@version", "host", "headers", "t-pot_sensor", "t-pot_log_path")
# the files the honeypot inputs read (inventory.LOGS, under /data in Logstash); a * stays in its folder
SOURCE_PATHS = [re.compile(re.escape("/data/" + rel).replace(r"\*", "[^/]*")) for _, rel in LOGS.values()]
MISSING = object()


class MatchError(ValueError):
    """Events that belong to no fixture, or a second event of one; `lines` names each."""

    def __init__(self, lines: List[str]):
        super().__init__("; ".join(lines))
        self.lines = lines


def is_source_path(value: object) -> bool:
    """True for the path of a log file a file input of T-Pot reads, never for a path a honeypot logs."""
    return isinstance(value, str) and any(pattern.fullmatch(value) for pattern in SOURCE_PATHS)


def _sorted_dump(value):
    """A string that is a JSON object exactly as LogStash::Json.dump writes it (compact) with its keys
    sorted, since the dump keeps no stable order; every other value as it is, lists and maps walked."""
    if isinstance(value, dict):
        return {key: _sorted_dump(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sorted_dump(item) for item in value]
    if not isinstance(value, str) or not value.startswith("{"):
        return value
    try:
        parsed = json.loads(value)
    except ValueError:
        return value
    compact = {"separators": (",", ":"), "ensure_ascii": False}
    if not isinstance(parsed, dict) or json.dumps(parsed, **compact) != value:
        return value
    return json.dumps(parsed, sort_keys=True, **compact)


def normalize(event: dict) -> dict:
    """The event without the fields of the run and the way in, its tags sorted. The file input names
    the file in [path] only where the honeypot logs no path of its own: only that one goes. A string
    Logstash dumped from a map (Honeypots data) gets its keys sorted, the dump has no stable order."""
    out = {key: _sorted_dump(value) for key, value in event.items() if key not in VOLATILE}
    if is_source_path(out.get("path")):
        del out["path"]
    if isinstance(out.get("tags"), list):
        out["tags"] = sorted(out["tags"], key=str)
    return out


def owner(event: dict, expect: Iterable[Tuple[str, str, int]]) -> Optional[str]:
    """The fixture of an event: the one whose token it carries (of tokens that contain one another the
    longest), else the one whose source port it has. None when there is none or more than one."""
    expect = list(expect)
    text = json.dumps(event, sort_keys=True, ensure_ascii=False)
    found = [(name, token) for name, token, _ in expect if token and token in text]
    longest = {name for name, token in found
               if not any(token != other and token in other for _, other in found)}
    if longest:
        return longest.pop() if len(longest) == 1 else None
    port = event.get("src_port")
    ports = {name for name, _, src_port in expect if port is not None and port in (src_port, str(src_port))}
    return ports.pop() if len(ports) == 1 else None


def match(events: List[dict], expect: List[Tuple[str, str, int]]) -> Dict[str, dict]:
    """The event of each fixture by its token or source port. MatchError for an event of no fixture
    (or of more than one) and for a second event of a fixture; a fixture without event is left out."""
    found: Dict[str, dict] = {}
    problems = []
    for event in events:
        name = owner(event, expect)
        if name is None:
            problems.append("an event of no fixture (or of more than one): "
                            + json.dumps(event, sort_keys=True, ensure_ascii=False)[:500])
        elif name in found:
            problems.append(f"{name}: more than one event")
        else:
            found[name] = event
    if problems:
        raise MatchError(problems)
    return found


def _flatten(value: object, path: Tuple[str, ...] = ()) -> Dict[Tuple[str, ...], object]:
    if isinstance(value, dict) and value:
        flat: Dict[Tuple[str, ...], object] = {}
        for key, item in value.items():
            flat.update(_flatten(item, path + (str(key),)))
        return flat
    return {path: value}


def _show(value: object) -> str:
    return "(missing)" if value is MISSING else json.dumps(value, sort_keys=True, ensure_ascii=False)


def compare(expected: Dict[str, dict], actual: Dict[str, dict]) -> List[str]:
    """One line per field that differs ("name: field: golden != now"), per missing or new fixture.
    Values are compared as JSON, so 12 and 12.0 or "1" and 1 differ."""
    lines = []
    for name in sorted(set(expected) | set(actual)):
        if name not in actual:
            lines.append(f"{name}: no event")
            continue
        if name not in expected:
            lines.append(f"{name}: not in the golden file")
            continue
        want, got = _flatten(expected[name]), _flatten(actual[name])
        for field in sorted(set(want) | set(got)):
            old, new = want.get(field, MISSING), got.get(field, MISSING)
            if _show(old) != _show(new):
                lines.append(f"{name}: {'.'.join(field)}: {_show(old)} != {_show(new)}")
    return lines


def load(path: Path) -> Dict[str, dict]:
    """The golden documents by fixture name."""
    documents: Dict[str, dict] = {}
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            entry = json.loads(line)
            if entry["name"] in documents:
                raise ValueError(f"{path}:{number}: {entry['name']} a second time")
            documents[entry["name"]] = entry["event"]
    return documents


def dump(path: Path, documents: Dict[str, dict]) -> None:
    """Writes the documents sorted by name, keys sorted: the same documents give the same bytes."""
    lines = ['{"name": %s, "event": %s}\n' % (json.dumps(name, ensure_ascii=False),
                                              json.dumps(documents[name], sort_keys=True, ensure_ascii=False))
             for name in sorted(documents)]
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".new")
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        handle.writelines(lines)
    os.replace(temporary, path)


def read_events(path: Path) -> List[dict]:
    """The events of the output, none when Logstash wrote no file."""
    try:
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]
    except FileNotFoundError:
        return []


def read_expect(path: Path) -> List[Tuple[str, str, int]]:
    expect = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                name, token, src_port = line.rstrip("\n").split("\t")
                expect.append((name, token, int(src_port)))
    return expect


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Record or compare the golden Logstash documents of the fixtures.")
    parser.add_argument("action", choices=("write", "compare"))
    parser.add_argument("--golden", required=True, type=Path, help="the golden file (JSON lines)")
    parser.add_argument("--events", required=True, type=Path, help="the output of the pipeline (JSON lines)")
    parser.add_argument("--expect", required=True, type=Path, help="name<TAB>token<TAB>src_port of each fixture")
    args = parser.parse_args(argv)

    expect = read_expect(args.expect)
    try:
        found = match(read_events(args.events), expect)
    except MatchError as error:
        for line in error.lines:
            print(f"[FAIL] {line}")
        return 1
    actual = {name: normalize(event) for name, event in found.items()}
    if args.action == "write":
        missing = sorted(name for name, _, _ in expect if name not in actual)
        if missing:
            print("[FAIL] no event of " + ", ".join(missing) + f": {args.golden} stays as it is")
            return 1
        dump(args.golden, actual)
        print(f"{len(actual)} golden documents written to {args.golden}")
        return 0
    if not args.golden.is_file():
        print(f"[FAIL] no golden file {args.golden}: record it with write")
        return 1
    lines = compare(load(args.golden), actual)
    for line in lines:
        print(f"[DIFF] {line}")
    print(f"{len(actual)} documents, {len(lines)} difference(s) to {args.golden}")
    return 1 if lines else 0


if __name__ == "__main__":
    sys.exit(main())
