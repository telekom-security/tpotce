#!/usr/bin/env python3
"""Update Glutton's saved objects from the shipped Lens and search templates."""

import argparse
import copy
import json
from pathlib import Path
import uuid


ROOT = Path(__file__).resolve().parents[3]
EXPORT = ROOT / "docker/tpotinit/dist/etc/objects/kibana_export.ndjson"
FIELDS = (
    ("Transport", "transport.keyword"),
    ("Protocol Stack", "glutton.protocol_stack.keyword"),
    ("Scanner", "glutton.scanner.keyword"),
    ("TLS Server Name", "glutton.tls.serverName.keyword"),
    ("TLS JA4", "glutton.tls.ja4.keyword"),
    ("TLS JA3", "glutton.tls.ja3.keyword"),
    ("TLS Version", "glutton.tls.version.keyword"),
    ("TLS ALPN", "glutton.tls.alpn.keyword"),
)
COLUMNS = [
    "@timestamp", "src_ip", "src_port", "dest_port", "transport", "handler",
    "glutton.duration_ms", "glutton.frame_count", "glutton.end_reason",
    "glutton.scanner", "glutton.tls.ja4", "glutton.commands",
]


def build(objects):
    objects = copy.deepcopy(objects)
    metadata = [obj for obj in objects if "exportedCount" in obj]
    objects = [obj for obj in objects if "exportedCount" not in obj]
    by_id = {obj.get("id"): obj for obj in objects}
    dashboard = by_id["83b11b50-e850-11e8-97df-bbc3de28ece0"]
    template = by_id["cdd67f30-8ed7-471e-83b1-4a4ebdbe0c86"]

    def terms(identifier, title, field):
        obj = copy.deepcopy(template)
        obj["id"] = identifier
        obj["attributes"]["title"] = "Glutton " + title
        obj["attributes"]["description"] = "Structured Glutton sessions; diagnostic lines are not attacks."
        state = obj["attributes"]["state"]
        state["query"] = {"language": "kuery", "query": 'type:"Glutton" and event_kind:"session"'}
        for layer in state["datasourceStates"]["formBased"]["layers"].values():
            for column in layer["columns"].values():
                if column.get("operationType") == "terms":
                    column["sourceField"] = field
                    column["label"] = title
        for key in ("version", "sort", "created_at", "updated_at"):
            obj.pop(key, None)
        if identifier in by_id:
            objects[objects.index(by_id[identifier])] = obj
        else:
            objects.append(obj)
        by_id[identifier] = obj
        return obj

    terms("51383fd9-721b-4378-b2d5-8a9d7a3660f2", "Commands", "glutton.commands.keyword")
    terms("a001a350-e85b-11e8-97df-bbc3de28ece0", "Payload SHA256", "glutton.payload_sha256.keyword")
    terms("b35a86dc-43d3-4633-82c9-871d18ff5798", "Session End Reason", "glutton.end_reason.keyword")
    panels = json.loads(dashboard["attributes"]["panelsJSON"])
    references = dashboard["references"]
    for panel in panels:
        if panel["panelIndex"] == "31":
            panel["type"] = "lens"
    for ref in references:
        if ref["id"] == "a001a350-e85b-11e8-97df-bbc3de28ece0":
            ref["type"] = "lens"
    for index, (title, field) in enumerate(FIELDS):
        identifier = next((obj["id"] for obj in objects
                           if obj.get("type") == "lens"
                           and obj["attributes"]["title"] == "Glutton " + title),
                          str(uuid.uuid5(uuid.NAMESPACE_URL, "tpot:glutton:" + field)))
        terms(identifier, title, field)
        panel_index = "glutton-" + identifier
        name = "panel_" + panel_index
        panels = [panel for panel in panels if panel["panelIndex"] != panel_index]
        panels.append({
            "type": "lens", "panelIndex": panel_index, "panelRefName": name,
            "embeddableConfig": {"enhancements": {}},
            "gridData": {"x": (index % 2) * 24, "y": 83 + (index // 2) * 12,
                         "w": 24, "h": 12, "i": panel_index},
        })
        references[:] = [ref for ref in references if ref["id"] != identifier]
        references.append({"id": identifier, "type": "lens", "name": panel_index + ":" + name})

    search = by_id["385ea460-ad22-11e8-942c-a39712fa9ddf"]
    search["attributes"]["columns"] = COLUMNS
    for tab in search["attributes"].get("tabs", []):
        tab["attributes"]["columns"] = COLUMNS
    panel_index = "glutton-sessions"
    panels = [panel for panel in panels if panel["panelIndex"] != panel_index]
    panels.append({
        "type": "search", "panelIndex": panel_index, "panelRefName": "panel_glutton_sessions",
        "embeddableConfig": {"enhancements": {}},
        "gridData": {"x": 0, "y": 131, "w": 48, "h": 18, "i": panel_index},
    })
    references[:] = [ref for ref in references if ref["id"] != search["id"]]
    references.append({"id": search["id"], "type": "search",
                       "name": panel_index + ":panel_glutton_sessions"})
    dashboard["attributes"]["panelsJSON"] = json.dumps(panels, separators=(",", ":"))
    dashboard["attributes"]["description"] = (
        "Glutton TCP/UDP sessions, protocol stacks, commands, payload hashes, scanners and TLS "
        "fingerprints. Duration and frame count are shown in the session log. Historical "
        "slog records remain searchable; diagnostic.log is not ingested. Scanner attribution "
        "is heuristic, not verified identity."
    )
    for item in metadata:
        item["exportedCount"] = len(objects)
    return objects + metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    before = EXPORT.read_text(encoding="utf-8")
    objects = [json.loads(line) for line in before.splitlines()]
    updated = build(objects)
    if args.check:
        return 0 if objects == updated else 1
    # Keep untouched objects byte-for-byte, including their export metadata.
    original = {(obj.get("type"), obj.get("id")): (obj, line)
                for obj, line in zip(objects, before.splitlines())}
    lines = []
    for obj in updated:
        old, text = original.get((obj.get("type"), obj.get("id")), (None, None))
        lines.append(text if old == obj else json.dumps(obj, separators=(",", ":"), ensure_ascii=False))
    EXPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
